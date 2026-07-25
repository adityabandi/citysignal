"""Aena monthly airport passenger traffic — the demand-side twin of EUROCONTROL's flights.

Aena's public statistics site does not expose a stable data API or predictable file
paths: each monthly "Informe estadístico" is served from a content-management blob
URL (``/sites/Satellite?...blobwhere=<opaque id>``) that only exists once, discovered
by scraping the listing page for that run. Hard-coding one of these ids would work
today and silently stop working the day the next report is published, which is
exactly the failure mode ``discover()`` is supposed to avoid — so this adapter walks
``informes-mensuales.html``, reads off each card's "Informe <mes> <año>" heading and
its paired XLS link, and only then builds fetch plans.

The XLS itself is a network-wide ranking (passengers and operations for every
airport Aena operates, in Spain and abroad) with no daily grain — it is a monthly
snapshot, and Aena marks it "DATOS PROVISIONALES": the same month's figure can be
revised in a later report, which is why ``revisions_allowed=True`` here instead of
the default that would quarantine a routine restatement.

Like EUROCONTROL, this is airport-level evidence, not municipal: Palma's airport
figure describes everyone flying into Mallorca, not people moving to the city of
Palma specifically. ``geo_level: airport`` keeps that boundary explicit in the data
model, matching ``airport_flights``.
"""

from __future__ import annotations

import io
import re
import unicodedata
from typing import Iterable

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.record import CanonicalRecord, airport

LISTING_URL = "https://www.aena.es/es/estadisticas/informes-mensuales.html"

MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12,
}

# Substring match against the accent-stripped, upper-cased airport name column in
# the "Ranking mensual" sheet. Aena's own naming varies ("BARCELONA-EL PRAT J.T."
# one month, plain "BARCELONA" another) so this is deliberately loose.
AIRPORT_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("MADRID-BARAJAS", "MAD"),
    ("BARCELONA-EL PRAT", "BCN"),
    ("VALENCIA", "VLC"),
    ("MALAGA", "AGP"),
    ("SEVILLA", "SVQ"),
    ("PALMA DE MALLORCA", "PMI"),
    ("BILBAO", "BIO"),
    ("ZARAGOZA", "ZAZ"),
)

_TITLE_RE = re.compile(r"Informe\s+([a-záéíóúñ]+\s+\d{4})", re.IGNORECASE)
# The XLS/PDF label sits behind an icon <span> inside the anchor, not immediately
# after the opening tag, so this captures the whole anchor body and classifies it
# by substring rather than requiring the label to be the first thing inside <a>.
_LINK_RE = re.compile(r'<a href="([^"]*blobwhere=\d+[^"]*)"[^>]*>(.*?)</a>', re.S)


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


class AenaAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="aena",
        publisher="Aena",
        license="Reuse permitted (attribution)",
        attribution="Source: Aena",
        docs_url="https://www.aena.es/es/estadisticas/inicio.html",
        cadence="monthly",
        geo_level="airport",
        max_age_days=75,
        formats=("xlsx",),
        redistribute=True,
        revisions_allowed=True,
        kind="official",
        notes=(
            "Network-wide ranking XLS, filtered to our eight airports by name. "
            "Aena marks these figures provisional and revises them in later reports."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        listing = ctx.fetcher.get(FetchPlan(url=LISTING_URL, fmt="html", label="aena-listing"))
        if listing is None:
            raise AdapterFailure("aena listing page returned no content")
        html = listing.text()

        # Titles ("Informe junio 2026") and download links appear in document order,
        # one title followed by a PDF link then an XLS link per monthly card.
        events: list[tuple[int, str, object]] = []
        for m in _TITLE_RE.finditer(html):
            events.append((m.start(), "title", m.group(1)))
        for m in _LINK_RE.finditer(html):
            label = re.sub(r"<[^>]+>", " ", m.group(2))
            events.append((m.start(), "link", (m.group(1), label)))
        events.sort(key=lambda e: e[0])

        plans: list[FetchPlan] = []
        current_period: str | None = None
        for _, kind, value in events:
            if kind == "title":
                month_name, year = value.rsplit(" ", 1)  # type: ignore[union-attr]
                month_num = MESES_ES.get(month_name.strip().lower())
                current_period = f"{int(year):04d}-{month_num:02d}" if month_num else None
            elif kind == "link" and current_period and "XLS" in value[1]:  # type: ignore[index]
                href = value[0]  # type: ignore[index]
                url = href if href.startswith("http") else f"https://www.aena.es{href}"
                plans.append(
                    FetchPlan(
                        url=url,
                        fmt="xlsx",
                        label=f"aena:{current_period}",
                        optional=True,
                        meta={"period": current_period},
                    )
                )

        if not plans:
            raise AdapterFailure(
                "no XLS links found on aena informes-mensuales.html — page layout may have changed"
            )
        return plans

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        try:
            return pd.read_excel(
                io.BytesIO(payload.content), sheet_name="Ranking mensual", header=None, engine="openpyxl"
            )
        except ValueError:
            # Sheet name is a template convention, not a guarantee.
            return pd.read_excel(io.BytesIO(payload.content), sheet_name=0, header=None, engine="openpyxl")

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        period = plan.meta["period"]
        matched: set[str] = set()
        out: list[CanonicalRecord] = []

        for _, row in frame.iterrows():
            name_cell = row.get(1)
            if not isinstance(name_cell, str):
                continue
            name = _strip_accents(name_cell).upper().strip()
            if name.startswith("TOTAL"):
                # End of the Spanish-network section; everything after this is
                # Aena's overseas airports (Brazil, London Luton), never ours.
                break
            value_cell = row.get(2)
            if not isinstance(value_cell, (int, float)) or pd.isna(value_cell):
                continue
            for keyword, iata in AIRPORT_KEYWORDS:
                if iata in matched or keyword not in name:
                    continue
                matched.add(iata)
                out.append(
                    CanonicalRecord(
                        metric_id="airport_passengers",
                        geo_id=airport(iata),
                        period=period,
                        value=float(value_cell),
                        unit="passengers",
                        source_id=self.manifest.source_id,
                    )
                )
                break

        return out
