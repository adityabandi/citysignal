"""MIVAU — the only genuinely housing-market source in the whole pipeline.

The Ministerio de Vivienda y Agenda Urbana publishes six statistical series as
flat CSVs on its own CDN, each catalogued on datos.gob.es under publisher code
``e05233601``. The catalogue's JSON API
(``https://datos.gob.es/apidata/catalog/dataset/<slug>.json``) is the preferred
way to resolve a slug to its current ``accessURL`` — that is what ``discover``
tries first. Two of the six catalogue pages this adapter was briefed against
(rent reference, dwelling stock) currently 404 on datos.gob.es, evidently mid
some 2026 portal reshuffle, while the other four resolve fine and hand back
CDN URLs of the form ``.../Datos_MIVAU/CSV/VDP0NN_01.csv``. Those two missing
slugs were confirmed by hand to sit at the same CDN under the same naming
convention (``VDP001_01.csv`` for rent, ``VDP002_01.csv`` for dwelling stock —
content structurally verified against MIVAU's own published methodology notes
for SERPAVI and "Estimación del parque de viviendas"), so ``discover`` falls
back to those fixed URLs when the catalogue lookup comes back empty. This
mirrors how INE table ids are hardcoded elsewhere in this codebase: a stable
identifier, verified by hand, used as a fallback rather than a first resort.

Files are UTF-8 (with a BOM) and semicolon-delimited, not the latin-1 the rest
of the Spanish open-data ecosystem favours — verified directly rather than
assumed. Every geographic column is read as a plain string and reduced to an
INE code by hand (``COD_POSTAL``/``CPRO`` are catastro-style codes that lose
their leading zero under naive numeric parsing), then matched against the
eight cities' own codes. Nothing is matched on a rendered place name.

Six declared metrics, five actually shipped:

- **rent_reference** (municipal, matches the registry). SERPAVI's own file has
  no per-m² price column — it publishes a median *total* monthly rent
  (``ELEMENTO=PRECIO``) and a median surface (``ELEMENTO=SUPERFICIE``) side by
  side, for ``COLECTIVA`` (multi-unit, i.e. apartment-block) dwellings. This
  adapter divides the two itself, the same way it would divide two officially
  published components anywhere else in this codebase. Verified against Madrid
  2024: 868.8 / 66 ≈ 13.2 €/m²/month — the right order of magnitude.
- **dwelling_stock** — ⚠️ **geography mismatch**. metrics.yml declares this
  ``municipality``, but "Estimación del parque de viviendas" is published by
  MIVAU at province/CCAA level only; there is no municipal breakdown in the
  file (confirmed: 52 provincial rows, no finer geography column at all).
  Rather than promote a provincial figure to a city's geo_id — the one thing
  this project's geography discipline exists to prevent — this adapter emits
  it honestly under ``prov-NN``. Until the registry is changed to
  ``geo_level: province`` (or a municipal source is found), the per-city
  lookup for `dwelling_stock` and every `per_1000_dwellings` transform that
  depends on it will not find these rows; they still land in history for when
  that's fixed.
- **housing_transactions** — **not shipped**. "Transacciones inmobiliarias de
  vivienda" (VDP003) is province-level, and worse: its transaction-*count*
  column (``Numero_Transacciones``) is populated for exactly one of its four
  housing-type categories nationwide — "Protegida Nueva" (subsidised new
  build) — and is 100% null, in every province and every quarter since 2004,
  for "Nueva" (free-market new build), "Segunda Mano" and "Protegida Segunda
  Mano". The free-market "Nueva" rows carry a monetary total instead
  (``Valor_Transacciones``, euros) with no count to go with it. There is no
  way to build an overall transaction-volume figure — the thing the metric
  name promises — out of what the file actually contains; shipping the
  "Protegida Nueva" count alone under the `housing_transactions` label would
  misrepresent a few dozen subsidised sales a quarter as market-wide volume.
  INE's `property_transfers` (already adapted, province, monthly) is the
  correct existing proxy for transaction volume; this metric is left dark
  here rather than filled with a number that doesn't mean what it says.
- **appraised_value** (province, matches). "Valor tasado de la Vivienda",
  ``Régimen=Libre`` (open-market, excluding subsidised housing) — the segment
  that tracks market conditions. Verified against Madrid: ~3.0k €/m² in 2023
  rising to ~4.0k €/m² by early 2026, consistent with reporting on the period.
- **land_price** (province, matches). "Precio Medio del Suelo",
  ``Tipo="PMS Mayor 50000 hab"`` — land prices for municipalities over 50,000
  inhabitants, the bracket every one of the eight cities sits in.
- **housing_starts** / **housing_completions** (province, matches). "Número de
  Viviendas Iniciadas y Terminadas por Régimen de Protección" is monthly and
  splits every observation by ``Tipo_de_Vivienda`` (``Libre``/``Protegida``);
  both regimes are summed to a single total, and three months are summed to
  the quarter the registry declares.
"""

from __future__ import annotations

import io
import logging
import unicodedata
from typing import Any, Iterable

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.record import CanonicalRecord, municipality, province

