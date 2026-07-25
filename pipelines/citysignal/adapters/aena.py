"""Aena monthly airport passenger traffic — the demand-side twin of EUROCONTROL's flights.

Aena's public statistics site does not expose a stable data API or predictable file
paths: each monthly "Informe estadístico" is served from a content-management blob
URL (``/sites/Satellite?...blobwhere=<opaque id>``) that only exists once, discovered
by scraping the listing page for that run. Hard-coding one of these ids would work
today and silently stop working the day the next report is published, which is
exactly the failure mode ``discover()`` is supposed to avoid — so this adapter walks
``informes-mensuales.html``, reads off each card's "Informe <mes> <año>" heading and
its paired XLS link, and only then builds fetch plans.

Unlike the base listing page (which only ever shows the current year), each year's
cards are also reachable via ``?anio=<year>`` — confirmed to return byte-identical
markup to the base page for the current year — so the full backfill window is just
one request per year, not a scrape-and-paginate dance.

The XLS itself is a network-wide ranking (passengers and operations for every
airport Aena operates, in Spain and abroad) with no daily grain — it is a monthly
snapshot, and Aena marks it "DATOS PROVISIONALES": the same month's figure can be
revised in a later report, which is why ``revisions_allowed=True`` here instead of
the default that would quarantine a routine restatement.

Two eras, two container formats. Every report from 2022 onward is a genuine .xlsx
(zip/OOXML); 2021 and earlier are legacy .xls (OLE2/BIFF), byte-verified across
2019-2022 samples and consistent within each year (Aena appears to have re-exported
each year's archive as one batch at whatever tool it was using at the time). Since
the URL gives no hint of which format a given month is, ``discover()`` picks the
plan's ``fmt`` — and therefore the read engine — from the year alone; a rare
mismatch just fails that one optional plan gracefully via ``sniff()`` rather than
silently misreading it.

The two eras also disagree on internal layout: modern reports have a sheet named
(something containing) "Ranking mensual" with the airport name in column 1 and the
passenger total in column 2; legacy reports have a sheet named "<MES> <AÑO>" with
the name column further right and a spacer column before the total. Rather than
hard-code either shape, ``_pick_sheet``/``_find_name_col`` locate the right sheet
and column by content (searching for a known anchor airport) so both eras normalize
through the same code path.

Like EUROCONTROL, this is airport-level evidence, not municipal: Palma's airport
figure describes everyone flying into Mallorca, not people moving to the city of
Palma specifically. ``geo_level: airport`` keeps that boundary explicit in the data
model, matching ``airport_flights``.
"""

from __future__ import annotations

import io
import re
import unicodedata
from datetime import date
from typing import Iterable

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.history import history_path, read_history
from ..framework.record import CanonicalRecord, airport

LISTING_URL = "https://www.aena.es/es/estadisticas/informes-mensuales.html"

# How far back the backfill window reaches — see sepe.py for why (24+ observations
# needed before the derive engine will score a measure at all).
BACKFILL_START = "2019-01"

# Reports for this year and earlier are legacy .xls (OLE2); 2022 onward are .xlsx
# (zip/OOXML). Byte-verified against real downloads for 2019, 2020, 2021 (all OLE2)
# and 2022, 2024, 2026 (all zip) — see module docstring.
LEGACY_XLS_LAST_YEAR = 2021

# How many of the most recent available months to re-check every run regardless of
# what is already stored, to catch Aena's routine "DATOS PROVISIONALES" revisions.
REVISION_RECHECK_MONTHS = 2

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


def _pick_sheet(sheet_names: list[str]) -> str:
    """Pick the passenger-ranking sheet out of a workbook, old era or new.

    Modern workbooks name it "Ranking mensual" (sometimes with a suffix like
    "(con 2019)"); legacy workbooks name it after the month itself ("ABRIL 2020")
    and carry a "Mozart Reports" pivot-cache sheet alongside it that is not data.
    """
    ranking = [s for s in sheet_names if "ranking mensual" in s.lower() and "acumul" not in s.lower()]
    if ranking:
        return ranking[0]
    candidates = [s for s in sheet_names if "mozart" not in s.lower() and "acumul" not in s.lower()]
    if candidates:
        return candidates[0]
    return sheet_names[0]


