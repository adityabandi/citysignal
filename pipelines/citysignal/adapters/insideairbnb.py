"""Inside Airbnb — a scraped research snapshot of short-term rental listings, not a register.

Inside Airbnb periodically scrapes Airbnb's public search results and republishes
them as a per-city ``listings.csv.gz``, linked from a single index page. This is the
single most important thing to hold onto about every number this adapter produces:
it is what a volunteer research project could see on the site on scrape day, not
what any authority licenses, taxes or counts. A listing can be a duplicate, a
long-since-delisted property Airbnb's search still surfaces, or a professional
operator's fortieth unit — Inside Airbnb's own data-assumptions page says as much.
That is why ``kind="research"`` and ``redistribute=False`` here, and why the site
must always show this beside INE's official ``tourist_dwellings`` count rather than
instead of it.

Three readings come out of one snapshot: how many listings exist, what share are
whole homes (displacing long-term housing stock, as opposed to a spare room), and
what share belong to hosts running more than one listing (a proxy for professional
operators rather than someone renting out their own place occasionally). All three
move ahead of official tourist-dwelling registrations, which is why they are tagged
``leading``.

Two of our eight cities are covered by a *regional* Inside Airbnb file rather than a
city-specific one — "mallorca" spans the whole island and "euskadi" spans the whole
Basque Country — so this adapter filters those down to the ``neighbourhood_cleansed``
matching the actual municipality (Palma de Mallorca, Bilbao) before computing
anything. Getting that filter wrong would silently turn an island-wide count into a
fabricated city number, which is worse than not shipping the metric at all. Zaragoza
has no Inside Airbnb coverage (``cities.yml`` records it as ``null``) and is skipped.
"""

from __future__ import annotations

import io
import re
from typing import Iterable

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.record import CanonicalRecord, municipality

LISTING_URL = "https://insideairbnb.com/get-the-data/"

_SNAPSHOT_RE = re.compile(
    r"https://data\.insideairbnb\.com/spain/[^/\"]+/([a-z-]+)/(\d{4}-\d{2}-\d{2})/data/listings\.csv\.gz"
)

# Snapshots covering more than one municipality: filter to this neighbourhood_cleansed
# value before computing anything. Cities not listed here already publish a
# single-municipality file (confirmed against neighbourhood_cleansed on each: Madrid,
# Barcelona, Valencia, Málaga and Sevilla's files use that city's own districts).
REGIONAL_FILTER: dict[str, str] = {
    "mallorca": "Palma de Mallorca",
    "euskadi": "Bilbao",
}


class InsideAirbnbAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="insideairbnb",
        publisher="Inside Airbnb",
        license="CC BY 4.0 (independent research project)",
        attribution="Source: Inside Airbnb (insideairbnb.com), a non-official research dataset",
        docs_url="https://insideairbnb.com/data-assumptions/",
        cadence="monthly",
        geo_level="municipality",
        max_age_days=210,
        formats=("csv",),
        redistribute=False,
        revisions_allowed=True,
        kind="research",
        notes=(
            "Scraped snapshot, not a register. Two of eight cities are filtered out of a "
            "regional file (Palma from the Mallorca-wide snapshot, Bilbao from Euskadi-wide)."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        index = ctx.fetcher.get(FetchPlan(url=LISTING_URL, fmt="html", label="insideairbnb-index"))
        if index is None:
            raise AdapterFailure("insideairbnb get-the-data page returned no content")
        html = index.text()

        # The index only ever links the latest snapshot per city, which already
        # satisfies "fetch only the most recent snapshot" — there is no older
        # snapshot to accidentally also pull in.
        latest: dict[str, tuple[str, str]] = {}
        for m in _SNAPSHOT_RE.finditer(html):
            slug, snapshot_date = m.group(1), m.group(2)
            latest[slug] = (m.group(0), snapshot_date)

        plans: list[FetchPlan] = []
        for city in ctx.config.cities:
            slug = city.insideairbnb
            if not slug:
                continue  # Zaragoza: no coverage
            hit = latest.get(slug)
            if hit is None:
                continue
            url, snapshot_date = hit
            plans.append(
                FetchPlan(
                    url=url,
                    fmt="gz",
                    label=f"insideairbnb:{city.slug}:{snapshot_date}",
                    optional=True,
                    meta={"city": city.slug, "airbnb_slug": slug, "snapshot_date": snapshot_date},
                )
            )

        if not plans:
            raise AdapterFailure(
                "no insideairbnb snapshot links resolved for any of our cities — page layout may have changed"
            )
        return plans

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        return pd.read_csv(
            io.BytesIO(payload.content),
            compression="gzip",
            usecols=["id", "host_id", "room_type", "neighbourhood_cleansed"],
            low_memory=False,
        )

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        meta = plan.meta
        city = ctx.config.city(meta["city"])
        df = frame

        filter_name = REGIONAL_FILTER.get(meta["airbnb_slug"])
        if filter_name:
            df = df[df["neighbourhood_cleansed"] == filter_name]

        total = len(df)
        if total == 0:
            return ()

        geo = municipality(city.ine_mun)
        period = meta["snapshot_date"][:7]

        entire_share = round(100.0 * (df["room_type"] == "Entire home/apt").mean(), 3)
        host_counts = df["host_id"].value_counts()
        multi_hosts = set(host_counts[host_counts > 1].index)
        multi_share = round(100.0 * df["host_id"].isin(multi_hosts).mean(), 3)

        return [
            CanonicalRecord(
                metric_id="str_listings",
                geo_id=geo,
                period=period,
                value=float(total),
                unit="listings",
                source_id=self.manifest.source_id,
            ),
            CanonicalRecord(
                metric_id="str_entire_home_share",
                geo_id=geo,
                period=period,
                value=entire_share,
                unit="percent",
                source_id=self.manifest.source_id,
            ),
            CanonicalRecord(
                metric_id="str_multi_host_share",
                geo_id=geo,
                period=period,
                value=multi_share,
                unit="percent",
                source_id=self.manifest.source_id,
            ),
        ]