log = logging.getLogger(__name__)

CATALOG_JSON = "https://datos.gob.es/apidata/catalog/dataset/e05233601-{slug}.json"
CDN_CSV = "https://cdn.mivau.gob.es/portal-web-mivau/Datos_MIVAU/CSV/{code}_01.csv"

# (kind, datos.gob.es slug, MIVAU CDN code, FetchPlan label). CDN codes verified
# by hand with curl — see module docstring — and used only when the catalogue
# lookup for that slug fails.
DATASETS: tuple[tuple[str, str, str, str], ...] = (
    ("rent", "referencia-del-precio-del-alquiler-de-vivienda", "VDP001", "rent-reference"),
    ("stock", "estimacion-del-parque-de-viviendas", "VDP002", "dwelling-stock"),
    ("land", "precio-medio-del-suelo", "VDP004", "land-price"),
    (
        "starts_completions",
        "numero-de-viviendas-iniciadas-y-terminadas-por-regimen-de-proteccion",
        "VDP005",
        "starts-completions",
    ),
    ("appraised", "valor-tasado-de-la-vivienda", "VDP006", "appraised-value"),
)


def _num(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("nan", "null", "none", "_"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _mun_code(value: Any) -> str | None:
    n = _num(value)
    return None if n is None else f"{int(n):05d}"


def _prov_code(value: Any) -> str | None:
    n = _num(value)
    return None if n is None else f"{int(n):02d}"


def _clean_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Strip accents and case from headers so every handler can use plain ASCII."""
    frame = frame.copy()
    frame.columns = [
        unicodedata.normalize("NFKD", str(c)).encode("ascii", "ignore").decode("ascii").strip().upper()
        for c in frame.columns
    ]
    return frame


class MivauAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="mivau",
        publisher="Ministerio de Vivienda y Agenda Urbana",
        license="Reuse permitted (datos.gob.es, attribution)",
        attribution="Source: MIVAU / datos.gob.es",
        docs_url="https://www.mivau.gob.es/vivienda/estadisticas",
        cadence="quarterly",
        geo_level="municipality",
        max_age_days=270,
        formats=("csv",),
        kind="official",
        min_rows=2,
        notes=(
            "Six MIVAU CSV bulk files resolved via datos.gob.es's catalogue API where "
            "possible. rent_reference is genuinely municipal; appraised_value, "
            "land_price, housing_starts and housing_completions are genuinely "
            "province-level, matching the registry. dwelling_stock is registered as "
            "municipality but MIVAU only publishes it by province — emitted honestly "
            "at prov-NN rather than promoted (see module docstring). "
            "housing_transactions is not shipped: its transaction-count column is "
            "populated for one minor subsidised-housing category only and cannot "
            "represent overall volume."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        return [
            FetchPlan(
                url=self._resolve_url(ctx, slug, code),
                fmt="csv",
                label=label,
                optional=True,
                meta={"kind": kind},
            )
            for kind, slug, code, label in DATASETS
        ]

    def _resolve_url(self, ctx: RunContext, slug: str, fallback_code: str) -> str:
        """Prefer the datos.gob.es catalogue's current accessURL; fall back to the
        verified CDN path when the catalogue entry is missing or unparseable."""
        fallback = CDN_CSV.format(code=fallback_code)
        try:
            payload = ctx.fetcher.get(
                FetchPlan(url=CATALOG_JSON.format(slug=slug), fmt="json", label=f"catalog:{slug}")
            )
        except Exception as exc:  # noqa: BLE001
            log.info("mivau: catalogue lookup failed for %s (%s); using verified CDN url", slug, exc)
            return fallback
        if payload is None:
            return fallback
        try:
            data = payload.json()
            items = ((data.get("result") or {}).get("items")) or []
            for item in items:
                dist = item.get("distribution")
                candidates = dist if isinstance(dist, list) else [dist] if dist else []
                for entry in candidates:
                    access = entry.get("accessURL", "") or ""
                    fmt = entry.get("format", "")
                    if isinstance(fmt, dict):
                        fmt = fmt.get("value", "")
                    if access.lower().endswith(".csv") or "csv" in str(fmt).lower():
                        return access
        except Exception as exc:  # noqa: BLE001
            log.info("mivau: could not parse catalogue JSON for %s (%s); using verified CDN url", slug, exc)
        return fallback

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        text = payload.content.decode("utf-8-sig")
        frame = pd.read_csv(io.StringIO(text), sep=";", dtype=str)
        if frame.empty:
            raise AdapterFailure(f"{payload.plan.label}: parsed zero rows")
        return _clean_columns(frame)

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        handler = getattr(self, f"_normalize_{plan.meta['kind']}")
        return handler(frame, ctx)

    def _record(self, **kwargs: Any) -> CanonicalRecord:
        return CanonicalRecord(source_id=self.manifest.source_id, **kwargs)

    # ---- per-file handlers -------------------------------------------------

    def _normalize_rent(self, frame: pd.DataFrame, ctx: RunContext) -> Iterable[CanonicalRecord]:
        mun_codes = ctx.config.municipality_codes
        staged: dict[tuple[str, str], dict[str, float]] = {}
        for row in frame.itertuples(index=False):
            if row.TIPO_VIVIENDA != "COLECTIVA" or row.TIPO_MEDIDA != "MEDIANA":
                continue
            if row.ELEMENTO not in ("PRECIO", "SUPERFICIE"):
                continue
            mun_code = _mun_code(row.COD_POSTAL)
            if mun_code not in mun_codes:
                continue
            value = _num(row.VALOR)
            year = _num(row.ANO)
            if value is None or year is None:
                continue
            staged.setdefault((mun_code, f"{int(year):04d}"), {})[row.ELEMENTO] = value

        out: list[CanonicalRecord] = []
        for (mun_code, period), parts in staged.items():
            precio = parts.get("PRECIO")
            superficie = parts.get("SUPERFICIE")
            if not precio or not superficie or superficie <= 0:
                continue
            out.append(
                self._record(
                    metric_id="rent_reference",
                    geo_id=municipality(mun_code),
                    period=period,
                    value=precio / superficie,
                    unit="eur_m2_month",
                )
            )
        return out

    def _normalize_stock(self, frame: pd.DataFrame, ctx: RunContext) -> Iterable[CanonicalRecord]:
        # Province-only in the source data — see module docstring. geo_id is
        # deliberately prov-NN, not a promoted municipality code.
        prov_codes = ctx.config.province_codes
        out: list[CanonicalRecord] = []
        for row in frame.itertuples(index=False):
            if row.TIPO_VIVIENDA != "Viviendas_Totales":
                continue
            prov_code = _prov_code(row.CPRO)
            if prov_code not in prov_codes:
                continue
            value = _num(row.VALOR)
            year = _num(row.ANO)
            if value is None or year is None:
                continue
            out.append(
                self._record(
                    metric_id="dwelling_stock",
                    geo_id=province(prov_code),
                    period=f"{int(year):04d}",
                    value=value,
                    unit="dwellings",
                )
            )
        return out

    def _normalize_land(self, frame: pd.DataFrame, ctx: RunContext) -> Iterable[CanonicalRecord]:
        prov_codes = ctx.config.province_codes
        out: list[CanonicalRecord] = []
        for row in frame.itertuples(index=False):
            if row.TIPO != "PMS Mayor 50000 hab":
                continue
            prov_code = _prov_code(row.CPRO)
            if prov_code not in prov_codes:
                continue
            value = _num(row.VALOR)
            year = _num(row.ANO)
            quarter = _num(row.TRIMESTRE)
            if value is None or year is None or quarter is None:
                continue
            out.append(
                self._record(
                    metric_id="land_price",
                    geo_id=province(prov_code),
                    period=f"{int(year):04d}-Q{int(quarter)}",
                    value=value,
                    unit="eur_m2",
                )
            )
        return out

    def _normalize_appraised(self, frame: pd.DataFrame, ctx: RunContext) -> Iterable[CanonicalRecord]:
        prov_codes = ctx.config.province_codes
        out: list[CanonicalRecord] = []
        for row in frame.itertuples(index=False):
            if row.REGIMEN != "Libre":
                continue
            prov_code = _prov_code(row.CPRO)
            if prov_code not in prov_codes:
                continue
            value = _num(row.VALOR)
            year = _num(row.ANO)
            quarter = _num(row.TRIMESTRE)
            if value is None or year is None or quarter is None:
                continue
            out.append(
                self._record(
                    metric_id="appraised_value",
                    geo_id=province(prov_code),
                    period=f"{int(year):04d}-Q{int(quarter)}",
                    value=value,
                    unit="eur_m2",
                )
            )
        return out

    def _normalize_starts_completions(
        self, frame: pd.DataFrame, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        prov_codes = ctx.config.province_codes
        metric_by_estado = {"Iniciadas": "housing_starts", "Terminadas": "housing_completions"}
        # Sum both protection regimes (Libre + Protegida) and all months in the
        # quarter — the registry declares this metric quarterly, the file is monthly.
        totals: dict[tuple[str, str, str], float] = {}
        for row in frame.itertuples(index=False):
            metric_id = metric_by_estado.get(row.ESTADO)
            if metric_id is None:
                continue
            prov_code = _prov_code(row.CPRO)
            if prov_code not in prov_codes:
                continue
            value = _num(row.VALOR)
            year = _num(row.ANO)
            month = _num(row.MES_NUMERO)
            if value is None or year is None or month is None:
                continue
            quarter = (int(month) - 1) // 3 + 1
            key = (metric_id, prov_code, f"{int(year):04d}-Q{quarter}")
            totals[key] = totals.get(key, 0.0) + value

        out: list[CanonicalRecord] = []
        for (metric_id, prov_code, period), value in totals.items():
            out.append(
                self._record(
                    metric_id=metric_id,
                    geo_id=province(prov_code),
                    period=period,
                    value=value,
                    unit="dwellings",
                )
            )
        return out
