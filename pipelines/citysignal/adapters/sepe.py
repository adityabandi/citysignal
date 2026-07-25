"""SEPE registered unemployment and new contracts, by municipality.

SEPE publishes "Paro registrado y contratos por municipios" as one XLS per province
per month, linked from a page that is organised year -> month -> per-province file.
The province files themselves are served from content-addressed URLs
(``/HomeSepe/dam/jcr:<uuid>/MUNI_<PROVINCE>_<mmyy>.xls`` for recent months, or a
stable ``/SiteSepe/contenidos/.../MUNI_<PROVINCE>_<mmyy>.xls`` path for archived
ones) that change shape release to release, so this adapter walks the listing
rather than guessing a path. Handily, ``municipios.html`` is not just a "latest
few months" page — it lists the *entire* archive back to 2005 in one document, so
discovery never needs to paginate: one fetch of the index yields every (year,
month) -> month-page link we could want, and we simply filter to the window we
care about.

Each province XLS carries two sheets — ``PARO`` (registered unemployment) and
``CONTRATOS`` (new contracts registered that month) — both keyed by five-digit INE
municipality code with a per-metric TOTAL column. Only rows matching one of our eight
``ine_mun`` codes are kept; everything else in the province (dozens of other towns)
is discarded in ``normalize``.

Both series are coincident-to-leading labour signals at the *municipality* level —
unlike the airport metrics in this batch, SEPE really does publish at the geography
CitySignal treats as ground truth for a city, so no metropolitan/city caveat applies
here. New contracts is the more forward-looking of the two: firms hire ahead of
confirmed demand and freeze hiring at the first sign of a slowdown, well before
registered unemployment (which reflects layoffs already made) starts to move.

Backfill is incremental so weekly runs stay cheap: on a run with existing history,
``discover()`` only asks for months outside what is already stored — any gap below
the earliest stored period (finishing an interrupted backfill), any new month above
the latest stored period, and a short trailing window re-checked for SEPE's routine
late revisions. An empty history (or ``--force``) walks the whole window back to
``BACKFILL_START``. Every province fetch is optional: one bad month or one bad
province XLS is noted and skipped, never fatal to the source.

One real defect worth documenting: a batch of 2026-05 province files carry a
single-byte-corrupted OLE2 header (byte offset 28 is ``0xFF`` where the "little-
endian" byte-order marker ``0xFE`` belongs), which makes xlrd raise
``CompDocError: Expected "little-endian" marker``. ``_read_xls_sheet`` recognises
that specific failure and retries once against a copy of the bytes with the marker
patched back to the canonical value — no data is invented, only a malformed
container-format byte is repaired so the real numbers underneath can be read.
"""

from __future__ import annotations

import io
import re
from typing import Iterable

import pandas as pd
import xlrd

from ..framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.history import history_path, read_history
from ..framework.record import CanonicalRecord, municipality

LISTING_URL = "https://www.sepe.es/HomeSepe/que-es-el-sepe/estadisticas/datos-estadisticos/municipios.html"

# How far back the backfill window reaches. The derive engine needs 24+ prior
# observations before it will score a measure, so a single recent month is
# invisible to the product; multi-year history is the whole point of this adapter.
BACKFILL_START = "2019-01"

MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12,
}

# SEPE's province file name token -> the INE province code of the one municipality
# we care about inside that province. All eight of our provinces publish under a
# single-word token matching this exactly.
PROVINCE_TOKENS: dict[str, str] = {
    "MADRID": "28",
    "BARCELONA": "08",
    "VALENCIA": "46",
    "MALAGA": "29",
    "SEVILLA": "41",
    "BALEARES": "07",
    "BIZKAIA": "48",
    "ZARAGOZA": "50",
}

# How many of the most recent available months to re-check every run, regardless
# of what is already stored. SEPE revises the prior month's figures as more
# contracts get registered late, so re-checking a couple of months back catches
# those without re-walking the whole archive. Content-hash skipping (framework-
# owned) keeps this nearly free when nothing has actually changed.
REVISION_RECHECK_MONTHS = 2

# Two URL shapes appear on the same index page: recent months use a bare
# "{month}.html" slug, archived ones use "{month}-{year}.html". Capture the full
# href so we never have to reconstruct — and potentially mis-guess — either shape.
_MONTH_LINK_RE = re.compile(
    r'href="(/HomeSepe/que-es-el-sepe/estadisticas/datos-estadisticos/municipios/'
    r'(\d{4})/([A-Za-zñÑ]+)(?:-\d{4})?\.html)"'
)
# Two path shapes for the province file itself: recent months are a content-
# addressed blob under /HomeSepe/dam/jcr:<uuid>/..., archived months sit at a
# stable /SiteSepe/contenidos/.../ path. Both end in MUNI_<PROVINCE>_<mmyy>.xls.
_PROVINCE_FILE_RE = re.compile(
    r'href="(/(?:HomeSepe/dam/jcr:[a-f0-9-]+|SiteSepe/contenidos/[^"]+)'
    r'/MUNI_([A-Z_]+)_\d{4}\.xls)"'
)

# xlrd's OLE2 reader expects the byte-order marker at this offset to read
# 0xFE 0xFF ("little-endian"). A batch of 2026-05 province files ship 0xFF in the
# first of those two bytes — a single-byte corruption in SEPE's export, not a
# real format change — which is repaired in-place before a retry.
_OLE2_BOM_OFFSET = 28


