"""SEPE registered unemployment and new contracts, by municipality.

SEPE publishes "Paro registrado y contratos por municipios" as one XLS per province
per month, linked from a page that is organised year -> month -> per-province file.
The province files themselves are served from content-addressed URLs
(``/HomeSepe/dam/jcr:<uuid>/MUNI_<PROVINCE>_<mmyy>.xls``) that change every month, so
this adapter walks the listing rather than guessing a path: fetch the top
``municipios.html`` index for the (year, month) links, fetch the most recent couple
of month pages, and read off the province file for each of our eight provinces by
name.

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
"""

from __future__ import annotations

import io
import re
from typing import Iterable

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.record import CanonicalRecord, municipality

LISTING_URL = "https://www.sepe.es/HomeSepe/que-es-el-sepe/estadisticas/datos-estadisticos/municipios.html"
MONTH_PAGE = (
    "https://www.sepe.es/HomeSepe/que-es-el-sepe/estadisticas/"
    "datos-estadisticos/municipios/{year}/{month_slug}.html"
)

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

# How many of the most recent listed months to pull each run. SEPE revises the
# prior month's figures as more contracts get registered late, so re-checking a
# couple of months back catches those without re-walking the whole archive.
MONTHS_TO_FETCH = 2

_MONTH_LINK_RE = re.compile(
    r"/HomeSepe/que-es-el-sepe/estadisticas/datos-estadisticos/municipios/(\d{4})/([A-Za-zñÑ]+)\.html"
)
_PROVINCE_FILE_RE = re.compile(r'href="(/HomeSepe/dam/jcr:[a-f0-9-]+/MUNI_([A-Z_]+)_\d{4}\.xls)"')


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

        months: list[tuple[int, int, str]] = []  # (year, month_num, month_slug)
        for year_str, slug in _MONTH_LINK_RE.findall(index.text()):
            month_num = MESES_ES.get(slug.lower())
            if month_num:
                months.append((int(year_str), month_num, slug))
        if not months:
            raise AdapterFailure("no year/month links found on sepe municipios.html — layout may have changed")

        months.sort(reverse=True)
        plans: list[FetchPlan] = []
        for year, month_num, slug in months[:MONTHS_TO_FETCH]:
            page_url = MONTH_PAGE.format(year=year, month_slug=slug)
            page = ctx.fetcher.get(FetchPlan(url=page_url, fmt="html", label=f"sepe-month:{year}-{month_num:02d}"))
            if page is None:
                continue
            period = f"{year:04d}-{month_num:02d}"
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

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        frames = []
        for sheet, metric_id in (("PARO", "unemployment_registered"), ("CONTRATOS", "contracts_registered")):
            sheet_df = pd.read_excel(io.BytesIO(payload.content), sheet_name=sheet, header=None)
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
