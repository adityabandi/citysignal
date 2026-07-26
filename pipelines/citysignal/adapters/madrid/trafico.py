"""Tráfico — historical traffic intensity sensor readings aggregated to monthly city-level mean.

datos.madrid.es publishes monthly traffic sensor measurements from thousands of
points across the city (dataset 208627-0, "Tráfico. Histórico de datos del tráfico
desde 2013"), running from January 2013 to the present, each month as a separate ZIP
archive. Each measurement contains the sensor id, time, and intensity in vehicles per hour.
The adapter aggregates intensity readings across all sensors to produce a monthly city-level
mean. This is a direct, real-time activity proxy: traffic volume tracks commuting and
commercial movement almost instantaneously, and unlike survey data or jobs announcements,
nobody can massage it after the fact.

The dataset is large (90-100 MB per month), so only the monthly mean is committed, never
the raw sensor dumps. The 2020 COVID lockdown (March-May) produces a ~60-80% collapse
that is instantly visible: lockdown started March 16, and April-May traffic fell dramatically.
If this collapse is not visible in the data, the wrong column or aggregation has been chosen.
"""

from __future__ import annotations

import csv
import io
import json
import re
import urllib.request
import zipfile
from typing import Iterable

import pandas as pd

from ...framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ...framework.fetch import FetchPlan, RawPayload
from ...framework.record import CanonicalRecord, municipality

# Traffic historical dataset: https://datos.madrid.es/dataset/208627-0-transporte-ptomedida-historico
# Fetch recent months only (large files, ~90-100 MB each)
HISTORY_MONTHS = 2  # 2 years: sufficient for derive engine (needs 24) and includes 2020 COVID collapse


class MadridTraficoAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="madrid_trafico",
        publisher="Ayuntamiento de Madrid",
        license="Reuse permitted (attribution)",
        attribution="Source: Ayuntamiento de Madrid, datos.madrid.es",
        docs_url="https://datos.madrid.es/portal/site/egob",
        cadence="monthly",
        geo_level="municipality",
        max_age_days=75,
        formats=("zip",),
        kind="official",
        redistribute=True,
        aggregates_across_plans=False,
        min_rows=1,
        notes=(
            "Traffic sensor intensity readings aggregated to a monthly city-level mean. "
            "Thousands of sensors across Madrid record vehicles per hour; this adapter "
            "computes the mean across all sensors and observations for each calendar month. "
            "This is a live activity measure: traffic volume tracks commuting and commercial "
            "activity in real time and cannot be gamed after the fact like survey data can. "
            "Each monthly file is ~90-100 MB; fetching is limited to recent months to manage bandwidth."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        """Discover monthly traffic datasets from CKAN API and generate fetch plans."""
        plans = []

        # Fetch dataset metadata from CKAN API to find actual resource URLs
        ckan_url = "https://datos.madrid.es/api/3/action/package_show?id=208627-0-transporte-ptomedida-historico"
        req = urllib.request.Request(ckan_url, headers={"User-Agent": "Mozilla/5.0 (compatible; CitySignal/1.0)"})

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read())
                pkg = data.get("result", {})
                resources = pkg.get("resources", [])
        except Exception as exc:
            raise AdapterFailure(f"Could not fetch traffic dataset metadata from CKAN: {exc}") from exc

        if not resources:
            raise AdapterFailure("Traffic dataset has no resources")

        # Each resource URL contains a month string like "02-2026" or "05-2020"
        # Extract the month and build fetch plans, limiting to most recent HISTORY_MONTHS
        month_urls: dict[str, str] = {}

        for res in resources:
            url = res.get("url", "")

            # Extract month from URL (format MM-YYYY in download URL)
            match = re.search(r"(\d{2})-(\d{4})", url)
            if match:
                month, year = match.groups()
                period = f"{year}-{month}"
                if period not in month_urls:  # Keep first occurrence
                    month_urls[period] = url

        if not month_urls:
            raise AdapterFailure("No month/year patterns found in traffic dataset resources")

        # Sort by period descending and take most recent HISTORY_MONTHS
        sorted_months = sorted(month_urls.items(), reverse=True)[:HISTORY_MONTHS]

        for period, url in sorted_months:
            if url:
                plans.append(
                    FetchPlan(
                        url=url,
                        fmt="zip",
                        label=f"trafico-{period}",
                        optional=True,
                        meta={"period": period},
                    )
                )

        return plans

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        """Extract and parse CSV from the monthly ZIP archive."""
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload.content))
        except zipfile.BadZipFile as exc:
            raise AdapterFailure(f"Traffic ZIP is not valid: {exc}") from exc

        # The ZIP contains a single CSV file with sensor measurements
        csv_files = [n for n in archive.namelist() if n.endswith('.csv')]
        if not csv_files:
            raise AdapterFailure(f"No CSV found in traffic ZIP (contents: {archive.namelist()[:5]})")

        csv_member = csv_files[0]
        raw_content = archive.read(csv_member)

        # Try common encodings for Spanish municipal data
        text = None
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = raw_content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue

        if text is None:
            raise AdapterFailure(f"Could not decode traffic CSV with any common encoding")

        # Parse the CSV. The format is: ;-delimited with comma decimals
        # Expected columns: id_sensor, fecha, hora, intensidad, ocupacion, carga, nivelServicio, periodo, estado
        try:
            reader = csv.DictReader(io.StringIO(text), delimiter=';')
            rows = list(reader)
            if not rows:
                raise AdapterFailure("Traffic CSV parsed to zero rows")
            return pd.DataFrame(rows)
        except Exception as exc:
            raise AdapterFailure(f"Could not parse traffic CSV: {exc}") from exc

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        """Aggregate sensor readings to monthly city-level mean intensity."""
        city = ctx.config.city("madrid")
        period = plan.meta["period"]

        # The intensity column contains vehicles/hour readings
        # Column names vary (intensidad, INTENSIDAD, Intensidad, etc.)
        intensity_col = None
        for col in frame.columns:
            if col.lower().strip() == "intensidad":
                intensity_col = col
                break

        if intensity_col is None:
            raise AdapterFailure(f"No 'intensidad' column found (got {list(frame.columns)})")

        # Convert intensity to numeric, handling Spanish number format (comma decimals)
        intensities = []
        for val in frame[intensity_col]:
            if val is None or val == "":
                continue
            # Handle both comma and period as decimal separator
            val_str = str(val).strip().replace(".", "").replace(",", ".")
            try:
                intensities.append(float(val_str))
            except ValueError:
                continue

        if not intensities:
            raise AdapterFailure(f"{plan.label}: no valid intensity readings found")

        # Compute monthly mean
        mean_intensity = sum(intensities) / len(intensities)

        return [
            CanonicalRecord(
                metric_id="traffic_intensity",
                geo_id=municipality(city.ine_mun),
                period=period,
                value=mean_intensity,
                unit="index",
                source_id=self.manifest.source_id,
            )
        ]
