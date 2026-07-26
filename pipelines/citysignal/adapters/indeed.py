"""Indeed Hiring Lab Job Postings Index — Spain series, daily → monthly.

Indeed Hiring Lab publishes the Job Postings Index as free CSVs on GitHub,
refreshed weekly, covering multiple countries including Spain (ES).

Data format:
- Daily observations
- % change vs 2020-02-01 baseline (7-day trailing average)
- Forward-looking: hiring intent leads actual employment by 6-12 weeks

This adapter fetches the raw CSV from GitHub, filters to Spain, aggregates
daily observations to monthly (taking the last day of each month), and drops
the incomplete current month. The series should crater in spring 2020 (roughly
-40 to -60%) and recover to positive territory by 2022.

Reference:
- GitHub: https://github.com/hiring-lab/job_postings_tracker
- Series: Job Postings Index (% change from 2020-02-01)
"""

from __future__ import annotations

import io
import logging
from typing import Iterable

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.record import CanonicalRecord, NATION

log = logging.getLogger(__name__)

# Indeed Hiring Lab GitHub repository with Job Postings Index CSVs
# Spain (ES) data is in ES/aggregate_job_postings_ES.csv
# Format: Date,Index,Country_Code (% change vs 2020-02-01, 7-day trailing average)
GITHUB_REPO = "https://raw.githubusercontent.com/hiring-lab/job_postings_tracker/master"
CSV_URL = f"{GITHUB_REPO}/ES/aggregate_job_postings_ES.csv"

# Alternative URL patterns
ALT_URLS = [
    f"{GITHUB_REPO}/ES/aggregate_job_postings_ES.csv",
]


class IndeedAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="indeed",
        publisher="Indeed Hiring Lab",
        license="Free for public use with attribution",
        attribution="Source: Indeed Hiring Lab",
        docs_url="https://www.hiringlab.org/research/jobs-postings-tracker/",
        cadence="monthly",
        geo_level="nation",
        max_age_days=14,
        formats=("csv",),
        kind="research",
        redistribute=True,
        aggregates_across_plans=True,
        min_rows=100,
        notes=(
            "Indeed Hiring Lab's Job Postings Index for Spain (ES). Daily % change "
            "vs 2020-02-01 baseline, 7-day trailing average. Aggregated to monthly "
            "(last day of month). Drops incomplete current month. The series should "
            "show ~-50% drop in spring 2020 and recovery to positive by 2022."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        plans: list[FetchPlan] = [
            FetchPlan(
                url=CSV_URL,
                fmt="csv",
                label="indeed-trends",
                optional=False,
                meta={"source": "main"},
            )
        ]

        # Add alternatives as optional fallbacks
        for alt_url in ALT_URLS:
            plans.append(
                FetchPlan(
                    url=alt_url,
                    fmt="csv",
                    label="indeed-trends-alt",
                    optional=True,
                    meta={"source": "fallback"},
                )
            )

        return plans

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        text = payload.content.decode("utf-8")
        frame = pd.read_csv(io.StringIO(text), dtype=str)
        if frame.empty:
            raise AdapterFailure(f"{payload.plan.label}: parsed zero rows")
        return frame

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        """Extract Spain job postings index from Indeed CSV."""
        # Normalize column names
        frame.columns = [str(c).strip().lower() for c in frame.columns]

        # Identify key columns
        # Expected: date, country/country_code, value/job_postings_index, etc.
        date_col = None
        country_col = None
        value_col = None

        for col in frame.columns:
            if not date_col and any(x in col for x in ["date", "fecha"]):
                date_col = col
            elif not country_col and any(x in col for x in ["country", "country_code", "region"]):
                country_col = col
            elif not value_col and any(x in col for x in ["value", "index", "job_postings"]):
                value_col = col

        if not (date_col and value_col):
            # Try to infer from first few rows
            log.warning("indeed: could not identify columns. Will infer from data.")
            # Assume first col is date, second is country, rest are values
            if len(frame.columns) >= 2:
                date_col = frame.columns[0]
                country_col = frame.columns[1] if len(frame.columns) > 2 else None
                value_col = frame.columns[2] if len(frame.columns) > 2 else frame.columns[1]

        if not (date_col and value_col):
            raise AdapterFailure(f"{plan.label}: could not identify date and value columns")

        out: list[CanonicalRecord] = []
        by_month: dict[str, list[float]] = {}

        for row in frame.itertuples(index=False):
            try:
                # Parse date
                date_str = str(getattr(row, date_col, "")).strip()
                period = self._parse_date(date_str)
                if not period:
                    continue

                # Check country if present
                if country_col:
                    country_str = str(getattr(row, country_col, "")).strip().upper()
                    if country_str not in ("ES", "ESP", "SPAIN", "ESPAÑA"):
                        continue

                # Parse value
                value_str = str(getattr(row, value_col, "")).strip()
                value = self._parse_number(value_str)
                if value is None:
                    continue

                # Aggregate to month (collect all daily values for each month)
                # We'll take the last day of the month later
                if period not in by_month:
                    by_month[period] = []
                by_month[period].append(value)

            except Exception as exc:  # noqa: BLE001
                log.debug("indeed row parse error: %s", exc)
                continue

        # Finalize: take the last value of each month (7-day trailing average
        # means we want the most recent observation in each month)
        for period, values in sorted(by_month.items()):
            if values:
                # Use the last value (most recent in month)
                value = values[-1]
                out.append(
                    CanonicalRecord(
                        metric_id="job_postings_index",
                        geo_id=NATION,
                        period=period,
                        value=value,
                        unit="index",
                        source_id=self.manifest.source_id,
                    )
                )

        if not out:
            raise AdapterFailure(f"{plan.label}: produced zero records for Spain")

        return out

    def finalize(
        self, records: list[CanonicalRecord], ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        """Drop the incomplete current month."""
        if not records:
            return records

        # Sort by period
        sorted_records = sorted(records, key=lambda r: r.period)

        # Remove the latest month if today is still in the first half of the month
        # (rough heuristic: if we have data, but the month's first 15 days may not be complete)
        from datetime import date
        from ..framework.record import period_end

        today = date.today()
        if sorted_records:
            latest_period = sorted_records[-1].period
            period_last_day = period_end(latest_period)

            # If the period's last day is in the future (incomplete month), drop it
            if period_last_day >= today:
                log.info("indeed: dropping incomplete month %s", latest_period)
                return sorted_records[:-1]

        return sorted_records

    @staticmethod
    def _parse_date(value: str) -> str | None:
        """Parse date string to YYYY-MM format."""
        import re
        value = value.strip()

        # Try YYYY-MM-DD format
        match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", value)
        if match:
            return f"{match.group(1)}-{match.group(2)}"

        # Try YYYY/MM/DD format
        match = re.match(r"^(\d{4})/(\d{2})/(\d{2})$", value)
        if match:
            return f"{match.group(1)}-{match.group(2)}"

        # Try DD/MM/YYYY format
        match = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", value)
        if match:
            return f"{match.group(3)}-{match.group(2)}"

        # Try YYYY-MM format
        match = re.match(r"^(\d{4})-(\d{2})$", value)
        if match:
            return f"{match.group(1)}-{match.group(2)}"

        return None

    @staticmethod
    def _parse_number(value: str) -> float | None:
        """Parse a number value."""
        if not value:
            return None
        value = value.strip()
        if value.lower() in ("nan", "null", "none", "_", "-", ""):
            return None
        try:
            return float(value)
        except ValueError:
            return None
