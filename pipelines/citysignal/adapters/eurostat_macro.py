"""Comparable official country indicators. Each dataset fails independently."""
from urllib.parse import urlencode

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, SourceManifest
from ..framework.fetch import FetchPlan
from ..framework.jsonstat import observations
from ..framework.record import CanonicalRecord, period_cadence

COUNTRIES = {"es": "Spain", "pt": "Portugal", "fr": "France", "de": "Germany", "it": "Italy", "nl": "Netherlands"}

# Filter every dimension except country/time. Units are publisher units, not
# inferred from names. HICP uses the current ECOICOP 2 series introduced in 2026.
SERIES = {
    "macro_gdp": ("namq_10_gdp", {"na_item": "B1GQ", "unit": "CLV_PCH_PRE", "s_adj": "SCA"}, "quarterly", "percent"),
    "macro_unemployment": ("une_rt_m", {"age": "TOTAL", "sex": "T", "unit": "PC_ACT", "s_adj": "SA"}, "monthly", "percent"),
    "macro_inflation": ("prc_hicp_minr", {"coicop18": "TOTAL", "unit": "RCH_A"}, "monthly", "percent"),
    "macro_industry": ("sts_inpr_m", {"indic_bt": "PRD", "nace_r2": "B-D", "unit": "I21", "s_adj": "SCA"}, "monthly", "index"),
    "macro_retail": ("sts_trtu_m", {"indic_bt": "VOL_SLS", "nace_r2": "G47", "unit": "I21", "s_adj": "SCA"}, "monthly", "index"),
    "macro_sentiment": ("ei_bssi_m_r2", {"indic": "BS-ESI-I", "s_adj": "SA"}, "monthly", "index"),
    "macro_house_prices": ("prc_hpi_q", {"purchase": "TOTAL", "unit": "RCH_A"}, "quarterly", "percent"),
    "macro_employment": ("namq_10_pe", {"na_item": "EMP_DC", "unit": "PCH_PRE_PER", "s_adj": "SCA"}, "quarterly", "percent"),
}
BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"


class EurostatMacroAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="eurostat_macro", publisher="Eurostat / European Commission DG ECFIN",
        license="Eurostat reuse policy (attribution)", attribution="Source: Eurostat; ESI: DG ECFIN",
        docs_url="https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-introduction",
        cadence="monthly", geo_level="nation", max_age_days=100, formats=("json",),
        revisions_allowed=True, notes="GDP, jobs, prices, industry, retail, sentiment and housing for six European countries. Quarterly series use their own freshness limits in the country dashboard.",
    )

    def discover(self, ctx):
        return [FetchPlan(
            url=f"{BASE}/{dataset}?" + urlencode({
                "format": "JSON", "lang": "EN", "geo": [c.upper() for c in COUNTRIES],
                "sinceTimePeriod": "2015", **filters,
            }, doseq=True), fmt="json", optional=True, label=metric,
            meta={"metric_id": metric, "cadence": cadence, "unit": unit},
        ) for metric, (dataset, filters, cadence, unit) in SERIES.items()]

    def parse(self, payload, ctx):
        rows = list(observations(payload.json()))
        if not rows:
            raise AdapterFailure("No country observations in response")
        return pd.DataFrame(rows)

    def normalize(self, frame, plan, ctx):
        for row in frame.itertuples():
            geo = row.geo.lower()
            if geo not in COUNTRIES:
                raise AdapterFailure(f"Unexpected country {geo}")
            if period_cadence(row.time) != plan.meta["cadence"]:
                raise AdapterFailure(f"Unexpected period {row.time}")
            yield CanonicalRecord(
                metric_id=plan.meta["metric_id"], geo_id=geo, period=row.time,
                value=row.value, unit=plan.meta["unit"], source_id=self.manifest.source_id,
                quality_flag="suspect" if "b" in row.status else "estimated" if any(f in row.status for f in ("p", "e")) else "ok",
            )
