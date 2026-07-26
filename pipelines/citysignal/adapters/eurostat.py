"""Eurostat — the stable, versioned pan-European housing and labour data.

Eurostat's JSON-stat API returns observations in a flat indexing scheme with
dimensions defined separately. The key challenge is getting the mapping right:
``value`` is a position→number map, and ``dimension.time.category.index``
maps period labels to those same positions — we must decode the index rather
than assume ordering.

Collect for geo=ES (Spain) unless otherwise noted:

- **hicp_rents** — COICOP ``CP041`` (actual rentals for housing), monthly.
  The tenant-cost series and a direct cross-check on our own rent data.
  Verified: 100.2 in 2015-01, 114.78 in 2025-12.

- **hicp_maintenance** — COICOP ``CP043`` (maintenance and repair), monthly.
  Renovation spending moves before transactions do.

The JSON-stat format published by Eurostat carries observations in a compressed
index space to save bandwidth. Decoding it correctly is essential: naive
iteration over the keys in ``value`` will produce wildly wrong results if any
period or geography has a gap.
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

BASE_API = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"


class EurostatAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="eurostat",
        publisher="Eurostat (European Commission)",
        license="CC BY 4.0",
        attribution="Source: Eurostat",
        docs_url="https://ec.europa.eu/eurostat/web/json-and-unicode-web-services/getting-started/rest-request",
        cadence="monthly",
        geo_level="nation",
        max_age_days=60,
        formats=("json",),
        kind="official",
        redistribute=True,
        min_rows=1,
        notes=(
            "Monthly housing-cost indicators for Spain: actual rents (COICOP CP041) "
            "and maintenance/repair costs (COICOP CP043), both from the Harmonised "
            "Index of Consumer Prices. A direct cross-check on rental-market movement."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        def plan(dataset: str, coicop: str, metric_id: str, label: str) -> FetchPlan:
            url = (
                f"{BASE_API}/{dataset}"
                f"?format=JSON&lang=EN&geo=ES&coicop={coicop}&unit=I15&sinceTimePeriod=2015-01"
            )
            return FetchPlan(
                url=url,
                fmt="json",
                label=label,
                optional=False,
                meta={"metric_id": metric_id, "dataset": dataset},
            )

        return [
            plan("prc_hicp_midx", "CP041", "hicp_rents", "HICP rents (CP041)"),
            plan("prc_hicp_midx", "CP043", "hicp_maintenance", "HICP maintenance (CP043)"),
        ]

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        try:
            data = payload.json()
        except json.JSONDecodeError as e:
            raise AdapterFailure(f"{payload.plan.label}: invalid JSON response") from e

        # Navigate the JSON-stat structure
        if "value" not in data or "dimension" not in data:
            raise AdapterFailure(
                f"{payload.plan.label}: missing 'value' or 'dimension' in response"
            )

        # Get the time dimension and its index mapping
        dimension = data.get("dimension", {})
        time_dim = dimension.get("time", {})
        time_category = time_dim.get("category", {})
        time_index = time_category.get("index", {})

        if not time_index:
            raise AdapterFailure(
                f"{payload.plan.label}: time dimension index not found"
            )

        # Build period→value map using the index
        values = data.get("value", {})
        records = []

        for period_label, time_position in time_index.items():
            # time_position is the index where this period's values are located
            if time_position is None:
                continue

            value_idx = str(time_position)
            if value_idx not in values:
                continue

            raw_value = values[value_idx]
            if raw_value is None:
                continue

            try:
                value = float(raw_value)
            except (ValueError, TypeError):
                continue

            records.append({
                "period": period_label,
                "value": value,
                "status": "ok"
            })

        if not records:
            raise AdapterFailure(f"{payload.plan.label}: no valid observations extracted")

        return pd.DataFrame(records)

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        metric_id = plan.meta["metric_id"]

        # Eurostat periods are YYYY-MM format, matching our monthly cadence
        for row in frame.itertuples():
            period = str(row.period).strip()
            value = row.value

            # Validate period format
            if not self._is_valid_period(period):
                continue

            yield CanonicalRecord(
                metric_id=metric_id,
                geo_id=NATION,
                period=period,
                value=value,
                unit="index",
                source_id=self.manifest.source_id,
            )

    @staticmethod
    def _is_valid_period(period: str) -> bool:
        """Check if period is in YYYY-MM format."""
        parts = period.split("-")
        if len(parts) != 2:
            return False
        try:
            year = int(parts[0])
            month = int(parts[1])
            return 1900 <= year <= 2100 and 1 <= month <= 12
        except ValueError:
            return False