def _find_name_col(frame: pd.DataFrame) -> int | None:
    """Locate the column holding airport names by searching for a known anchor.

    Modern sheets put it in column 1; legacy sheets put it further right (with a
    blank spacer column before the passenger total). Rather than hard-code either,
    search for Madrid-Barajas — present in every month of this series, the whole
    archive back to 2019 — and use whichever column it turns up in.
    """
    anchor = AIRPORT_KEYWORDS[0][0]  # "MADRID-BARAJAS"
    for col in frame.columns:
        for cell in frame[col]:
            if isinstance(cell, str) and anchor in _strip_accents(cell).upper():
                return col
    return None


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
        formats=("xls", "xlsx"),
        redistribute=True,
        revisions_allowed=True,
        kind="official",
        notes=(
            "Network-wide ranking XLS, filtered to our eight airports by name. "
            "Aena marks these figures provisional and revises them in later reports."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        current_year = date.today().year
        start_year = int(BACKFILL_START[:4])

        periods: dict[str, tuple[str, str]] = {}  # period -> (href, label_text)
        for year in range(start_year, current_year + 1):
            year_url = f"{LISTING_URL}?anio={year}"
            page = ctx.fetcher.get(FetchPlan(url=year_url, fmt="html", label=f"aena-listing:{year}"))
            if page is None:
                continue
            self._extract_period_links(page.text(), periods)

        if not periods:
            raise AdapterFailure(
                "no XLS links found on aena informes-mensuales.html — page layout may have changed"
            )

        available = {p for p in periods if p >= BACKFILL_START}
        target_periods = self._select_target_periods(ctx, available)

        plans: list[FetchPlan] = []
        for period in sorted(target_periods):
            href = periods[period][0]
            url = href if href.startswith("http") else f"https://www.aena.es{href}"
            year = int(period[:4])
            fmt = "xls" if year <= LEGACY_XLS_LAST_YEAR else "xlsx"
            plans.append(
                FetchPlan(
                    url=url,
                    fmt=fmt,
                    label=f"aena:{period}",
                    optional=True,
                    meta={"period": period},
                )
            )

        if not plans:
            raise AdapterFailure(
                "no XLS links found on aena informes-mensuales.html — page layout may have changed"
            )
        return plans

    @staticmethod
    def _extract_period_links(html: str, periods: dict[str, tuple[str, str]]) -> None:
        # Titles ("Informe junio 2026") and download links appear in document order,
        # one title followed by a PDF link then an XLS link per monthly card.
        events: list[tuple[int, str, object]] = []
        for m in _TITLE_RE.finditer(html):
            events.append((m.start(), "title", m.group(1)))
        for m in _LINK_RE.finditer(html):
            label = re.sub(r"<[^>]+>", " ", m.group(2))
            events.append((m.start(), "link", (m.group(1), label)))
        events.sort(key=lambda e: e[0])

        current_period: str | None = None
        for _, kind, value in events:
            if kind == "title":
                month_name, year = value.rsplit(" ", 1)  # type: ignore[union-attr]
                month_num = MESES_ES.get(month_name.strip().lower())
                current_period = f"{int(year):04d}-{month_num:02d}" if month_num else None
            elif kind == "link" and current_period and "XLS" in value[1]:  # type: ignore[index]
                periods[current_period] = value  # type: ignore[assignment]

    def _select_target_periods(self, ctx: RunContext, available: Iterable[str]) -> set[str]:
        """Same incremental-backfill policy as sepe.py — see there for the rationale."""
        available = sorted(available)
        if ctx.force:
            return set(available)

        stored_periods = {
            row["period"] for row in read_history(history_path(ctx.data_dir, self.manifest.source_id, "airport_passengers"))
        }
        if not stored_periods:
            return set(available)

        earliest_stored, latest_stored = min(stored_periods), max(stored_periods)
        recheck = set(available[-REVISION_RECHECK_MONTHS:])
        return {p for p in available if p < earliest_stored or p > latest_stored} | recheck

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        engine = "xlrd" if payload.plan.fmt == "xls" else "openpyxl"
        workbook = pd.ExcelFile(io.BytesIO(payload.content), engine=engine)
        sheet = _pick_sheet(workbook.sheet_names)
        return pd.read_excel(io.BytesIO(payload.content), sheet_name=sheet, header=None, engine=engine)

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        period = plan.meta["period"]
        name_col = _find_name_col(frame)
        if name_col is None:
            raise AdapterFailure(f"aena {period}: could not locate an airport-name column in the parsed sheet")

        matched: set[str] = set()
        out: list[CanonicalRecord] = []

        for _, row in frame.iterrows():
            name_cell = row.get(name_col)
            if not isinstance(name_cell, str):
                continue
            name = _strip_accents(name_cell).upper().strip()
            if name.startswith("TOTAL"):
                # End of the Spanish-network section; everything after this is
                # Aena's overseas airports (Brazil, London Luton), never ours.
                break
            # The passenger total sits somewhere to the right of the name column —
            # directly adjacent in modern sheets, one blank spacer column further
            # in legacy ones — so take the first numeric cell found looking right.
            value_cell = None
            for offset in range(1, 6):
                candidate = row.get(name_col + offset)
                if isinstance(candidate, (int, float)) and not pd.isna(candidate):
                    value_cell = candidate
                    break
            if value_cell is None:
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
