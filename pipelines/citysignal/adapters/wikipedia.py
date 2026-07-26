"""Wikipedia pageviews — the attention layer that works without a gated API.

Two distinct readings come out of the same endpoint:

1. **Who is looking at this city.** Reading a city's article across seven language
   editions approximates an origin-market attention matrix — German readers looking
   up Málaga is a different signal from Spanish readers doing the same — without
   needing Google Trends.
2. **What the country is worrying about.** A frozen basket of Spanish-language
   articles (Euríbor, desahucio, ingreso mínimo vital…) moves when household
   finances tighten. Euríbor is the anchor: it is the reference rate on most
   Spanish variable-rate mortgages, so its pageviews rise before the repayment does.

Everything here is published as a *share* of total traffic on the same language
editions, never as a raw count. Spanish Wikipedia served about 937 million views
in June 2021 and about 518 million in June 2026; a basket measured in raw views
would show a collapse in hardship that is really a collapse in Wikipedia reading.
The raw counts are still committed alongside, so the normalisation is auditable.

This is attention, never demand. Whether it leads anything observable is measured
by the lead-lag lab and published, not assumed.
"""

from __future__ import annotations

import urllib.parse
from collections import defaultdict
from datetime import date
from typing import Iterable

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.record import CanonicalRecord, municipality, district

API = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
AGGREGATE_API = "https://wikimedia.org/api/rest_v1/metrics/pageviews/aggregate"
START = "2018010100"

# Map Wikipedia article names to Madrid district codes (28079 is Madrid municipality)
MADRID_ARTICLE_TO_DISTRICT = {
    "Malasaña": "01",  # Centro
    "Lavapiés": "01",  # Centro
    "Chueca": "01",  # Centro
    "Barrio_de_Salamanca": "04",  # Salamanca
    "Chamberí": "07",  # Chamberí
    "Carabanchel": "11",  # Carabanchel
    "Usera": "12",  # Usera
    # Disambiguated deliberately: the bare article "Tetuán" on Spanish
    # Wikipedia is the Moroccan city of Tétouan, which was contributing some
    # 6,000 views a month to what this published as a Madrid district signal.
    "Tetuán_(Madrid)": "06",  # Tetuán
}


def _month_period(timestamp: str) -> str:
    return f"{timestamp[:4]}-{timestamp[4:6]}"


def _current_month() -> str:
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"


class WikipediaAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="wikipedia",
        publisher="Wikimedia Foundation",
        license="CC0 (pageview data)",
        attribution="Source: Wikimedia REST API pageviews",
        docs_url="https://wikimedia.org/api/rest_v1/",
        cadence="monthly",
        geo_level="municipality",
        max_age_days=45,
        formats=("json",),
        kind="research",
        redistribute=True,
        revisions_allowed=False,
        aggregates_across_plans=True,
        notes="Attention, not demand. Language split doubles as an origin-market proxy.",
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        basket = ctx.config.baskets.get("wikipedia")
        if not basket:
            raise AdapterFailure("config/baskets/wikipedia.yml is missing")

        end = date.today().strftime("%Y%m%d00")
        plans: list[FetchPlan] = []

        for city in ctx.config.cities:
            for lang in basket["city_languages"]:
                title = city.wikipedia.get(lang)
                if not title:
                    continue
                plans.append(
                    FetchPlan(
                        url=self._url(f"{lang}.wikipedia", title, end),
                        fmt="json",
                        label=f"{city.slug}:{lang}",
                        # Language editions rename and merge articles on their own
                        # schedule; a missing edition costs us one column, not the source.
                        optional=True,
                        meta={"kind": "city", "city": city.slug, "lang": lang},
                    )
                )

        for basket_name, metric_id in (
            ("hardship", "wiki_hardship_index"),
            ("relocation", "wiki_relocation_index"),
            ("barrio_madrid", "wiki_barrio_views"),
        ):
            spec = basket.get(basket_name)
            if not spec:
                continue
            for article in spec["articles"]:
                plans.append(
                    FetchPlan(
                        url=self._url(spec["project"], article, end),
                        fmt="json",
                        label=f"{basket_name}:{article}",
                        optional=True,
                        meta={"kind": "basket" if basket_name != "barrio_madrid" else "barrio",
                              "metric_id": metric_id, "article": article},
                    )
                )

        # Denominators: total views per language edition, so every basket can be
        # expressed as a share rather than a count.
        for lang in sorted(set(basket["city_languages"]) | {"es"}):
            plans.append(
                FetchPlan(
                    url=f"{AGGREGATE_API}/{lang}.wikipedia/all-access/user/monthly/{START}/{end}",
                    fmt="json",
                    label=f"total:{lang}",
                    meta={"kind": "total", "lang": lang},
                )
            )

        return plans

    @staticmethod
    def _url(project: str, title: str, end: str) -> str:
        encoded = urllib.parse.quote(title.replace(" ", "_"), safe="")
        return f"{API}/{project}/all-access/user/{encoded}/monthly/{START}/{end}"

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        items = payload.json().get("items", [])
        if not items:
            # A renamed or redirected article returns an empty item list rather
            # than a 404, which would otherwise look like a genuine zero.
            raise AdapterFailure(f"no pageview items for {payload.plan.label}")
        frame = pd.DataFrame(items)
        frame["period"] = frame["timestamp"].map(_month_period)
        # The current month is always partial; publishing it would read as a collapse.
        return frame.loc[frame["period"] < _current_month(), ["period", "views"]]

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        # Components are staged rather than emitted: both the city metrics and the
        # basket indices are sums or ratios that only exist once every plan is in.
        meta = plan.meta
        if meta["kind"] == "city":
            city = ctx.config.city(meta["city"])
            geo = municipality(city.ine_mun)
            for row in frame.itertuples():
                self._city_views[(geo, row.period)][meta["lang"]] = int(row.views)
        elif meta["kind"] == "total":
            for row in frame.itertuples():
                self._totals[(meta["lang"], row.period)] = int(row.views)
        elif meta["kind"] == "barrio":
            article = meta["article"]
            district_code = MADRID_ARTICLE_TO_DISTRICT.get(article)
            if district_code:
                geo = district("28079", district_code)
                for row in frame.itertuples():
                    self._barrio_views[(geo, row.period)] = int(row.views)
        else:  # "basket" (hardship, relocation, etc.)
            for row in frame.itertuples():
                self._basket_views[(meta["metric_id"], row.period)] += int(row.views)
        return ()

    def finalize(
        self, records: list[CanonicalRecord], ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        out: list[CanonicalRecord] = list(records)

        for (geo, period), by_lang in sorted(self._city_views.items()):
            total = sum(by_lang.values())
            if total <= 0:
                continue
            out.append(
                CanonicalRecord(
                    metric_id="wiki_city_views",
                    geo_id=geo,
                    period=period,
                    value=float(total),
                    unit="views",
                    source_id=self.manifest.source_id,
                )
            )
            # Share of all traffic on the editions this city was read in, which is
            # the only form comparable across years.
            denominator = sum(
                self._totals.get((lang, period), 0) for lang in by_lang
            )
            if denominator > 0:
                out.append(
                    CanonicalRecord(
                        metric_id="wiki_city_attention",
                        geo_id=geo,
                        period=period,
                        value=round(1e6 * total / denominator, 4),
                        unit="per_million",
                        source_id=self.manifest.source_id,
                    )
                )
            # A city read mostly from outside Spain is being considered from
            # outside Spain — the closest keyless stand-in for origin-market intent.
            spanish = by_lang.get("es", 0)
            out.append(
                CanonicalRecord(
                    metric_id="wiki_city_foreign_share",
                    geo_id=geo,
                    period=period,
                    value=round(100.0 * (total - spanish) / total, 3),
                    unit="percent",
                    source_id=self.manifest.source_id,
                )
            )

        for (metric_id, period), views in sorted(self._basket_views.items()):
            denominator = self._totals.get(("es", period), 0)
            if denominator <= 0:
                continue
            out.append(
                CanonicalRecord(
                    metric_id=metric_id,
                    geo_id="es",
                    period=period,
                    value=round(1e6 * views / denominator, 4),
                    unit="per_million",
                    source_id=self.manifest.source_id,
                )
            )

        # Barrio views: district-level observations of neighbourhood attention
        for (geo, period), views in sorted(self._barrio_views.items()):
            if views <= 0:
                continue
            out.append(
                CanonicalRecord(
                    metric_id="wiki_barrio_views",
                    geo_id=geo,
                    period=period,
                    value=float(views),
                    unit="views",
                    source_id=self.manifest.source_id,
                )
            )

        return out

    # Staging state, rebuilt per run.
    _city_views: defaultdict[tuple[str, str], dict[str, int]]
    _basket_views: defaultdict[tuple[str, str], int]
    _barrio_views: dict[tuple[str, str], int]
    _totals: dict[tuple[str, str], int]

    def __init__(self) -> None:
        self._city_views = defaultdict(dict)
        self._basket_views = defaultdict(int)
        self._barrio_views = {}
        self._totals = {}
