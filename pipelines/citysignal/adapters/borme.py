"""BORME — the Boletín Oficial del Registro Mercantil, company births and deaths.

Every incorporation, dissolution and insolvency filing that a Spanish commercial
registry (Registro Mercantil) processes is republished the next business day in
the BORME, organised by province — one section per registry, one entry per
company act. It is the closest thing Spain has to a real-time company register,
and unlike the tax or Social Security registers it is fully open, no application
required. This adapter reads the daily province sections and rolls three signals
up to monthly: new companies (``Constitución``), companies leaving the register
(``Disolución`` / ``Extinción``), and insolvency filings (an ``Auto de
declaración de concurso`` — the court order opening insolvency proceedings, the
actual legal event; BORME does not use the informal phrase "concurso de
acreedores" itself). Incorporations lead the cycle — someone registering a
company is making a forward-looking bet on the local economy; dissolutions and
insolvencies lag it — the business or its lender is recognising a bet that
already failed.

Geography is the registry's province, published directly in the sumario as a
province name (``BORME-A-2025-9-28`` is the Madrid section of issue 9). It is
not the registered address within the province, and it is not the municipality —
CitySignal only ever reads it as a province-level series.

The BOE's open-data API (documented at ``/datosabiertos/documentos/
APIsumarioBORME.pdf``) publishes one JSON sumario per calendar day and one XML
document per province section inside it; there is no server-side monthly
rollup, so this adapter must fetch and count each business day itself. To keep
that bounded it never re-derives a month once that month has closed and been
committed to history: every run re-fetches the current, still-accreting month
in full (so its total is always exact, not a running partial sum) and, only if
a run has been missed, catches up a small, capped number of prior closed
months once each.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.history import history_path, read_history
from ..framework.record import CanonicalRecord, province

SUMARIO_API = "https://www.boe.es/datosabiertos/api/borme/sumario/{date}"

# BORME province-section titles for our eight target provinces, exactly as the
# sumario API spells them (accents and slashes included).
PROVINCE_TITLES = {
    "MADRID": "28",
    "BARCELONA": "08",
    "VALENCIA/VALÈNCIA": "46",
    "MÁLAGA": "29",
    "SEVILLA": "41",
    "ILLES BALEARS": "07",
    "BIZKAIA": "48",
    "ZARAGOZA": "50",
}

UNIT = {
    "company_incorporations": "companies",
    "company_dissolutions": "companies",
    "insolvencies": "cases",
}

# Bounded catch-up: the current month in full, plus at most this many prior
# closed months if a gap in scheduling left them uncaptured. Each month costs
# roughly (business days) x (provinces that published that day) requests.
MAX_BACKFILL_MONTHS = 3
_PARAGRAPH_RE = re.compile(r'<p class="parrafo">(.*?)</p>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_CONCURSO_RE = re.compile(r"declaraci[oó]n de concurso", re.IGNORECASE)


def _as_list(value: Any) -> list[Any]:
    """The BOE JSON API collapses single-item arrays to a bare object."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _shift_month(ym: tuple[int, int], delta: int) -> tuple[int, int]:
    year, month = ym
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


def _business_days(year: int, month: int, *, not_after: date) -> list[date]:
    day = date(year, month, 1)
    out = []
    while day.month == month and day <= not_after:
        if day.weekday() < 5:  # Mon–Fri; BORME never publishes on weekends
            out.append(day)
        day += timedelta(days=1)
    return out


class BormeAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="borme",
        publisher="Registro Mercantil / BOE",
        license="Reuse permitted (attribution)",
        attribution="Source: BORME (boe.es)",
        docs_url="https://www.boe.es/datosabiertos/documentos/APIsumarioBORME.pdf",
        cadence="monthly",
        geo_level="province",
        max_age_days=45,
        formats=("json", "xml"),
        kind="official",
        redistribute=True,
        # Re-derived in full every run until the month closes.
        revisions_allowed=True,
        # finalize() sums many daily province documents into one monthly total
        # per metric; a day whose bytes are unchanged must still be counted.
        aggregates_across_plans=True,
        notes=(
            "Daily per-province BORME sections rolled up to monthly counts of "
            "incorporations, dissolutions and insolvency filings. No deep "
            "backfill: history accrues from whenever this adapter starts running."
        ),
    )

    def __init__(self) -> None:
        self._counts: defaultdict[tuple[str, str, str], float] = defaultdict(float)

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        today = date.today()
        current = (today.year, today.month)

        stored_periods: set[str] = set()
        for metric_id in UNIT:
            path = history_path(ctx.data_dir, self.manifest.source_id, metric_id)
            for row in read_history(path):
                stored_periods.add(row["period"])

        months = [current]
        probe = _shift_month(current, -1)
        while (
            f"{probe[0]:04d}-{probe[1]:02d}" not in stored_periods
            and len(months) < MAX_BACKFILL_MONTHS
        ):
            months.append(probe)
            probe = _shift_month(probe, -1)

        plans: list[FetchPlan] = []
        for year, month in months:
            period = f"{year:04d}-{month:02d}"
            for day in _business_days(year, month, not_after=today):
                plans.extend(self._day_plans(ctx, day, period))
        return plans

    def _day_plans(self, ctx: RunContext, day: date, period: str) -> list[FetchPlan]:
        date_str = day.strftime("%Y%m%d")
        listing = FetchPlan(url=SUMARIO_API.format(date=date_str), fmt="json", label=f"sumario:{date_str}")
        try:
            payload = ctx.fetcher.get(listing, headers={"Accept": "application/json"})
        except Exception:  # noqa: BLE001 — a holiday or an outage costs one day, not the run
            return []
        if payload is None:
            return []

        try:
            sumario = payload.json()["data"]["sumario"]
        except (ValueError, KeyError, TypeError):
            return []

        plans: list[FetchPlan] = []
        for diario in _as_list(sumario.get("diario")):
            for seccion in _as_list(diario.get("seccion")):
                if seccion.get("codigo") != "A":
                    continue  # Section A: actos inscritos. Section B is mergers/other notices.
                for item in _as_list(seccion.get("item")):
                    prov = PROVINCE_TITLES.get(item.get("titulo", ""))
                    url_xml = item.get("url_xml")
                    if not prov or not url_xml:
                        continue
                    plans.append(
                        FetchPlan(
                            url=url_xml,
                            fmt="xml",
                            label=f"{date_str}:{item['titulo']}",
                            optional=True,
                            meta={"province": prov, "period": period},
                        )
                    )
        return plans

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        text = payload.text()
        if "<documento" not in text:
            raise AdapterFailure(f"{payload.plan.url} did not return a BORME XML document")

        kinds: list[str] = []
        for raw_paragraph in _PARAGRAPH_RE.findall(text):
            para = _TAG_RE.sub("", raw_paragraph)
            found: set[str] = set()
            if para.lstrip().startswith("Constitución."):
                found.add("company_incorporations")
            if "Extinción" in para or "Disolución" in para:
                found.add("company_dissolutions")
            if _CONCURSO_RE.search(para):
                found.add("insolvencies")
            kinds.extend(found)
        return pd.DataFrame({"kind": kinds})

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        geo = province(plan.meta["province"])
        period = plan.meta["period"]
        for kind, n in frame["kind"].value_counts().items():
            self._counts[(kind, geo, period)] += float(n)
        return ()

    def finalize(
        self, records: list[CanonicalRecord], ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        out = list(records)
        for (metric_id, geo, period), value in sorted(self._counts.items()):
            out.append(
                CanonicalRecord(
                    metric_id=metric_id,
                    geo_id=geo,
                    period=period,
                    value=value,
                    unit=UNIT[metric_id],
                    source_id=self.manifest.source_id,
                )
            )
        return out
