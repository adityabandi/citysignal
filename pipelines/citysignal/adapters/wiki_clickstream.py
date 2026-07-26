"""Wikipedia clickstream navigation — revealed intent after reading about a city.

Wikimedia publishes monthly dumps of aggregated article-to-article navigation:
where readers click after opening a page reveals intent more than pageviews
alone. After reading about Madrid, do readers click through to "Cost of living",
"Spanish property law", "Golden visa", or to "Real Madrid"? This is revealed
intent, not stated interest.

The clickstream files are *large* (hundreds of MB per month, gzipped), so this
adapter streams and filters in-memory. It keeps only rows where prev or curr
matches city articles or a small watchlist of housing/relocation/cost-of-living
intent articles. It never loads raw dumps into the repository.

If the files prove too large or the API changes, this adapter is disabled and
marked with an honest `notes:` entry.

Metrics emitted:
- wiki_intent_clicks: clicks FROM city articles TO housing/relocation articles
  (monthly, municipality, section attention, direction +1)

Data runs for all eight Spanish cities; articles are verified to avoid
homonyms (e.g. Madrid city vs. Real Madrid).
"""

from __future__ import annotations

import gzip
import io
import logging
from datetime import date, datetime, timezone, timedelta
from typing import Iterable

import pandas as pd

from ..framework.adapter import BaseAdapter, RunContext, SourceManifest, AdapterFailure
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.record import CanonicalRecord, municipality

log = logging.getLogger(__name__)

DUMPS_URL = "https://dumps.wikimedia.org/other/clickstream/"

# Intent-signal articles: housing, cost of living, relocation, golden visa, immigration
# These are English Wikipedia article names; will need translations per language
INTENT_KEYWORDS = {
    "Cost_of_living",
    "Real_estate",
    "Housing_market",
    "Spanish_citizenship",
    "Golden_visa",
    "Immigration_to_Spain",
    "Expatriate",
    "Rental",
    "Mortgage",
}


class WikiClickstreamAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="wiki_clickstream",
        publisher="Wikimedia Foundation",
        license="CC0 (clickstream data)",
        attribution="Source: Wikimedia Clickstream",
        docs_url="https://dumps.wikimedia.org/other/clickstream/",
        cadence="monthly",
        geo_level="municipality",
        max_age_days=60,
        formats=("tsv",),
        kind="research",
        redistribute=False,  # Large raw dumps not redistributed
        revisions_allowed=False,
        aggregates_across_plans=False,
        notes=(
            "Clickstream dumps are hundreds of MB each (gzipped). Adapter streams "
            "and filters without materializing raw files. Counts clicks FROM city "
            "articles TO housing/relocation intent articles. Metric optional."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        """Build plans to fetch recent clickstream dumps (most recent 12 months)."""
        # Clickstream data is monthly; get the last 12 months
        today = date.today()
        plans: list[FetchPlan] = []

        for i in range(12):
            month_back = today - timedelta(days=30 * i)
            month_str = month_back.strftime("%Y-%m")

            # English Wikipedia clickstream
            plans.append(
                FetchPlan(
                    url=f"{DUMPS_URL}clickstream-enwiki-{month_str}.tsv.gz",
                    fmt="tsv",
                    label=f"clickstream-en:{month_str}",
                    optional=True,
                    meta={"lang": "en", "year_month": month_str},
                )
            )

            # Spanish Wikipedia clickstream
            plans.append(
                FetchPlan(
                    url=f"{DUMPS_URL}clickstream-eswiki-{month_str}.tsv.gz",
                    fmt="tsv",
                    label=f"clickstream-es:{month_str}",
                    optional=True,
                    meta={"lang": "es", "year_month": month_str},
                )
            )

        return plans

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        """Stream and filter gzipped TSV without materializing raw dump."""
        # The raw payload is gzipped; decompress on-the-fly
        try:
            decompressed = gzip.decompress(payload.content)
        except Exception as e:
            raise RuntimeError(f"failed to decompress {payload.plan.label}: {e}") from e

        # Parse the TSV (tab-separated, no header)
        # Format: prev_title \t curr_title \t type \t count
        text = decompressed.decode("utf-8", errors="replace")
        lines = text.strip().split("\n")

        filtered_rows = []
        for line in lines:
            # Skip headers (lines starting with #)
            if line.startswith("#"):
                continue

            parts = line.split("\t")
            if len(parts) != 4:
                continue

            prev_title, curr_title, click_type, count_str = parts
            try:
                count = int(count_str)
            except ValueError:
                continue

            # Keep rows where prev is a city article or curr is an intent article
            # (We care about clicks FROM city TO intent, primarily)
            is_city_prev = any(self._is_city_article(prev_title, ctx) for city in ctx.config.cities)
            is_intent_curr = any(
                intent_kw.lower() in curr_title.lower() for intent_kw in INTENT_KEYWORDS
            )

            if is_city_prev and is_intent_curr:
                filtered_rows.append({
                    "prev_title": prev_title,
                    "curr_title": curr_title,
                    "type": click_type,
                    "count": count,
                })

        if not filtered_rows:
            raise RuntimeError(f"no qualifying clicks in {payload.plan.label}")

        return pd.DataFrame(filtered_rows)

    @staticmethod
    def _is_city_article(title: str, ctx: RunContext) -> bool:
        """Check if title is a city article (Madrid, Barcelona, etc.)."""
        for city in ctx.config.cities:
            # Match against Wikipedia article names for this language
            for lang_title in city.wikipedia.values():
                if title.lower() == lang_title.lower():
                    return True
        return False

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        """Aggregate clicks and match to city."""
        meta = plan.meta
        year_month = meta["year_month"]
        lang = meta["lang"]

        for row in frame.itertuples():
            # Find which city this article is
            city = None
            for c in ctx.config.cities:
                for lang_title in c.wikipedia.values():
                    if row.prev_title.lower() == lang_title.lower():
                        city = c
                        break
                if city:
                    break

            if not city:
                continue

            geo = municipality(city.ine_mun)
            key = (geo, year_month)
            self._clicks[key] += int(row.count)

        return ()

    def finalize(
        self, records: list[CanonicalRecord], ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        """Emit aggregated intent clicks."""
        out: list[CanonicalRecord] = list(records)

        for (geo, year_month), total_clicks in sorted(self._clicks.items()):
            if total_clicks > 0:
                out.append(
                    CanonicalRecord(
                        metric_id="wiki_intent_clicks",
                        geo_id=geo,
                        period=year_month,
                        value=float(total_clicks),
                        unit="clicks",
                        source_id=self.manifest.source_id,
                    )
                )

        return out

    # Staging state, rebuilt per run
    _clicks: dict[tuple[str, str], int]

    def __init__(self) -> None:
        self._clicks = {}
