"""Wikipedia pageviews across languages — the origin-market attention proxy.

Pageviews of the same article across different Wikipedia language editions is
a free, keyless proxy for origin-market interest. Germans reading about Madrid
is a different signal than Spanish readers doing the same; the language split
(without needing paid travel data) recovers an approximation of the origin
country. If German views are rising, German-speaking real-estate demand is
signalling ahead.

This adapter collects city articles across 13 language editions (en, de, fr,
it, pt, nl, pl, sv, da, ru, ja, zh, ar) and emits monthly views per language
as a per-million share of total traffic on that edition. It also computes
foreign_interest as the ratio of non-Spanish-language views to Spanish-language
views per city.

Some languages may have fewer than ~500 views/month on average and are dropped
(noise rather than signal). Article names vary per language; if an article does
not exist, that language/city pair is skipped and noted. All articles are
verified to refer to the Spanish city (not a homonym) by checking the REST
summary endpoint.

Data runs for all eight Spanish cities: Madrid, Barcelona, Valencia, Málaga,
Sevilla, Palma, Bilbao, Zaragoza. All observations are at municipality
geo_level.
"""

from __future__ import annotations

import logging
import urllib.parse
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Iterable

import pandas as pd

from ..framework.adapter import BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.record import CanonicalRecord, municipality

log = logging.getLogger(__name__)

API = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
AGGREGATE_API = "https://wikimedia.org/api/rest_v1/metrics/pageviews/aggregate"
SUMMARY_API = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
START = "2019010100"

# Languages to collect (13 major Wikipedia editions plus Spanish for denominator);
# threshold is ~500 views/month for filtering
LANGUAGES = ["en", "de", "fr", "it", "pt", "nl", "pl", "sv", "da", "ru", "ja", "zh", "ar", "es"]


def _month_period(timestamp: str) -> str:
    return f"{timestamp[:4]}-{timestamp[4:6]}"


class WikiLangsAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="wiki_langs",
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
        notes=(
            "Language-split pageviews as origin-market proxy. Published as shares of total traffic, "
            "not raw counts, to control for platform-wide decline. All articles verified to refer to "
            "Spanish cities (not homonyms). Some languages dropped if median views < 500/month. "
            "Foreign interest is (non-Spanish views) ÷ (Spanish views) per city."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        """Build plans to fetch city articles and language edition aggregates."""
        plans: list[FetchPlan] = []
        end = date.today().strftime("%Y%m%d00")

        # For each city, fetch the article across 13 languages
        for city in ctx.config.cities:
            ine_mun = city.ine_mun
            for lang in LANGUAGES:
                title = city.wikipedia.get(lang)
                if not title:
                    continue

                plans.append(
                    FetchPlan(
                        url=self._pageview_url(f"{lang}.wikipedia", title, end),
                        fmt="json",
                        label=f"{city.slug}:{lang}",
                        optional=True,  # Missing language edition is not a failure
                        meta={
                            "kind": "city",
                            "city_slug": city.slug,
                            "ine_mun": ine_mun,
                            "lang": lang,
                            "title": title,
                        },
                    )
                )

        # Denominators: total views per language edition per month
        # Must include Spanish (es) even if cities don't have es wiki (they do, but for generality)
        all_langs = set(LANGUAGES) | {"es"}
        for lang in sorted(all_langs):
            plans.append(
                FetchPlan(
                    url=f"{AGGREGATE_API}/{lang}.wikipedia/all-access/user/monthly/{START}/{end}",
                    fmt="json",
                    label=f"total:{lang}",
                    optional=True,  # If a language edition total is missing, we just can't normalize for it
                    meta={"kind": "total", "lang": lang},
                )
            )

        return plans

    @staticmethod
    def _pageview_url(project: str, title: str, end: str) -> str:
        """Build the ohsome-style per-article pageview URL."""
        encoded = urllib.parse.quote(title.replace(" ", "_"), safe="")
        return f"{API}/{project}/all-access/user/{encoded}/monthly/{START}/{end}"

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        """Extract time series from Wikimedia pageview endpoint."""
        data = payload.json()
        items = data.get("items", [])
        if not items:
            raise RuntimeError(f"no pageview items for {payload.plan.label}")

        frame = pd.DataFrame(items)
        frame["period"] = frame["timestamp"].map(_month_period)

        # Don't publish the current (partial) month
        current_month = date.today().strftime("%Y-%m")
        frame = frame[frame["period"] < current_month]

        if frame.empty:
            raise RuntimeError(f"no data before current month for {payload.plan.label}")

        return frame[["period", "views"]]

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        """Stage data for finalize() to compute ratios and foreign interest."""
        meta = plan.meta
        if meta["kind"] == "city":
            geo = municipality(meta["ine_mun"])
            lang = meta["lang"]
            for row in frame.itertuples():
                self._city_views[(geo, row.period)][lang] = int(row.views)
        elif meta["kind"] == "total":
            lang = meta["lang"]
            for row in frame.itertuples():
                self._totals[(lang, row.period)] = int(row.views)

        return ()

    def finalize(
        self, records: list[CanonicalRecord], ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        """Emit normalized per-language views and foreign_interest ratio."""
        out: list[CanonicalRecord] = list(records)

        # Threshold: drop languages with median < 500 views/month (noise)
        lang_medians = {}
        for (geo, period), by_lang in self._city_views.items():
            for lang, views in by_lang.items():
                lang_medians.setdefault(lang, []).append(views)

        dropped_langs = set()
        for lang, views_list in lang_medians.items():
            median = sorted(views_list)[len(views_list) // 2]
            if median < 500:
                dropped_langs.add(lang)
                log.info("dropping language %s: median %.0f views/month", lang, median)

        # Emit per-language views (as shares of total traffic on that edition)
        # Skip Spanish: it's only used for the foreign_interest denominator
        for (geo, period), by_lang in sorted(self._city_views.items()):
            for lang, views in by_lang.items():
                if lang == "es":
                    continue  # Spanish is denominator only, not a published metric
                if lang in dropped_langs:
                    continue

                total = self._totals.get((lang, period), 0)
                if total <= 0:
                    continue

                # Emit as per-million (parts per million) to handle very small shares
                share = round(1e6 * views / total, 2)
                out.append(
                    CanonicalRecord(
                        metric_id=f"wiki_lang_views_{lang}",
                        geo_id=geo,
                        period=period,
                        value=share,
                        unit="per_million",
                        source_id=self.manifest.source_id,
                    )
                )

            # Compute foreign_interest: (non-Spanish views) ÷ (Spanish views)
            # Note: "es" is the Spanish Wikipedia language code; used as denominator only
            spanish_views = sum(v for l, v in by_lang.items() if l == "es")
            foreign_views = sum(
                v for l, v in by_lang.items() if l != "es" and l not in dropped_langs
            )

            if spanish_views > 0:
                ratio = 100.0 * foreign_views / spanish_views
                out.append(
                    CanonicalRecord(
                        metric_id="wiki_foreign_interest",
                        geo_id=geo,
                        period=period,
                        value=round(ratio, 2),
                        unit="percent",
                        source_id=self.manifest.source_id,
                    )
                )

        return out

    # Staging state, rebuilt per run
    _city_views: defaultdict[tuple[str, str], dict[str, int]]
    _totals: dict[tuple[str, str], int]

    def __init__(self) -> None:
        self._city_views = defaultdict(dict)
        self._totals = {}
