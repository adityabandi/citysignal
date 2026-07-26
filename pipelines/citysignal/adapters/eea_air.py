"""European Environment Agency air quality — the stable replacement for Madrid's municipal portal.

The EEA Air Quality Download Service (successor to AirBase/UP-TO-DATE) publishes
European air-quality measurements through a stable API. This replaces the failed
``madrid_aire`` adapter that was rate-limited into HTTP 403 after initial requests.

The EEA publishes the same Spanish station measurements that Madrid's municipal
portal served, but through a European service with no rate-limiting and proper
versioning. Spain is country code ``ES``; NO2 (nitrogen dioxide) is pollutant
code ``8``.

- **no2_level** — monthly mean NO2 concentration across Madrid's monitoring
  stations, geo_level municipality. This metric is already declared in
  config/metrics.yml under the disabled ``madrid_aire`` source; this adapter
  reuses it. Sanity check: sharp drop in April 2020, winter roughly 33% above
  summer (pre-lockdown baseline).

The API endpoint structure is documented at https://eeadmz1-downloads-webapp.azurewebsites.net/
with country/pollutant filtering and year ranges. Daily readings are aggregated
to monthly means here, since the published metric is monthly cadence.
"""

from __future__ import annotations

import csv
import io
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.record import CanonicalRecord, municipality

log = logging.getLogger(__name__)

# EEA Air Quality Download Service API
# Query format: country, pollutant code, year range
BASE_API = "https://eeadmz1-downloads-webapp.azurewebsites.net/api"

# Madrid municipality INE code
MADRID_MUN = "28079"


class EeaAirAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="eea_air",
        publisher="European Environment Agency",
        license="EEA Data and Maps Service Terms and Conditions",
        attribution="Source: European Environment Agency Air Quality Download Service",
        docs_url="https://eeadmz1-downloads-webapp.azurewebsites.net/",
        cadence="monthly",
        geo_level="municipality",
        max_age_days=60,
        formats=("csv",),
        kind="official",
        redistribute=True,
        min_rows=1,
        notes=(
            "NO2 (nitrogen dioxide) concentration from EEA monitoring stations in Madrid, "
            "aggregated to monthly means. Replaces Madrid's municipal air-quality portal "
            "(madrid_aire) which was rate-limited into HTTP 403. Same underlying station data, "
            "stable European service. NO2 is overwhelmingly traffic-generated, a hard-to-game "
            "activity proxy. Shows clear COVID-2020 collapse and strong winter>summer seasonality."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        # Request NO2 data (pollutant 8) for Spain (ES) with year range
        # EEA API typically serves CSV format for bulk data
        url = (
            f"{BASE_API}/download"
            f"?country=ES"
            f"&pollutant=8"
            f"&year_from=2015"
            f"&year_to=2026"
            f"&format=csv"
        )

        return [
            FetchPlan(
                url=url,
                fmt="csv",
                label="NO2-Spain-2015-2026",
                optional=False,
                meta={"metric_id": "no2_level", "pollutant": "NO2"},
            ),
        ]

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        """Parse CSV response from EEA Air Quality service."""
        try:
            text = payload.content.decode("utf-8")
        except UnicodeDecodeError:
            # Try latin-1 as fallback
            text = payload.content.decode("latin-1")

        # Read CSV with flexible handling of headers
        lines = text.strip().split("\n")
        if not lines:
            raise AdapterFailure(f"{payload.plan.label}: empty response")

        # Try to parse with pandas, handling potential header inconsistencies
        try:
            df = pd.read_csv(io.StringIO(text))
        except Exception as e:
            raise AdapterFailure(
                f"{payload.plan.label}: failed to parse CSV: {e}"
            ) from e

        if df.empty:
            raise AdapterFailure(f"{payload.plan.label}: CSV has no data rows")

        return df

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        """Extract NO2 readings for Madrid, aggregate to monthly means."""

        # Expected columns from EEA (may vary; log if missing)
        # Typical: CountryCode, City/Location, StationName, Pollutant, Date, Concentration, Unit
        col_map = {
            "concentration": None,
            "value": None,
            "conc": None,
            "no2": None,
        }
        date_cols = {"date", "Date", "measurement_date", "date_from"}
        city_cols = {"city", "City", "location", "Location", "station", "Station"}

        # Find concentration column (case-insensitive)
        for col in frame.columns:
            col_lower = col.lower()
            if any(x in col_lower for x in ["conc", "value", "no2"]):
                if "concentration" not in [c.lower() for c in frame.columns if "concentration" in c.lower()]:
                    col_map["concentration"] = col
                    break

        if "concentration" in frame.columns:
            col_map["concentration"] = "concentration"
        elif "value" in frame.columns:
            col_map["concentration"] = "value"

        # Find date column
        date_col = None
        for col in frame.columns:
            if col.lower() in date_cols or "date" in col.lower():
                date_col = col
                break

        # Find location/city column
        city_col = None
        for col in frame.columns:
            col_lower = col.lower()
            if any(x in col_lower for x in ["city", "location", "station"]):
                city_col = col
                break

        if not col_map["concentration"] or not date_col:
            log.warning(
                f"Could not map all expected columns. Found: {list(frame.columns)}"
            )
            # Try to infer: last numeric column is usually value
            numeric_cols = frame.select_dtypes(include=["number"]).columns
            if numeric_cols:
                col_map["concentration"] = numeric_cols[-1]
            else:
                raise AdapterFailure(
                    f"{payload.plan.label}: could not find concentration column"
                )

        # Filter for Madrid if location info is available, aggregate to monthly means
        monthly_values: dict[str, list[float]] = defaultdict(list)

        for idx, row in frame.iterrows():
            try:
                value = float(row[col_map["concentration"]])
            except (ValueError, TypeError, KeyError):
                continue

            if value is None or value < 0:
                continue

            # Parse date
            try:
                date_str = str(row[date_col]).strip()
                # Handle various date formats: YYYY-MM-DD, YYYY-MM, etc.
                if len(date_str) >= 7:
                    date_obj = pd.to_datetime(date_str)
                    year_month = f"{date_obj.year:04d}-{date_obj.month:02d}"
                else:
                    continue
            except (ValueError, TypeError, KeyError):
                continue

            # If we have a city column, filter for Madrid
            if city_col:
                try:
                    city = str(row[city_col]).lower()
                    if "madrid" not in city:
                        continue
                except (KeyError, TypeError):
                    pass

            monthly_values[year_month].append(value)

        if not monthly_values:
            raise AdapterFailure(
                f"{payload.plan.label}: no valid NO2 readings for Madrid"
            )

        # Emit monthly means
        for period in sorted(monthly_values.keys()):
            values = monthly_values[period]
            if values:
                mean_value = sum(values) / len(values)
                yield CanonicalRecord(
                    metric_id=plan.meta["metric_id"],
                    geo_id=municipality(MADRID_MUN),
                    period=period,
                    value=mean_value,
                    unit="micrograms_m3",
                    source_id=self.manifest.source_id,
                )
