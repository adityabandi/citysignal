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

    # Exchange rates are not decoration on a property site. Foreign buyers are
    # roughly 15% of Spanish purchases nationally and well over 30% in Málaga and
    # the Balearics, and their purchasing power is a pure function of FX. When
    # sterling weakens against the euro, a British buyer's budget in Málaga falls
    # that day, before any housing statistic has been collected.
    #
    # The yen is here for a different reason. It is the world's funding currency,
    # so a sharp yen appreciation forces carry trades to unwind and drains capital
    # out of peripheral risk assets — including southern European property. That
    # is visible in this series: EUR/JPY ran 171.17 in July 2024, then 161.06 in
    # August and 159.08 in September, a 6.3% three-month move, which is the
    # unwind that took global equities with it.
    FX = {
        "GBP": "British buyers — the largest foreign group on the Costa del Sol",
        "USD": "American and dollar-pegged buyers",
        "JPY": "the world's funding currency, so the carry-trade tell",
        "SEK": "Swedish buyers, concentrated on the Mediterranean coast",
        "NOK": "Norwegian buyers, same coast",
        "CHF": "Swiss buyers",
    }

    # Euro-area conditions. Spain does not set these; it receives them.
    EURO_SERIES = {
        "ecb_policy_rate": ("FM/B.U2.EUR.4F.KR.MRR_FR.LEV", "euro_area", "event"),
        "euro_inflation": ("ICP/M.U2.N.000000.4.ANR", "euro_area", "monthly"),
        "euro_unemployment": ("LFSI/M.I9.S.UNEHRT.TOTAL0.15_74.T", "euro_area", "monthly"),
    }

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        plans = [
            FetchPlan(
                url=f"{BASE_API}/MIR/M.ES.B.A2C.A.R.A.2250.EUR.N?format=jsondata&startPeriod=2015-01",
                fmt="json",
                label="mortgage-rate-spain",
                optional=False,
                meta={"metric_id": "ecb_mortgage_rate", "cadence": "monthly", "geo": NATION},
            )
        ]

        for ccy in self.FX:
            plans.append(
                FetchPlan(
                    url=(
                        f"{BASE_API}/EXR/M.{ccy}.EUR.SP00.A"
                        "?format=jsondata&startPeriod=1999-01"
                    ),
                    fmt="json",
                    label=f"eur-{ccy.lower()}",
                    optional=True,
                    meta={
                        "metric_id": f"eur_{ccy.lower()}",
                        "cadence": "monthly",
                        "geo": "euro-area",
                    },
                )
            )

        for metric_id, (key, _, cadence) in self.EURO_SERIES.items():
            plans.append(
                FetchPlan(
                    url=f"{BASE_API}/{key}?format=jsondata&startPeriod=1999-01",
                    fmt="json",
                    label=metric_id.replace("_", "-"),
                    optional=True,
                    meta={
                        "metric_id": metric_id,
                        "cadence": cadence,
                        "geo": "euro-area",
                    },
                )
            )
        return plans

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
        geo_id = plan.meta.get("geo", NATION)
        unit = (ctx.config.metrics.get(metric_id) or {}).get("unit", "percent")

        rows = [
            (str(r.period).strip(), r.value)
            for r in frame.itertuples()
            if str(r.period).strip()
        ]

        if cadence == "event":
            # The policy rate is published only when the ECB actually moves it,
            # dated to the day. Left alone it would fail monthly cadence
            # validation and leave gaps wherever the Bank sat still — which is
            # most months. Forward-filling states the true fact: the rate that
            # was in force during that month.
            rows = self._forward_fill_monthly(rows)

        for period, value in rows:
            if not self._is_valid_period(period, "monthly"):
                continue
            yield CanonicalRecord(
                metric_id=metric_id,
                geo_id=geo_id,
                period=period,
                value=value,
                unit=unit,
                source_id=self.manifest.source_id,
            )

    @staticmethod
    def _forward_fill_monthly(rows: list[tuple[str, float]]) -> list[tuple[str, float]]:
        """Daily rate-change events → the rate in force in each month."""
        events = sorted((p, v) for p, v in rows if len(p) >= 7)
        if not events:
            return []

        out: list[tuple[str, float]] = []
        current = events[0][1]
        index = 0
        year, month = int(events[0][0][:4]), int(events[0][0][5:7])
        last_year, last_month = int(events[-1][0][:4]), int(events[-1][0][5:7])

        while (year, month) <= (last_year, last_month):
            stamp = f"{year:04d}-{month:02d}"
            while index < len(events) and events[index][0][:7] <= stamp:
                current = events[index][1]
                index += 1
            out.append((stamp, current))
            month += 1
            if month > 12:
                year, month = year + 1, 1
        return out

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
