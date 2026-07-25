"""Banco de España — the policy-rate side of the credit cycle.

BdE's own statistics portal (datos.bde.es) fronts a JSON API
(``app.bde.es/bierest/resources/srdatosapp``), but its "latest value" and
"list series" endpoints are built for pulling a handful of named series, not
for bulk history. The Boletín Estadístico's own CSV exports
(``bde.es/webbe/es/estadisticas/compartido/datos/csv/<table>.csv``) give the
same series with their full history in one request, so this adapter reads
those directly — one file per table, three named series across them.

The CSV layout is BdE's own, not a generic export: six metadata rows
(``CÓDIGO DE LA SERIE`` / ``NÚMERO SECUENCIAL`` / ``ALIAS DE LA SERIE`` /
``DESCRIPCIÓN DE LA SERIE`` / ``DESCRIPCIÓN DE LAS UNIDADES`` / ``FRECUENCIA``)
carrying one column per series, each holding a human-readable Spanish
description — no stable machine-readable series id survives from the API to
the CSV, so series are matched on their exact published description string,
verified by hand against the downloaded files (the same discipline as CGPJ's
verbatim sheet names). Data rows follow, keyed by a Spanish-month label
("ENE 1999"), missing observations marked ``_``, and the file ends with
``FUENTE``/``NOTAS`` rows that are not data and are dropped. The encoding is
latin-1 (cp1252-compatible) and the delimiter is a comma, not the semicolon
common elsewhere in this pipeline — both confirmed against the live files
rather than assumed.

Three tables, three series:

- **euribor_12m** — table 19.1 (``be1901.csv``), "Tipo de interés. UEM.
  Mercado monetario. Euríbor. A 12 meses". Monthly, published by EMMI, and it
  is the *monthly average* series (unlike the chapter's companion daily
  table), so it lines up with the registry's monthly cadence with no
  aggregation needed. History runs from Jan 1999, Euribor's launch — verified:
  3.06% at launch, negative through 2019-2021 (-0.25% Jan 2020, -0.51% Jan
  2021), spiking to 4.16% in Oct 2023, easing to ~2.8% by mid-2026.
- **mortgage_rate_new** — table 19.4 (``be1904.csv``), "Tipo de interés.
  Nuevas operaciones. EC y EFC. TEDR. Hogares e ISFLSH. Crédito a la vivienda.
  Tipo medio ponderado" — the weighted-average TEDR (effective rate) on new
  house-purchase loans to households, across all maturities and both fixed
  and variable business. Monthly since Jan 2003.
- **npl_ratio** — BdE does not publish the system-wide doubtful-loan ratio as
  its own series; it publishes the two components. Table 4.13
  (``be0413.csv``) carries both total credit to other resident sectors
  ("Créditos a OSR por finalidades. Total para financiar actividades
  productivas y detalle por funciones de gasto. Total" — deliberately matched
  by its exact trailing ". Total", since two sibling columns for ISFLSH-only
  and unclassified-only credit share most of the same description) and total
  doubtful credit ("Créditos dudosos a OSR por finalidades..."). This adapter
  divides the two itself, the same way it divides MIVAU's rent components.
  Verified against two well-known checkpoints: Dec 2013 works out to 13.6% —
  the historical peak of the Spanish banking crisis — and Dec 2025 to 2.7%,
  matching contemporary reporting of multi-year lows. Doubtful-credit data
  starts Dec 1998.

All three are ``geo_level: nation`` (``NATION`` = "es"), matching the
registry — nothing here is a city or province figure.
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any, Iterable

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.record import CanonicalRecord, NATION

log = logging.getLogger(__name__)

BASE_URL = "https://www.bde.es/webbe/es/estadisticas/compartido/datos/csv"

# Table codes, verified by hand against the live CSV exports before being
# wired in here (the same discipline as INE's Tempus3 table ids).
TBL_EURIBOR = "be1901"  # 19.1: tipos de interés legales, euríbor, mibor y otros oficiales
TBL_MORTGAGE_RATE = "be1904"  # 19.4: TEDR de nuevas operaciones, hogares — crédito a la vivienda
TBL_CREDIT_DUDOSO = "be0413"  # 4.13: crédito y crédito dudoso a OSR por finalidades

_MONTHS_ES = {
    "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12,
}

# Exact published descriptions, matched verbatim — see module docstring.
DESC_EURIBOR_12M = "Tipo de interés. UEM. Mercado monetario. Euríbor. A 12 meses"
DESC_MORTGAGE_RATE_NEW = (
    "Tipo de interés. Nuevas operaciones. EC y EFC. TEDR. Hogares e ISFLSH. "
    "Crédito a la vivienda. Tipo medio ponderado"
)
DESC_CREDIT_TOTAL = (
    "EC y EFC. Créditos a OSR por finalidades. Total para financiar actividades "
    "productivas y detalle por funciones de gasto. Total"
)
DESC_CREDIT_DUDOSO = (
    "EC y EFC. Créditos dudosos a OSR por finalidades. Total para financiar "
    "actividades productivas y detalle funciones gasto"
)


def _parse_period(label: Any) -> str | None:
    text = str(label or "").strip().upper()
    parts = text.split()
    if len(parts) != 2:
        return None
    month_abbr, year = parts
    month = _MONTHS_ES.get(month_abbr)
    if month is None or not year.isdigit():
        return None
    return f"{int(year):04d}-{month:02d}"


def _parse_value(raw: Any) -> float | None:
    text = str(raw or "").strip()
    if not text or text == "_":
        return None
    try:
        return float(text)
    except ValueError:
        return None


class BdeAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="bde",
        publisher="Banco de España",
        license="Reuse permitted (attribution)",
        attribution="Source: Banco de España",
        docs_url="https://www.bde.es/webbe/es/estadisticas/recursos/api-estadisticas-bde.html",
        cadence="monthly",
        geo_level="nation",
        max_age_days=75,
        formats=("csv",),
        kind="official",
        min_rows=8,
        notes=(
            "Three monthly national series read from the Boletín Estadístico's own "
            "CSV exports: euríbor a 12 meses (table 19.1), the weighted-average TEDR "
            "on new household house-purchase loans (table 19.4), and the doubtful-"
            "loan ratio computed from total and doubtful credit to other resident "
            "sectors (table 4.13, BdE does not publish the ratio itself)."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        def plan(table_id: str, label: str, kind: str) -> FetchPlan:
            return FetchPlan(
                url=f"{BASE_URL}/{table_id}.csv",
                fmt="csv",
                label=label,
                # Three independent tables; one broken download must not sink the
                # other two.
                optional=True,
                meta={"kind": kind},
            )

        return [
            plan(TBL_EURIBOR, "euribor-12m", "euribor"),
            plan(TBL_MORTGAGE_RATE, "mortgage-rate-new", "mortgage_rate"),
            plan(TBL_CREDIT_DUDOSO, "credito-dudoso-osr", "npl"),
        ]

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        text = payload.content.decode("latin-1")
        rows = list(csv.reader(io.StringIO(text)))
        if len(rows) < 8:
            raise AdapterFailure(f"{payload.plan.label}: too few rows for a BdE series export")

        desc_row, units_row, freq_row = rows[3], rows[4], rows[5]
        if not desc_row or desc_row[0].strip() != "DESCRIPCIÓN DE LA SERIE":
            raise AdapterFailure(f"{payload.plan.label}: unexpected header row 4 — BdE layout may have changed")
        if not freq_row or freq_row[0].strip() != "FRECUENCIA":
            raise AdapterFailure(f"{payload.plan.label}: unexpected header row 6 — BdE layout may have changed")
        frequencies = {f.strip() for f in freq_row[1:] if f.strip()}
        if frequencies - {"MENSUAL"}:
            raise AdapterFailure(f"{payload.plan.label}: expected only monthly series, found {frequencies}")

        columns = ["PERIODO"] + [d.strip() for d in desc_row[1:]]
        width = len(columns)
        data: list[list[str]] = []
        for row in rows[6:]:
            if not row or not row[0].strip():
                continue
            label = row[0].strip()
            if label in ("FUENTE", "NOTAS"):
                continue
            cells = (row + [""] * width)[:width]
            data.append(cells)

        if not data:
            raise AdapterFailure(f"{payload.plan.label}: no data rows parsed")
        return pd.DataFrame(data, columns=columns)

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        handler = getattr(self, f"_normalize_{plan.meta['kind']}")
        return handler(frame)

    def _record(self, **kwargs: Any) -> CanonicalRecord:
        return CanonicalRecord(source_id=self.manifest.source_id, **kwargs)

    def _simple_series(
        self, frame: pd.DataFrame, column: str, *, metric_id: str
    ) -> Iterable[CanonicalRecord]:
        if column not in frame.columns:
            raise AdapterFailure(
                f"{metric_id}: expected column {column!r} not found in BdE export — table layout may have changed"
            )
        out: list[CanonicalRecord] = []
        for period_label, raw_value in zip(frame["PERIODO"], frame[column]):
            period = _parse_period(period_label)
            value = _parse_value(raw_value)
            if period is None or value is None:
                continue
            out.append(self._record(metric_id=metric_id, geo_id=NATION, period=period, value=value, unit="percent"))
        return out

    def _normalize_euribor(self, frame: pd.DataFrame) -> Iterable[CanonicalRecord]:
        return self._simple_series(frame, DESC_EURIBOR_12M, metric_id="euribor_12m")

    def _normalize_mortgage_rate(self, frame: pd.DataFrame) -> Iterable[CanonicalRecord]:
        return self._simple_series(frame, DESC_MORTGAGE_RATE_NEW, metric_id="mortgage_rate_new")

    def _normalize_npl(self, frame: pd.DataFrame) -> Iterable[CanonicalRecord]:
        for column in (DESC_CREDIT_TOTAL, DESC_CREDIT_DUDOSO):
            if column not in frame.columns:
                raise AdapterFailure(
                    f"npl_ratio: expected column {column!r} not found in BdE export — table layout may have changed"
                )
        out: list[CanonicalRecord] = []
        for period_label, total_raw, dudoso_raw in zip(
            frame["PERIODO"], frame[DESC_CREDIT_TOTAL], frame[DESC_CREDIT_DUDOSO]
        ):
            period = _parse_period(period_label)
            total = _parse_value(total_raw)
            dudoso = _parse_value(dudoso_raw)
            if period is None or total is None or dudoso is None or total <= 0:
                continue
            out.append(
                self._record(
                    metric_id="npl_ratio",
                    geo_id=NATION,
                    period=period,
                    value=dudoso / total * 100.0,
                    unit="percent",
                )
            )
        return out
