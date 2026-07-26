"""Calidad del aire — air quality NO2 aggregated to monthly city-level mean.

datos.madrid.es publishes daily air quality measurements from monitoring stations
across the city (dataset 201410-0, "Calidad del aire. Datos diarios desde 2001"),
running from 2001 to the present. Each record is one station-month, carrying day-by-day
readings (D01-D31) with validity flags (V01-V31). NO2 is identified by MAGNITUD code 8
and is overwhelmingly traffic-generated in urban areas, making it an independent,
hard-to-game check on traffic volumes and a genuine liveability measure.

The adapter aggregates daily NO2 measurements (only validity flag V = valid readings)
across all stations to produce a monthly city-level mean. The 2020 COVID lockdown produces
a clear collapse in NO2 roughly parallel to traffic (though with slight lag), and a strong
winter>summer seasonal pattern is visible. If these features are absent, the wrong magnitude
code or validity-flag handling was used.
"""

from __future__ import annotations

import csv
import io
import json
import re
import urllib.request
from collections import defaultdict
from typing import Iterable

import pandas as pd

from ...framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ...framework.fetch import FetchPlan, RawPayload
from ...framework.record import CanonicalRecord, municipality

# Air quality daily dataset: https://datos.madrid.es/dataset/201410-0-calidad-aire-diario
# Fetches recent CSV resources covering multiple years of daily data
RECENT_RESOURCES = 3  # Grab 3 recent CSV files covering ~3 years


class MadridAireAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="madrid_aire",
        publisher="Ayuntamiento de Madrid",
        license="Reuse permitted (attribution)",
        attribution="Source: Ayuntamiento de Madrid, datos.madrid.es",
        docs_url="https://datos.madrid.es/portal/site/egob",
        cadence="monthly",
        geo_level="municipality",
        max_age_days=75,
        formats=("csv",),
        kind="official",
        redistribute=True,
        aggregates_across_plans=False,
        min_rows=1,
        notes=(
            "Air quality NO2 (nitrogen dioxide) concentration aggregated to a monthly "
            "city-level mean from daily monitoring station readings (MAGNITUD=8, validity=V only). "
            "NO2 is overwhelmingly traffic-generated in urban areas, making it an independent, "
            "hard-to-game check on traffic volumes and a genuine liveability measure. "
            "Shows clear COVID lockdown collapse in 2020 and strong seasonal winter>summer pattern."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        """Discover air quality CSV datasets from CKAN API."""
        plans = []

        # Fetch dataset metadata from CKAN API
        ckan_url = "https://datos.madrid.es/api/3/action/package_show?id=201410-0-calidad-aire-diario"
        req = urllib.request.Request(ckan_url, headers={"User-Agent": "Mozilla/5.0 (compatible; CitySignal/1.0)"})

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read())
                pkg = data.get("result", {})
                resources = pkg.get("resources", [])
        except Exception as exc:
            raise AdapterFailure(f"Could not fetch air quality dataset metadata: {exc}") from exc

        if not resources:
            raise AdapterFailure("Air quality dataset has no resources")

        # Collect CSV resources
        csv_resources: list[tuple[int, str, str]] = []

        for res in resources:
            fmt = res.get("format", "").upper()
            if fmt != "CSV":
                continue

            url = res.get("url", "")
            name = res.get("name", "")

            # Extract resource index from name (201410-NN-calidad-aire-diario-csv)
            match = re.search(r"201410-(\d+)-", name)
            if match and url:
                idx = int(match.group(1))
                csv_resources.append((idx, name, url))

        if not csv_resources:
            raise AdapterFailure("No CSV resources found in air quality dataset")

        # Take the most recent RECENT_RESOURCES (highest indices)
        csv_resources.sort(key=lambda x: x[0], reverse=True)

        for idx, name, url in csv_resources[:RECENT_RESOURCES]:
            plans.append(
                FetchPlan(
                    url=url,
                    fmt="csv",
                    label=f"aire-resource-{idx}",
                    optional=True,
                    meta={"resource_idx": idx},
                )
            )

        return plans

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        """Parse CSV air quality data."""
        # Try common encodings
        text = None
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = payload.content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue

        if text is None:
            raise AdapterFailure("Could not decode air quality CSV with any encoding")

        try:
            reader = csv.DictReader(io.StringIO(text), delimiter=';')
            rows = list(reader)
            if not rows:
                raise AdapterFailure("Air quality CSV parsed to zero rows")
            return pd.DataFrame(rows)
        except Exception as exc:
            raise AdapterFailure(f"Could not parse air quality CSV: {exc}") from exc

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        """Aggregate NO2 daily readings to monthly city-level mean."""
        city = ctx.config.city("madrid")

        # Filter to Madrid municipality (MUNICIPIO=079, PROVINCIA=28)
        # and NO2 (MAGNITUD=8)
        madrid_no2 = frame[
            (frame.get("PROVINCIA", "").astype(str) == "28")
            & (frame.get("MUNICIPIO", "").astype(str) == "079")
            & (frame.get("MAGNITUD", "").astype(str) == "8")
        ]

        if madrid_no2.empty:
            raise AdapterFailure(f"{plan.label}: no NO2 data (MAGNITUD=8) for Madrid municipality")

        # Collect all valid daily readings
        # Day columns are D01-D31, validity columns are V01-V31
        monthly_readings: dict[str, list[float]] = defaultdict(list)

        for row in madrid_no2.itertuples(index=False):
            try:
                year = int(row.ANO)
                month = int(row.MES)
            except (ValueError, TypeError, AttributeError):
                continue

            if not (1 <= month <= 12 and 2000 <= year <= 2030):
                continue

            period = f"{year:04d}-{month:02d}"

            # Extract daily readings with validity flags
            for day in range(1, 32):
                day_col = f"D{day:02d}"
                val_col = f"V{day:02d}"

                # Get values safely
                try:
                    val_flag = str(getattr(row, val_col, "")).strip().upper()
                    val_str = str(getattr(row, day_col, "")).strip()
                except AttributeError:
                    continue

                # Only use valid readings (V = valid, N = not valid or missing)
                if val_flag != "V":
                    continue

                # Parse value (format: "00020" = 20 µg/m³)
                if val_str and val_str != "00000":
                    try:
                        val = float(val_str)
                        if val >= 0:
                            monthly_readings[period].append(val)
                    except ValueError:
                        continue

        if not monthly_readings:
            raise AdapterFailure(f"{plan.label}: no valid NO2 readings found (checked only validity=V)")

        # Compute monthly means
        out: list[CanonicalRecord] = []
        for period in sorted(monthly_readings.keys()):
            values = monthly_readings[period]
            if values:
                mean_no2 = sum(values) / len(values)
                out.append(
                    CanonicalRecord(
                        metric_id="no2_level",
                        geo_id=municipality(city.ine_mun),
                        period=period,
                        value=mean_no2,
                        unit="index",
                        source_id=self.manifest.source_id,
                    )
                )

        if not out:
            raise AdapterFailure(f"{plan.label}: no records after monthly aggregation")

        return out