def _read_xls_sheet(content: bytes, sheet: str) -> pd.DataFrame:
    try:
        return pd.read_excel(io.BytesIO(content), sheet_name=sheet, header=None)
    except xlrd.compdoc.CompDocError as exc:
        if "little-endian" not in str(exc) or len(content) < _OLE2_BOM_OFFSET + 2:
            raise
        patched = bytearray(content)
        patched[_OLE2_BOM_OFFSET : _OLE2_BOM_OFFSET + 2] = b"\xfe\xff"
        return pd.read_excel(io.BytesIO(bytes(patched)), sheet_name=sheet, header=None)


class SepeAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="sepe",
        publisher="Servicio Público de Empleo Estatal",
        license="Reuse permitted (attribution)",
        attribution="Source: SEPE",
        docs_url=LISTING_URL,
        cadence="monthly",
        geo_level="municipality",
        max_age_days=75,
        formats=("xls",),
        redistribute=True,
        revisions_allowed=True,
        kind="official",
        notes="Registered unemployment and new contracts by municipality; SEPE revises recent months.",
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        index = ctx.fetcher.get(FetchPlan(url=LISTING_URL, fmt="html", label="sepe-index"))
        if index is None:
            raise AdapterFailure("sepe municipios.html index returned no content")

        # The index lists the entire archive back to 2005 in one document — no
        # pagination, no per-year fetch needed. (period, href), de-duplicated.
        months: dict[str, str] = {}
        for href, year_str, slug in _MONTH_LINK_RE.findall(index.text()):
            month_num = MESES_ES.get(slug.lower())
            if not month_num:
                continue
            period = f"{int(year_str):04d}-{month_num:02d}"
            if period >= BACKFILL_START:
                months[period] = href
        if not months:
            raise AdapterFailure("no year/month links found on sepe municipios.html — layout may have changed")

        target_periods = self._select_target_periods(ctx, months.keys())

        plans: list[FetchPlan] = []
        for period in sorted(months):
            if period not in target_periods:
                continue
            page_url = f"https://www.sepe.es{months[period]}"
            page = ctx.fetcher.get(FetchPlan(url=page_url, fmt="html", label=f"sepe-month:{period}"))
            if page is None:
                continue
            seen: set[str] = set()
            for href, token in _PROVINCE_FILE_RE.findall(page.text()):
                ine_prov = PROVINCE_TOKENS.get(token)
                if ine_prov is None or ine_prov in seen:
                    continue
                seen.add(ine_prov)
                plans.append(
                    FetchPlan(
                        url=f"https://www.sepe.es{href}",
                        fmt="xls",
                        label=f"sepe:{period}:{token}",
                        optional=True,
                        meta={"period": period, "province": token},
                    )
                )

        if not plans:
            raise AdapterFailure("no province XLS links resolved from sepe month pages")
        return plans

    def _select_target_periods(self, ctx: RunContext, available: Iterable[str]) -> set[str]:
        """Which of the available months to actually build fetch plans for.

        Empty history (or ``--force``) walks the full window — that is the initial
        backfill. Otherwise: fill any gap below what is already stored (in case a
        prior backfill was interrupted), pick up any new month published above the
        latest stored one, and re-check a short trailing window for late revisions.
        Everything else is already on disk and unchanged, so it is left out —
        that is what keeps weekly runs cheap.
        """
        available = sorted(available)
        if ctx.force:
            return set(available)

        stored_periods = {
            row["period"]
            for metric_id in ("unemployment_registered", "contracts_registered")
            for row in read_history(history_path(ctx.data_dir, self.manifest.source_id, metric_id))
        }
        if not stored_periods:
            return set(available)

        earliest_stored, latest_stored = min(stored_periods), max(stored_periods)
        recheck = set(available[-REVISION_RECHECK_MONTHS:])
        return {p for p in available if p < earliest_stored or p > latest_stored} | recheck

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        frames = []
        for sheet, metric_id in (("PARO", "unemployment_registered"), ("CONTRATOS", "contracts_registered")):
            sheet_df = _read_xls_sheet(payload.content, sheet)
            sub = sheet_df.iloc[:, [0, 2]].copy()
            sub.columns = ["ine_code", "total"]
            sub["metric_id"] = metric_id
            frames.append(sub)
        combined = pd.concat(frames, ignore_index=True)
        combined["ine_code"] = pd.to_numeric(combined["ine_code"], errors="coerce")
        combined["total"] = pd.to_numeric(combined["total"], errors="coerce")
        return combined.dropna(subset=["ine_code", "total"])

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        period = plan.meta["period"]
        target_codes = ctx.config.municipality_codes
        out: list[CanonicalRecord] = []
        for row in frame.itertuples(index=False):
            code = str(int(row.ine_code)).zfill(5)
            if code not in target_codes:
                continue
            unit = "persons" if row.metric_id == "unemployment_registered" else "contracts"
            out.append(
                CanonicalRecord(
                    metric_id=row.metric_id,
                    geo_id=municipality(code),
                    period=period,
                    value=float(row.total),
                    unit=unit,
                    source_id=self.manifest.source_id,
                )
            )
        return out
