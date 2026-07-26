"""ECB Data Portal — the policy-rate side of European credit markets.

The ECB's SDMX-JSON API returns time-series data with observations in a
position→value map, and dimension metadata separate. The key is matching
positions to the time dimension's values array, which is in the same order.

Collect for Spain (country code ES) unless noted:

- **ecb_mortgage_rate** — interest rate on new household loans for house
  purchase, Spain, monthly. Series key ``MIR/M.ES.B.A2C.A.R.A.2250.EUR.N``.
  We already have a Banco de España rate; the ECB's own is a genuine
  cross-check and this API is far more reliable. Verified: roughly 1.5% in
  early 2022 rising above 3.5% during 2023 (the tightening cycle).

- **ecb_residential_property_price** — residential property prices for Spain,
  quarterly, if available cleanly from dataset RPP.

History stretches back to 2015 or earlier. Set startPeriod early in the query
to capture full available history.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.record import CanonicalRecord, NATION

log = logging.getLogger(__name__)

BASE_API = "https://data-api.ecb.europa.eu/service/data"


class EcbAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="ecb",
        publisher="European Central Bank",
        license="ECB copyright, data available under CC BY 4.0",
        attribution="Source: ECB Data Portal",
        docs_url="https://www.ecb.europa.eu/stats/ecb_statistics/info/services/html/index.en.html",
        cadence="monthly",
        geo_level="nation",
        max_age_days=60,
        formats=("json",),
        kind="official",
        redistribute=True,
        min_rows=1,
        notes=(
            "Interest rates on new household loans for house purchase (Spain), "
            "monthly from the ECB's Monetary Interest Rates dataset. "
            "A reliable, versioned cross-check on Spanish mortgage-market conditions."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        return [
            FetchPlan(
                url=f"{BASE_API}/MIR/M.ES.B.A2C.A.R.A.2250.EUR.N?format=jsondata&startPeriod=2015-01",
                fmt="json",
                label="mortgage-rate-spain",
                optional=False,
                meta={"metric_id": "ecb_mortgage_rate", "cadence": "monthly"},
            ),
        ]

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        try:
            data = payload.json()
        except json.JSONDecodeError as e:
            raise AdapterFailure(f"{payload.plan.label}: invalid JSON response") from e

        # Navigate the SDMX-JSON structure
        datasets = data.get("dataSets", [])
        if not datasets:
            raise AdapterFailure(f"{payload.plan.label}: no dataSets in response")

        dataset = datasets[0]
        series_dict = dataset.get("series", {})
        if not series_dict:
            raise AdapterFailure(f"{payload.plan.label}: no series in dataset")

        # All series in this response should have the same observations structure
        # Just take the first one (there should only be one for this query)
        first_series_key = next(iter(series_dict.keys()), None)
        if first_series_key is None:
            raise AdapterFailure(f"{payload.plan.label}: series dict is empty")

        series = series_dict[first_series_key]
        observations = series.get("observations", {})
        if not observations:
            raise AdapterFailure(f"{payload.plan.label}: no observations in series")

        # Get time dimension from structure
        structure = data.get("structure", {})
        dimensions = structure.get("dimensions", {})
        obs_dims = dimensions.get("observation", [])
        if not obs_dims:
            raise AdapterFailure(f"{payload.plan.label}: no observation dimensions")

        time_dim = obs_dims[0]  # Time is the first observation dimension
        time_values_list = time_dim.get("values", [])
        if not time_values_list:
            raise AdapterFailure(f"{payload.plan.label}: no time values in dimension")

        # Build period→value map using position indices
        records = []
        for pos_str, obs_array in observations.items():
            try:
                position = int(pos_str)
            except ValueError:
                continue

            # obs_array is [value, ...]; first element is the actual value
            if not obs_array or obs_array[0] is None:
                continue

            try:
                value = float(obs_array[0])
            except (ValueError, TypeError):
                continue

            # Get the period label from the time values array
            if position >= len(time_values_list):
                log.warning(f"Position {position} exceeds time values length")
                continue

            # SDMX-JSON time values are objects, not strings: each is
            # {"id": "2015-01", "name": "2015-01", ...}. Taking str() of the dict
            # yielded a period like "{'id': '2015-01', ...}", which then failed
            # cadence validation and was dropped — a silent zero-record fetch.
            period_label = time_values_list[position]
            if isinstance(period_label, dict):
                period_label = period_label.get("id") or period_label.get("name")
            period = str(period_label).strip()

            records.append({
                "period": period,
                "value": value,
            })

        if not records:
            raise AdapterFailure(f"{payload.plan.label}: no valid observations extracted")

        return pd.DataFrame(records)

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        metric_id = plan.meta["metric_id"]
        cadence = plan.meta["cadence"]

        for row in frame.itertuples():
            period = str(row.period).strip()
            value = row.value

            # Validate period format (YYYY-MM for monthly)
            if not self._is_valid_period(period, cadence):
                continue

            yield CanonicalRecord(
                metric_id=metric_id,
                geo_id=NATION,
                period=period,
                value=value,
                unit="percent",
                source_id=self.manifest.source_id,
            )

    @staticmethod
    def _is_valid_period(period: str, cadence: str) -> bool:
        """Check if period matches the expected cadence format."""
        if cadence == "monthly":
            parts = period.split("-")
            if len(parts) != 2:
                return False
            try:
                year = int(parts[0])
                month = int(parts[1])
                return 1900 <= year <= 2100 and 1 <= month <= 12
            except ValueError:
                return False
        return True
