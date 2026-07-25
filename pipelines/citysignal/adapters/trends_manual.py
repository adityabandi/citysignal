"""Google Trends without the API: exported baskets, read from the repository.

The official Trends API is alpha and application-gated. The unofficial endpoints
the scraping libraries use are not a viable substitute for a scheduled build —
`/trends/api/explore` returns HTTP 429 on a first request from a clean address,
and a shared CI runner fares worse. Building the weekly pipeline on that would
mean a public site whose search layer silently stops updating.

So the search layer takes the honest route. A basket is exported from
trends.google.com — which is allowed, reproducible and costs nothing — and lands
as a CSV in `data/manual/trends/`. This adapter reads whatever is there and marks
the series stale when nobody has refreshed it. No scraping in the weekly job, no
credentials, no silent failure.

Two properties of Trends data shape everything below.

**The scale is per-request.** Trends rescales 0-100 for each query, so two
separately exported files share no scale and can never be combined. Terms
exported *together* do share one — which is why a basket is a single file with
one column per term, and why the ratios between those columns are the honest
unit. A level can fall because Spain searched less in total; the ratio of rooms
to flats within one export cannot.

**Absence is reported as zero.** A term with too little volume comes back as 0,
not as missing. Left alone that reads as "nobody searched this", which is a much
stronger claim than the data supports. Any series whose months are mostly zeros
is therefore dropped rather than published — which is why the smaller cities
carry fewer search metrics than Madrid, and should.
"""

from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.record import CanonicalRecord

# data/manual/trends/<basket>__<geo_id>.csv
FILENAME = re.compile(r"^(?P<basket>[a-z_]+)__(?P<geo>[a-z]+-?\d*|es)\.csv$")


class TrendsManualAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="trends_manual",
        publisher="Google Trends (manual export)",
        license="Google Trends terms — exported by hand, not scraped",
        attribution="Source: Google Trends, exported manually",
        docs_url="https://trends.google.com/trends/explore",
        cadence="monthly",
        geo_level="municipality",
        max_age_days=120,
        formats=("csv",),
        kind="commercial",
        redistribute=False,
        revisions_allowed=True,
        notes=(
            "Hand-exported baskets. Trends rescales 0-100 per request, so each file "
            "is its own series; only ratios within a file are comparable over time."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        directory = ctx.data_dir / "manual" / "trends"
        if not directory.exists():
            raise AdapterFailure(
                f"{directory} does not exist — see its README for the export workflow"
            )

        plans: list[FetchPlan] = []
        for path in sorted(directory.glob("*.csv")):
            match = FILENAME.match(path.name)
            if not match:
                continue
            plans.append(
                FetchPlan(
                    url=f"file://{path.resolve()}",
                    fmt="csv",
                    label=path.name,
                    optional=True,
                    meta={"path": str(path), "basket": match["basket"], "geo_id": match["geo"]},
                )
            )

        if not plans:
            raise AdapterFailure(
                f"no exports found in {directory} — see the README there for how to add one"
            )
        return plans

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        frame = pd.read_csv(payload.plan.meta["path"])
        if "period" not in frame.columns:
            raise AdapterFailure(f"{payload.plan.label}: first column must be 'period'")
        return frame

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        config = ctx.config.baskets.get("trends_manual")
        if not config:
            raise AdapterFailure("config/baskets/trends_manual.yml is missing")

        spec = config["baskets"].get(plan.meta["basket"])
        if spec is None:
            raise AdapterFailure(f"{plan.label}: no basket named {plan.meta['basket']!r}")

        geo_id = plan.meta["geo_id"]
        min_coverage = float(config.get("min_coverage", 0.7))
        exported = date.fromtimestamp(Path(plan.meta["path"]).stat().st_mtime).isoformat()

        for entry in spec["metrics"]:
            metric_id = entry["metric"]
            meta = ctx.config.metrics.get(metric_id)
            if meta is None:
                raise AdapterFailure(f"{plan.label}: {metric_id!r} is not in config/metrics.yml")

            if "column" in entry:
                values = pd.to_numeric(frame[entry["column"]], errors="coerce")
                coverage_source = values
            else:
                numerator = pd.to_numeric(frame[entry["ratio"][0]], errors="coerce")
                denominator = pd.to_numeric(frame[entry["ratio"][1]], errors="coerce")
                # A ratio is only as trustworthy as its scarcer term.
                coverage_source = numerator.where(denominator > 0)
                values = (numerator / denominator.replace(0, pd.NA)) * float(entry.get("scale", 1))

            coverage = float((coverage_source > 0).sum()) / max(len(frame), 1)
            if coverage < min_coverage:
                # Too many months came back as zero for this to mean anything.
                continue

            for period, value in zip(frame["period"], values):
                if pd.isna(value) or value == 0:
                    continue
                yield CanonicalRecord(
                    metric_id=metric_id,
                    geo_id=geo_id,
                    period=str(period)[:7],
                    value=round(float(value), 3),
                    unit=meta["unit"],
                    source_id=self.manifest.source_id,
                    published_at=exported,
                )
