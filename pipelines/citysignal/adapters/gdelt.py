"""GDELT DOC 2.0 — daily news coverage, no key required.

GDELT's DOC API (`/api/v2/doc/doc`) runs a full-text query against the outlets
it monitors and, in `timelinevol`/`timelinetone` mode, hands back a daily
time series rather than a page of articles: `timelinevol` is the share of
that day's monitored articles matching the query (already a percentage, which
is why `news_volume` and `news_housing_volume` are declared `unit: percent`
in the registry), and `timelinetone` is the mean sentiment score of matching
articles that day, roughly -10 (very negative) to +10 (very positive). Both
were confirmed against the live endpoint before this adapter was written: a
single `timelinevol` call for one city spanning 2018-01-01 to today returns
one JSON payload with one point per day — over 3,000 points — so the whole
history for one query is one request, not one request per month.

**This is attention, not demand** — the same caveat wikipedia.py carries, and
`sources.yml` says as much for this source ("Never mixed into demand
measures"). A spike in `news_volume` means the city was in the news more,
which can mean a heatwave, an election, a football result or a housing
crisis; `news_housing_volume` narrows that to coverage that also mentions
housing terms, but it is still coverage volume, not a housing indicator on
its own.

**Query disambiguation.** GDELT has no geography filter, only full-text
search, so a bare city name pulls in every place on earth that shares it.
`sourcecountry:SP` (outlets physically based in Spain) removes most of this
for six of the eight cities. Valencia and Málaga needed more: "Valencia" is
also a Venezuelan state capital that Spanish outlets do cover, and "Málaga"
is also a Colombian municipality and a Philippine town, so both queries add
explicit `-Venezuela -Carabobo` / `-Colombia -Filipinas` exclusions. "Palma"
alone would also match "Las Palmas de Gran Canaria" — a different city in
this same registry — so the Palma query uses the unambiguous full form
"Palma de Mallorca" rather than the bare city name. Every query string is
frozen in `config/baskets/gdelt.yml`, in the same spirit as the Wikipedia
baskets: reviewed once, versioned, never silently edited.

**Rate limiting.** The keyless endpoint asks for one request per 5 seconds
and enforces it inconsistently — sometimes a 429, sometimes a 200 whose body
is the plain-text throttle notice instead of JSON. The latter is caught by
the framework's own `sniff()` (a `json`-typed payload that doesn't start
with `{` or `[` is rejected as malformed) rather than anything special here.
Every plan is `optional=True` so one throttled city-metric does not sink the
whole source, and the shared `Fetcher` already retries with backoff.

**Monthly rollup happens per plan, not in `finalize()`.** Each plan already
returns a city-and-metric's entire daily history in one response, so the
day-to-month rollup (mean of daily values within the month, current
incomplete month dropped) happens directly in `normalize()`. `finalize()` is
only needed when a value can't be computed until every plan is in — summing
a basket, or dividing by a denominator fetched separately — and nothing here
depends on another plan's output, so `aggregates_across_plans` stays False
and ordinary content-hash skipping still applies.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterable

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.record import CanonicalRecord, municipality

API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


def _current_month() -> str:
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"


def _gdelt_datetime(iso_date: str) -> str:
    """'2018-01-01' -> '20180101000000', the GDELT start/enddatetime format."""
    return iso_date.replace("-", "") + "000000"


class GdeltAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="gdelt",
        publisher="The GDELT Project",
        license="Open, attribution requested",
        attribution="Source: The GDELT Project (gdeltproject.org)",
        docs_url="https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/",
        cadence="monthly",
        geo_level="municipality",
        max_age_days=45,
        formats=("json",),
        kind="research",
        redistribute=True,
        revisions_allowed=False,
        min_rows=0,
        notes=(
            "News attention and tone from GDELT DOC 2.0, daily resolution rolled up "
            "to monthly. Frozen per-city queries live in config/baskets/gdelt.yml. "
            "Never mixed into demand measures."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        basket = ctx.config.baskets.get("gdelt")
        if not basket:
            raise AdapterFailure("config/baskets/gdelt.yml is missing")

        start = _gdelt_datetime(basket["start"])
        end = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        housing_clause = "(" + " OR ".join(basket["housing_terms"]) + ")"

        plans: list[FetchPlan] = []
        for city in ctx.config.cities:
            spec = basket["cities"].get(city.slug)
            if not spec:
                continue
            base_query = spec["query"]

            # Interchangeable fetches: a throttled or malformed response for one
            # city-metric costs that column, not the source. See module docstring.
            plans.append(
                self._plan(base_query, "timelinevol", start, end, city.slug, "news_volume")
            )
            plans.append(
                self._plan(base_query, "timelinetone", start, end, city.slug, "news_tone")
            )
            plans.append(
                self._plan(
                    f"{base_query} {housing_clause}",
                    "timelinevol",
                    start,
                    end,
                    city.slug,
                    "news_housing_volume",
                )
            )
        return plans

    @staticmethod
    def _plan(query: str, mode: str, start: str, end: str, slug: str, metric_id: str) -> FetchPlan:
        return FetchPlan(
            url=API_URL,
            fmt="json",
            label=f"{slug}:{metric_id}",
            params={
                "query": query,
                "mode": mode,
                "format": "json",
                "startdatetime": start,
                "enddatetime": end,
            },
            optional=True,
            meta={"city": slug, "metric_id": metric_id},
        )

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        data = payload.json()
        timeline = data.get("timeline") or []
        if not timeline:
            # A throttled request that slipped past sniff() as valid JSON (an
            # empty {} rather than the plain-text notice) looks like this too.
            raise AdapterFailure(f"no timeline in GDELT response for {payload.plan.label}")
        series = timeline[0].get("data") or []
        if not series:
            raise AdapterFailure(f"empty timeline for {payload.plan.label}")
        frame = pd.DataFrame(series)
        # '20180101T000000Z' -> '2018-01'
        frame["period"] = frame["date"].str.slice(0, 4) + "-" + frame["date"].str.slice(4, 6)
        return frame[["period", "value"]]

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        city = ctx.config.city(plan.meta["city"])
        metric_id = plan.meta["metric_id"]
        geo = municipality(city.ine_mun)
        unit = "tone" if metric_id == "news_tone" else "percent"

        # The current month is always partial; averaging it in would read as a
        # sudden drop in coverage rather than a month still in progress.
        complete = frame[frame["period"] < _current_month()]
        monthly = complete.groupby("period")["value"].mean()

        return [
            CanonicalRecord(
                metric_id=metric_id,
                geo_id=geo,
                period=period,
                value=round(float(value), 4),
                unit=unit,
                source_id=self.manifest.source_id,
            )
            for period, value in monthly.items()
        ]
