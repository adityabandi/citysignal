"""Physical activity proxies from Red Electrica's public REData API."""
import math
from datetime import timedelta

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, SourceManifest
from ..framework.fetch import FetchPlan
from ..framework.record import CanonicalRecord, utc_today, period_end


class ReeAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="ree", publisher="Red Eléctrica", license="Red Eléctrica legal notice; commercial redistribution requires review",
        attribution="Source: Red Eléctrica, REData", docs_url="https://www.ree.es/en/datos/apidata",
        cadence="daily", geo_level="nation", max_age_days=10, formats=("json",),
        revisions_allowed=True, notes="National daily electricity demand (MWh) and mainland corrected large-user electricity index (>450 kW). Electricity is a proxy, affected by weather, efficiency and sector mix.",
    )

    def discover(self, ctx):
        yesterday = utc_today() - timedelta(days=1)
        plans = []
        for year in range(2019, yesterday.year + 1):
            end = min(f"{year}-12-31", yesterday.isoformat())
            query = f"start_date={year}-01-01T00:00&end_date={end}T23:59"
            plans.append(FetchPlan(
                url=f"https://apidatos.ree.es/en/datos/demanda/evolucion?{query}&time_trunc=day",
                fmt="json", label=f"national-demand-{year}", optional=True,
                meta={"metric_id": "electricity_demand", "series_id": "10297", "geo": "es", "unit": "mwh", "daily": True},
            ))
            plans.append(FetchPlan(
                url=f"https://apidatos.ree.es/en/datos/demanda/ire-general?{query}&time_trunc=month&geo_trunc=electric_system&geo_limit=peninsular&geo_ids=8741",
                fmt="json", label=f"mainland-large-users-{year}", optional=True,
                meta={"metric_id": "electricity_large_users", "series_id": "1599", "geo": "grid-es-mainland", "unit": "index", "daily": False},
            ))
        return plans

    def parse(self, payload, ctx):
        data = payload.json()
        target = payload.plan.meta["series_id"]
        matches = [s for s in data.get("included", []) if s.get("id") == target]
        if len(matches) != 1:
            raise AdapterFailure(f"Expected exactly one REData series {target}")
        rows = matches[0]["attributes"].get("values", [])
        if not rows:
            raise AdapterFailure("Empty REData series")
        return pd.DataFrame(rows)

    def normalize(self, frame, plan, ctx):
        today = utc_today()
        for row in frame.itertuples():
            # Keep local reporting date: conversion to UTC would move midnight
            # observations into the previous day during both CET and CEST.
            period = str(row.datetime)[:10 if plan.meta["daily"] else 7]
            if period_end(period) >= today:
                continue
            value = float(row.value)
            if not math.isfinite(value) or value < 0:
                raise AdapterFailure("Invalid electricity observation")
            yield CanonicalRecord(metric_id=plan.meta["metric_id"], geo_id=plan.meta["geo"],
                period=period, value=value, unit=plan.meta["unit"], source_id=self.manifest.source_id)
