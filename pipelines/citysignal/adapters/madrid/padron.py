"""Padrón municipal, histórico — monthly population by district.

datos.madrid.es publishes one dated CSV resource per month under the
"Padrón municipal. Histórico" dataset (209163), running from January 2014 to
the current month, each carrying every census section in the city broken down
by age and by Spanish/foreign, male/female. There is no stable per-month URL —
CKAN mints a new resource id for every monthly file — so ``discover`` reads
the dataset's own downloads page and follows the resource each month's
``data-key`` attribute names (e.g. ``2026-Julio``), rather than guessing a
URL pattern. That key is also the most reliable period source: the column set
has changed twice since 2014 (an ``FX_DATOS_INI`` snapshot-date column was
added in 2022, and the population columns were re-cased from ``EspanolesHombres``
to ``ESPANOLESHOMBRES`` at the same time), but the catalogue key is present
and correctly spelled for every month in the series.

Only the district code and the four population columns are used — this
adapter sums census-section rows up to district level and throws the rest
away. ``HISTORY_MONTHS`` bounds how many months are fetched per run; the derive
engine needs 24 to score a trend, so this defaults to three years, comfortably
past that floor without pulling all 150-odd months (~25MB each) on every run.
Once fetched, unchanged months are skipped by the framework's content-hash
check on subsequent runs, so the cost is mostly a one-time backfill.
"""

from __future__ import annotations

import io
import re
from typing import Iterable

import pandas as pd

from ...framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ...framework.fetch import FetchPlan, RawPayload
from ...framework.record import CanonicalRecord, district
from ._shared import decode_spanish_text, period_from_spanish_label

DOWNLOADS_URL = "https://datos.madrid.es/dataset/209163-0-padron-municipal-historico/downloads"

# Three years of monthly history: well past the 24-observation floor the
# derive engine needs, without re-pulling the full ~2014-present backfill
# (~150 files) on every run. Raise this once and the next run simply fetches
# the extra months — already-fetched ones are skipped by content hash.
HISTORY_MONTHS = 36

_POP_COLUMNS = ("ESPANOLESHOMBRES", "ESPANOLESMUJERES", "EXTRANJEROSHOMBRES", "EXTRANJEROSMUJERES")

_RESOURCE_RE = re.compile(
    r'data-resourceid="[^"]+" data-deepness="3" data-key="([^"]+)">(.*?)'
    r'(?=<div class="tracked-resource-item"|\Z)',
    re.S,
)
_CSV_HREF_RE = re.compile(r'href="([^"]+\.csv)"')


class MadridPadronAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="madrid_padron",
        publisher="Ayuntamiento de Madrid",
        license="Reuse permitted (attribution)",
        attribution="Source: Ayuntamiento de Madrid, datos.madrid.es",
        docs_url="https://datos.madrid.es/portal/site/egob",
        cadence="monthly",
        geo_level="district",
        max_age_days=75,
        formats=("csv",),
        kind="official",
        redistribute=True,
        expected_columns=("COD_DISTRITO", "DESC_DISTRITO", "COD_BARRIO"),
        min_rows=1000,
        notes=(
            "Padrón municipal, histórico: one dated CSV per month since January "
            "2014, summed from census-section to district level. The dataset's "
            "own downloads page is read each run to find the current set of "
            "monthly resource URLs (they are not predictable from the month alone)."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        listing_plan = FetchPlan(url=DOWNLOADS_URL, fmt="html", label="padron-historico-listing")
        try:
            payload = ctx.fetcher.get(listing_plan)
        except Exception as exc:  # noqa: BLE001
            raise AdapterFailure(f"could not fetch the padrón histórico downloads page: {exc}") from exc
        if payload is None:
            raise AdapterFailure("padrón histórico downloads page fetch returned no content")

        months = self._parse_listing(payload.text())
        if not months:
            raise AdapterFailure(
                "no monthly CSV resources found on the padrón histórico downloads page "
                "— the page layout may have changed"
            )

        # Newest first, capped to HISTORY_MONTHS; each month is an independent,
        # interchangeable fetch, so one dead link should not sink the others.
        selected = sorted(months.items(), key=lambda kv: kv[0], reverse=True)[:HISTORY_MONTHS]
        return [
            FetchPlan(url=url, fmt="csv", label=f"padron-{period}", optional=True, meta={"period": period})
            for period, url in selected
        ]

    @staticmethod
    def _parse_listing(html: str) -> dict[str, str]:
        months: dict[str, str] = {}
        for match in _RESOURCE_RE.finditer(html):
            label, block = match.groups()
            csv_match = _CSV_HREF_RE.search(block)
            if not csv_match:
                continue
            try:
                period = period_from_spanish_label(label)
            except (KeyError, ValueError):
                continue
            href = csv_match.group(1)
            url = href if href.startswith("http") else f"https://datos.madrid.es{href}"
            months[period] = url
        return months

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        text = decode_spanish_text(payload.content)
        frame = pd.read_csv(io.StringIO(text), sep=";", dtype=str)
        frame.columns = [c.strip().strip('"').upper() for c in frame.columns]
        missing = [c for c in ("COD_DISTRITO", *_POP_COLUMNS) if c not in frame.columns]
        if missing:
            raise AdapterFailure(f"{payload.plan.label}: missing expected columns {missing}")
        return frame

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        city = ctx.config.city("madrid")
        period = plan.meta["period"]

        working = frame.copy()
        working["COD_DISTRITO"] = working["COD_DISTRITO"].astype(str).str.strip().str.strip('"')
        for col in _POP_COLUMNS:
            working[col] = pd.to_numeric(working[col].astype(str).str.strip().str.strip('"'), errors="coerce").fillna(0)
        working["_total"] = working[list(_POP_COLUMNS)].sum(axis=1)

        by_district = working.groupby("COD_DISTRITO")["_total"].sum()

        out: list[CanonicalRecord] = []
        for code_raw, total in by_district.items():
            if not code_raw or not code_raw.isdigit():
                continue
            code = int(code_raw)
            if not 1 <= code <= 21:
                continue
            out.append(
                CanonicalRecord(
                    metric_id="padron_district",
                    geo_id=district(city.ine_mun, code),
                    period=period,
                    value=float(total),
                    unit="persons",
                    source_id=self.manifest.source_id,
                )
            )
        if not out:
            raise AdapterFailure(f"{plan.label}: produced zero district rows")
        return out
