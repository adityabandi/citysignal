"""Google Trends without the API: hand-exported CSVs, read from the repository.

The official Trends API is alpha and application-gated. The unofficial endpoints
the scraping libraries use are not a viable substitute for a scheduled build —
`/trends/api/explore` returns HTTP 429 on a first request from a clean address,
and a shared CI runner fares worse. Building the weekly pipeline on that would
mean a public site whose search layer silently stops updating.

So the search layer takes the honest route. A person exports a basket from
trends.google.com — which is allowed, reproducible and costs nothing — and drops
the CSV into `data/manual/trends/`. This adapter reads whatever is there, records
when each file was exported, and marks the series stale when nobody has refreshed
it. No scraping, no credentials, no silent failure.

The important caveat travels with the data: Trends rescales its 0–100 index per
request, so two separately exported files do not share a scale. Each file is
therefore treated as its own series and only ever compared with itself over time,
never across files. When API access arrives, `trends.py` can replace this with
consistently scaled data and the metric ids stay the same.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.record import CanonicalRecord

# data/manual/trends/<metric_id>__<geo_id>.csv — e.g.
#   search_rental_pressure__mun-28079.csv
#   search_hardship__es.csv
FILENAME = re.compile(r"^(?P<metric>[a-z_]+)__(?P<geo>[a-z]+-?\d*|es)\.csv$")

DATE_ROW = re.compile(r"^(?P<date>\d{4}-\d{2}(-\d{2})?)\s*,")


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
            "is its own series and is never compared across files."
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
                    url=path.as_uri(),
                    fmt="csv",
                    label=path.name,
                    optional=True,
                    meta={
                        "path": str(path),
                        "metric_id": match["metric"],
                        "geo_id": match["geo"],
                    },
                )
            )

        if not plans:
            raise AdapterFailure(
                f"no exports found in {directory}. Export a basket from trends.google.com "
                "and save it as <metric_id>__<geo_id>.csv — the README there lists the "
                "baskets this build expects."
            )
        return plans

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        text = payload.text()
        # Trends exports carry a two-or-three line preamble ("Categoría: …", a
        # blank line, then the header) whose wording depends on the export locale,
        # so the data is found by shape rather than by skipping a fixed count.
        lines = text.splitlines()
        start = next((i for i, line in enumerate(lines) if DATE_ROW.match(line)), None)
        if start is None:
            raise AdapterFailure(f"{payload.plan.label}: no dated rows found")

        header_index = start - 1
        header = lines[header_index] if header_index >= 0 else "period,value"
        body = "\n".join([header, *lines[start:]])

        frame = pd.read_csv(io.StringIO(body))
        frame.columns = ["period", *[f"value_{i}" for i in range(1, len(frame.columns))]]
        return frame

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        metric_id = plan.meta["metric_id"]
        geo_id = plan.meta["geo_id"]
        meta = ctx.config.metrics.get(metric_id)
        if meta is None:
            raise AdapterFailure(
                f"{plan.label}: {metric_id!r} is not in config/metrics.yml"
            )

        exported = date.fromtimestamp(Path(plan.meta["path"]).stat().st_mtime).isoformat()
        value_columns = [c for c in frame.columns if c.startswith("value_")]

        for row in frame.itertuples():
            period = str(row.period)[:7]
            if len(period) != 7:
                continue
            # A multi-term export becomes one basket: the mean of its terms, which
            # is defensible only because they were exported together and therefore
            # do share a scale.
            values = [
                pd.to_numeric(getattr(row, column), errors="coerce") for column in value_columns
            ]
            values = [v for v in values if pd.notna(v)]
            if not values:
                continue

            yield CanonicalRecord(
                metric_id=metric_id,
                geo_id=geo_id,
                period=period,
                value=float(sum(values) / len(values)),
                unit=meta["unit"],
                source_id=self.manifest.source_id,
                published_at=exported,
            )
