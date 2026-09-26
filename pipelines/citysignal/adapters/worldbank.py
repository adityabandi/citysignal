"""World Bank WDI: annual structural context, never a short-term activity signal."""
import math
import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, SourceManifest
from ..framework.fetch import FetchPlan
from ..framework.record import CanonicalRecord
from ..countries import COUNTRIES

INDICATORS = {
    "wb_gdp_growth": ("NY.GDP.MKTP.KD.ZG", "percent"),
    "wb_inflation": ("FP.CPI.TOTL.ZG", "percent"),
    "wb_unemployment": ("SL.UEM.TOTL.ZS", "percent"),
    "wb_population": ("SP.POP.TOTL", "persons"),
    "wb_gdp_per_capita": ("NY.GDP.PCAP.PP.KD", "international_dollars"),
    "wb_trade": ("NE.TRD.GNFS.ZS", "percent"),
}


class WorldBankAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="worldbank", publisher="World Bank — World Development Indicators",
        license="CC BY 4.0 (World Bank open data terms)", attribution="Source: World Bank, World Development Indicators",
        docs_url="https://datahelpdesk.worldbank.org/knowledgebase/topics/125589",
        cadence="annual", geo_level="nation", max_age_days=900, formats=("json",),
        revisions_allowed=True, notes="Annual GDP growth, consumer inflation, modelled unemployment, population, PPP-adjusted real GDP per person and trade openness for 28 major markets. Historical benchmarks, excluded from the activity reading.",
    )

    def discover(self, ctx):
        countries = ";".join(COUNTRIES)
        return [FetchPlan(
            url=f"https://api.worldbank.org/v2/country/{countries}/indicator/{code}?format=json&date=2015:2100&per_page=10000",
            fmt="json", label=metric, optional=True, meta={"metric_id": metric, "unit": unit},
        ) for metric, (code, unit) in INDICATORS.items()]

    def parse(self, payload, ctx):
        data = payload.json()
        if not isinstance(data, list) or len(data) != 2 or not isinstance(data[0], dict) or data[0].get("pages") != 1:
            raise AdapterFailure("Unexpected or paginated World Bank response")
        rows = []
        for item in data[1] or []:
            if item.get("value") is None:
                continue
            value = float(item["value"])
            geo = item["country"]["id"].lower()
            if geo not in COUNTRIES or not math.isfinite(value):
                raise AdapterFailure("Invalid World Bank country/value")
            rows.append({"geo": geo, "period": item["date"], "value": value})
        if not rows:
            raise AdapterFailure("No World Bank observations")
        return pd.DataFrame(rows)

    def normalize(self, frame, plan, ctx):
        for row in frame.itertuples():
            yield CanonicalRecord(metric_id=plan.meta["metric_id"], geo_id=row.geo,
                period=row.period, value=row.value, unit=plan.meta["unit"], source_id=self.manifest.source_id)
