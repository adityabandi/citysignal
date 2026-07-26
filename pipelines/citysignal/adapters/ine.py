"""INE Tempus3 — the only source with a legal mandate to count everyone.

INE's public JSON API (Tempus3) exposes every INEbase table as a flat list of
named series, each carrying a ``MetaData`` array of classification dimensions
(geography, concept, sex, data type...) alongside its data points. The catch is
that ``Nombre`` — the human-readable label — is a display string assembled from
those same dimensions in table-specific order, and it is not always unique: two
different series can render to the identical ``Nombre`` string when a province
and a municipality share a name (Madrid, Barcelona, Málaga, Sevilla and Zaragoza
all do). Splitting a table's rows into "Total nacional, comunidades autónomas y
provincias" and "Municipios" halves does not save you either, because some tables
mix both scopes under one id. Requesting ``det=2&tip=AM`` returns the full
``MetaData`` block per series — each dimension tagged with both a ``Variable``
name and, for the geography dimension, INE's own numeric code — so filtering by
``Variable.Nombre == "Municipios"`` and ``Codigo == city.ine_mun`` is exact,
where matching on the rendered ``Nombre`` string would silently cross provincial
and municipal series for five of the eight cities. That correctness costs a
bigger payload; for the two nationwide municipality tables (padrón, tourist
dwellings) the fetch is trimmed to a handful of periods to keep it inside the
fetcher's size cap.

One exception: hotel statistics (EOH) are published by "punto turístico", INE's
own tourism-geography classification, which does not consistently reuse the
municipality's INE code — Madrid's tourist-point code is the ad-hoc "G2", not
"28079". There the join has to run on the point's display name, which was
verified empirically to equal the city's ``name`` field exactly for all eight
cities across all four hotel tables used here.

Two further mechanics worth naming:

- **Travellers and nights are two SUM'd components, not two rows.** The EOH
  travellers/nights table splits every point into "residents in Spain" and
  "residents abroad". INE does not publish the total as its own series, so
  ``normalize`` groups by (city, concept, period) and adds the two residency
  splits itself, within one table fetch — no cross-plan staging required.
- **Tourist dwellings (VTE) is a biannual snapshot dressed as monthly.** INE
  tags each VTE observation with a real calendar month (May, November) even
  though the underlying survey only runs twice a year. ``tourist_dwellings``
  is declared quarterly in the metric registry, so each snapshot is folded into
  the calendar quarter its month falls in (May → Q2, November → Q4) rather than
  invented as a false monthly cadence.

What was investigated and dropped: population_foreign_share and net_migration
have no clean home in Tempus3 at municipality level. The padrón table (DPOP,
id 29005) only carries a sex breakdown, not nationality; the nationality-split
population tables live under "Estadística Continua de Población" (ECP), whose
municipality-level series either don't exist or weren't findable through the
public table index without guessing IDs, which this adapter's brief explicitly
rules out. Both metrics are left for a future adapter once a verified table id
is found, rather than shipped from a guess.
"""

from __future__ import annotations

from typing import Any, Iterable

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.record import CanonicalRecord, ccaa, municipality, province

BASE_URL = "https://servicios.ine.es/wstempus/js/ES/DATOS_TABLA"

# Table ids, each verified by hand with curl before being wired in here — see the
# module docstring's companion research notes in the adapter's commit message.
TBL_PADRON = "29005"  # DPOP: Cifras oficiales del padrón por municipio (annual)
TBL_HOTEL_TRAVELLERS_NIGHTS = "2078"  # Viajeros y pernoctaciones por ciudad y residencia (monthly, 2005-06+)
TBL_HOTEL_OCCUPANCY = "75198"  # EOH: Establecimientos... grados de ocupación por puntos turísticos (monthly)
TBL_HOTEL_ADR = "46298"  # IRSH: Tarifa media diaria (ADR) por puntos turísticos (monthly)
TBL_HOTEL_REVPAR = "46301"  # IRSH: Ingresos por habitación disponible (RevPAR) por puntos turísticos (monthly)
TBL_MORTGAGES = "76317"  # HPT: Hipotecas constituidas, resultados por provincias, base nueva (monthly)
TBL_PROPERTY_TRANSFERS = "6149"  # ETDP: Viviendas transmitidas según título de adquisición, por provincias (monthly)
TBL_HOUSE_PRICE_INDEX = "80270"  # IPV: Índices por CCAA, trimestrales
TBL_TOURIST_DWELLINGS = "39363"  # VTE: Viviendas turísticas, plazas y plazas/vivienda por municipios (biannual)
TBL_BIRTHS = "6524"  # MNP: Nacimientos por lugar de residencia de la madre (monthly, from 1975)
TBL_MARRIAGES = "6540"  # MNP: Matrimonios por lugar de residencia (monthly, from 1975)


class IneAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="ine",
        publisher="Instituto Nacional de Estadística",
        license="INE reuse conditions (attribution)",
        attribution="Source: INE (ine.es)",
        docs_url="https://ine.es/dyngs/DAB/index.htm?cid=1100",
        cadence="monthly",
        geo_level="municipality",
        max_age_days=120,
        formats=("json",),
        kind="official",
        revisions_allowed=True,
        notes=(
            "Eleven Tempus3 tables spanning population, hotels, mortgages, property "
            "transfers, house prices, tourist dwellings, births, and marriages. "
            "Geography is matched on INE's own classification codes wherever the table "
            "exposes them; hotel tables key off the tourist-point display name instead "
            "(see module docstring)."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        def plan(table_id: str, label: str, nult: int, kind: str) -> FetchPlan:
            return FetchPlan(
                url=f"{BASE_URL}/{table_id}",
                fmt="json",
                label=label,
                params={"nult": nult, "det": 2, "tip": "AM"},
                # Nine independent tables; a single dead id must not sink the
                # other eight, so every plan is interchangeable-optional.
                optional=True,
                meta={"kind": kind},
            )

        return [
            plan(TBL_PADRON, "padron", 6, "padron"),
            plan(TBL_HOTEL_TRAVELLERS_NIGHTS, "hotel_travellers_nights", 250, "hotel_tn"),
            plan(TBL_HOTEL_OCCUPANCY, "hotel_occupancy", 250, "hotel_occ"),
            plan(TBL_HOTEL_ADR, "hotel_adr", 250, "hotel_adr"),
            plan(TBL_HOTEL_REVPAR, "hotel_revpar", 250, "hotel_revpar"),
            plan(TBL_MORTGAGES, "mortgages", 240, "mortgages"),
            plan(TBL_PROPERTY_TRANSFERS, "property_transfers", 240, "transfers"),
            plan(TBL_HOUSE_PRICE_INDEX, "house_price_index", 20, "hpi"),
            plan(TBL_TOURIST_DWELLINGS, "tourist_dwellings", 8, "vte"),
            plan(TBL_BIRTHS, "births", 600, "births"),
            plan(TBL_MARRIAGES, "marriages", 600, "marriages"),
        ]

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        items = payload.json()
        if not items:
            raise AdapterFailure(f"no series returned for {payload.plan.label}")

        rows: list[dict[str, Any]] = []
        for series in items:
            metadata = series.get("MetaData") or []
            for point in series.get("Data", []):
                if point.get("Secreto"):
                    # Suppressed for confidentiality (small-N municipalities/provinces).
                    continue
                valor = point.get("Valor")
                if valor is None:
                    continue
                periodo = point.get("Periodo") or {}
                rows.append(
                    {
                        "metadata": metadata,
                        "anyo": point.get("Anyo"),
                        "periodo_nombre": periodo.get("Nombre") or "",
                        "valor": float(valor),
                    }
                )

        if not rows:
            raise AdapterFailure(f"{payload.plan.label}: no usable (non-secret) data points")
        return pd.DataFrame(rows)

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        handler = getattr(self, f"_normalize_{plan.meta['kind']}")
        return handler(frame, ctx)

    # ---- MetaData / period helpers ---------------------------------------

    @staticmethod
    def _dim(metadata: list[dict[str, Any]], variable_name: str) -> tuple[str, str]:
        """Return (Nombre, Codigo) for the MetaData entry on this variable, or ("", "")."""
        for entry in metadata:
            if (entry.get("Variable") or {}).get("Nombre") == variable_name:
                return (entry.get("Nombre") or "").strip(), (entry.get("Codigo") or "").strip()
        return "", ""

    @staticmethod
    def _annual(anyo: Any) -> str:
        return str(int(anyo))

    @staticmethod
    def _monthly(anyo: Any, periodo_nombre: str) -> str:
        return f"{int(anyo):04d}-{int(periodo_nombre[1:]):02d}"

    @staticmethod
    def _quarterly(anyo: Any, periodo_nombre: str) -> str:
        return f"{int(anyo):04d}-Q{int(periodo_nombre[1:])}"

    @staticmethod
    def _month_to_quarter(anyo: Any, periodo_nombre: str) -> str:
        month = int(periodo_nombre[1:])
        return f"{int(anyo):04d}-Q{(month - 1) // 3 + 1}"

    def _record(self, **kwargs: Any) -> CanonicalRecord:
        return CanonicalRecord(source_id=self.manifest.source_id, **kwargs)

    # ---- per-table handlers -----------------------------------------------

    def _normalize_padron(self, frame: pd.DataFrame, ctx: RunContext) -> Iterable[CanonicalRecord]:
        cities_by_mun = {c.ine_mun: c for c in ctx.config.cities}
        out: list[CanonicalRecord] = []
        for row in frame.itertuples(index=False):
            meta = row.metadata
            if row.periodo_nombre != "A":
                continue
            _, mun_code = self._dim(meta, "Municipios")
            city = cities_by_mun.get(mun_code)
            if city is None:
                continue
            sex_name, _ = self._dim(meta, "Sexo")
            if sex_name != "Total":
                continue
            size_name, _ = self._dim(meta, "Tamaño de los municipios")
            if size_name != "Total habitantes":
                continue
            out.append(
                self._record(
                    metric_id="population_padron",
                    geo_id=municipality(city.ine_mun),
                    period=self._annual(row.anyo),
                    value=row.valor,
                    unit="persons",
                )
            )
        return out

    def _normalize_hotel_tn(self, frame: pd.DataFrame, ctx: RunContext) -> Iterable[CanonicalRecord]:
        # Table 2078: Viajeros y pernoctaciones por ciudad y residencia
        # Metadata dimensions:
        # - "Concepto turístico": "Viajero" or "Pernoctaciones"
        # - "PUNTOS TURÍSTICOS": city name
        # - "RESIDENCIA/ORIGEN": "Residentes en España" or "Residentes en el Extranjero"

        # Build city name mappings including aliases
        city_by_name = {}
        for c in ctx.config.cities:
            city_by_name[c.name] = c
            for alias in c.aliases:
                city_by_name[alias] = c

        staged: list[tuple[str, str, str, str, float]] = []  # (city_name, concept, period, residency, valor)

        for row in frame.itertuples(index=False):
            meta = row.metadata
            if not row.periodo_nombre.startswith("M"):
                continue

            # Extract dimensions from metadata
            concept, _ = self._dim(meta, "Concepto turístico")
            city_name, _ = self._dim(meta, "PUNTOS TURÍSTICOS")
            residency, _ = self._dim(meta, "RESIDENCIA/ORIGEN")

            # Skip if we're missing any required dimension
            if not concept or not city_name or not residency:
                continue

            # Check if this is one of our tracked cities
            city = city_by_name.get(city_name)
            if city is None:
                continue

            # Verify concept is one we expect
            if concept not in ("Viajero", "Pernoctaciones"):
                continue

            # Determine residency type
            if "España" in residency:
                residency_type = "Spain"
            elif "Extranjero" in residency:
                residency_type = "Foreign"
            else:
                continue

            staged.append((city.name, concept, self._monthly(row.anyo, row.periodo_nombre), residency_type, row.valor))

        if not staged:
            return []

        # Create a detailed dataframe with residency tracking
        totals = pd.DataFrame(staged, columns=["city", "concept", "period", "residency", "valor"])

        # Calculate totals and international share
        # Group by (city, concept, period) keeping track of residency splits
        by_city_concept_period = {}
        for row in totals.itertuples(index=False):
            key = (row.city, row.concept, row.period)
            if key not in by_city_concept_period:
                by_city_concept_period[key] = {"Spain": 0.0, "Foreign": 0.0}
            by_city_concept_period[key][row.residency] += row.valor

        metric_by_concept = {
            "Viajero": ("hotel_travellers", "persons"),
            "Pernoctaciones": ("hotel_nights", "nights"),
        }

        out: list[CanonicalRecord] = []
        for (city_name, concept, period), residencies in by_city_concept_period.items():
            total_value = residencies["Spain"] + residencies["Foreign"]
            metric_id, unit = metric_by_concept[concept]
            city = city_by_name[city_name]

            # Emit the total metric
            out.append(
                self._record(
                    metric_id=metric_id,
                    geo_id=municipality(city.ine_mun),
                    period=period,
                    value=total_value,
                    unit=unit,
                )
            )

            # Emit international share only for nights
            if concept == "Pernoctaciones" and total_value > 0:
                international_share = (residencies["Foreign"] / total_value) * 100
                out.append(
                    self._record(
                        metric_id="hotel_international_share",
                        geo_id=municipality(city.ine_mun),
                        period=period,
                        value=international_share,
                        unit="percent",
                    )
                )

        return out

    def _normalize_hotel_occ(self, frame: pd.DataFrame, ctx: RunContext) -> Iterable[CanonicalRecord]:
        city_by_name = {c.name: c for c in ctx.config.cities}
        out: list[CanonicalRecord] = []
        for row in frame.itertuples(index=False):
            meta = row.metadata
            if not row.periodo_nombre.startswith("M"):
                continue
            pt_name, _ = self._dim(meta, "PUNTOS TURISTÍCOS")
            city = city_by_name.get(pt_name)
            if city is None:
                continue
            concept, _ = self._dim(meta, "Concepto turístico")
            # By places, not rooms or weekend-only — INE's headline occupancy figure.
            if concept != "Grado de ocupación por plazas":
                continue
            out.append(
                self._record(
                    metric_id="hotel_occupancy_rate",
                    geo_id=municipality(city.ine_mun),
                    period=self._monthly(row.anyo, row.periodo_nombre),
                    value=row.valor,
                    unit="percent",
                )
            )
        return out

    def _hotel_price_metric(
        self, frame: pd.DataFrame, ctx: RunContext, *, metric_id: str
    ) -> Iterable[CanonicalRecord]:
        city_by_name = {c.name: c for c in ctx.config.cities}
        out: list[CanonicalRecord] = []
        for row in frame.itertuples(index=False):
            meta = row.metadata
            if not row.periodo_nombre.startswith("M"):
                continue
            pt_name, _ = self._dim(meta, "PUNTOS TURISTÍCOS")
            city = city_by_name.get(pt_name)
            if city is None:
                continue
            # Exclude the "Tasa de variación interanual" companion series.
            tipo_name, _ = self._dim(meta, "Tipo de dato")
            if tipo_name != "Dato":
                continue
            out.append(
                self._record(
                    metric_id=metric_id,
                    geo_id=municipality(city.ine_mun),
                    period=self._monthly(row.anyo, row.periodo_nombre),
                    value=row.valor,
                    unit="eur",
                )
            )
        return out

    def _normalize_hotel_adr(self, frame: pd.DataFrame, ctx: RunContext) -> Iterable[CanonicalRecord]:
        return self._hotel_price_metric(frame, ctx, metric_id="hotel_adr")

    def _normalize_hotel_revpar(self, frame: pd.DataFrame, ctx: RunContext) -> Iterable[CanonicalRecord]:
        return self._hotel_price_metric(frame, ctx, metric_id="hotel_revpar")

    def _normalize_mortgages(self, frame: pd.DataFrame, ctx: RunContext) -> Iterable[CanonicalRecord]:
        city_by_prov = {c.ine_prov: c for c in ctx.config.cities}
        out: list[CanonicalRecord] = []
        for row in frame.itertuples(index=False):
            meta = row.metadata
            if not row.periodo_nombre.startswith("M"):
                continue
            nature, _ = self._dim(meta, "Naturaleza de la finca")
            if nature != "Viviendas":
                continue
            concept, _ = self._dim(meta, "Concepto financiero")
            if concept != "Número de hipotecas":
                continue
            _, prov_code = self._dim(meta, "Provincias")
            city = city_by_prov.get(prov_code)
            if city is None:
                continue
            out.append(
                self._record(
                    metric_id="mortgages_count",
                    geo_id=province(city.ine_prov),
                    period=self._monthly(row.anyo, row.periodo_nombre),
                    value=row.valor,
                    unit="mortgages",
                )
            )
        return out

    def _normalize_transfers(self, frame: pd.DataFrame, ctx: RunContext) -> Iterable[CanonicalRecord]:
        city_by_prov = {c.ine_prov: c for c in ctx.config.cities}
        out: list[CanonicalRecord] = []
        for row in frame.itertuples(index=False):
            meta = row.metadata
            if not row.periodo_nombre.startswith("M"):
                continue
            title, _ = self._dim(meta, "Título de adquisición")
            if title != "Total":
                continue
            dwelling_type, _ = self._dim(meta, "Tipo de vivienda")
            if dwelling_type != "Total viviendas":
                continue
            tipo_dato, _ = self._dim(meta, "Tipo de dato")
            if tipo_dato != "Número":
                continue
            _, prov_code = self._dim(meta, "Provincias")
            city = city_by_prov.get(prov_code)
            if city is None:
                continue
            out.append(
                self._record(
                    metric_id="property_transfers",
                    geo_id=province(city.ine_prov),
                    period=self._monthly(row.anyo, row.periodo_nombre),
                    value=row.valor,
                    unit="transactions",
                )
            )
        return out

    def _normalize_hpi(self, frame: pd.DataFrame, ctx: RunContext) -> Iterable[CanonicalRecord]:
        city_by_ccaa = {c.ine_ccaa: c for c in ctx.config.cities}
        out: list[CanonicalRecord] = []
        for row in frame.itertuples(index=False):
            meta = row.metadata
            if not row.periodo_nombre.startswith("T"):
                continue
            breakdown, _ = self._dim(meta, "General, vivienda nueva y de segunda mano")
            if breakdown != "General":
                continue
            measure, _ = self._dim(meta, "Índices y Tasas")
            if measure != "Índice":
                continue
            _, ccaa_code = self._dim(meta, "Comunidades y Ciudades Autónomas")
            city = city_by_ccaa.get(ccaa_code)
            if city is None:
                continue
            out.append(
                self._record(
                    metric_id="house_price_index",
                    geo_id=ccaa(city.ine_ccaa),
                    period=self._quarterly(row.anyo, row.periodo_nombre),
                    value=row.valor,
                    unit="index",
                )
            )
        return out

    def _normalize_vte(self, frame: pd.DataFrame, ctx: RunContext) -> Iterable[CanonicalRecord]:
        city_by_mun = {c.ine_mun: c for c in ctx.config.cities}
        metric_by_concept = {
            "Viviendas turísticas": ("tourist_dwellings", "dwellings"),
            "Plazas": ("tourist_dwelling_places", "places"),
        }
        out: list[CanonicalRecord] = []
        for row in frame.itertuples(index=False):
            meta = row.metadata
            if not row.periodo_nombre.startswith("M"):
                continue
            # This table mixes province-scope and municipality-scope series under
            # one id; only the "Municipios" dimension carries the 5-digit code we
            # match on, so the province rows fall out on their own (empty code).
            _, mun_code = self._dim(meta, "Municipios")
            city = city_by_mun.get(mun_code)
            if city is None:
                continue
            concept, _ = self._dim(meta, "Concepto turístico")
            mapping = metric_by_concept.get(concept)
            if mapping is None:
                continue
            metric_id, unit = mapping
            out.append(
                self._record(
                    metric_id=metric_id,
                    geo_id=municipality(city.ine_mun),
                    period=self._month_to_quarter(row.anyo, row.periodo_nombre),
                    value=row.valor,
                    unit=unit,
                )
            )
        return out

    def _normalize_births(self, frame: pd.DataFrame, ctx: RunContext) -> Iterable[CanonicalRecord]:
        city_by_prov = {c.ine_prov: c for c in ctx.config.cities}
        out: list[CanonicalRecord] = []
        unmatched_provinces: dict[str, str] = {}

        for row in frame.itertuples(index=False):
            meta = row.metadata
            if not row.periodo_nombre.startswith("M"):
                continue
            # Get province code
            _, prov_code = self._dim(meta, "Provincias")
            if not prov_code or prov_code == "00":  # Skip Total Nacional
                continue
            city = city_by_prov.get(prov_code)
            if city is None:
                # Track unmatched provinces for reporting
                prov_name, _ = self._dim(meta, "Provincias")
                if prov_code not in unmatched_provinces:
                    unmatched_provinces[prov_code] = prov_name
                continue
            out.append(
                self._record(
                    metric_id="births",
                    geo_id=province(city.ine_prov),
                    period=self._monthly(row.anyo, row.periodo_nombre),
                    value=row.valor,
                    unit="births",
                )
            )

        # Log any unmatched provinces
        if unmatched_provinces:
            import warnings
            for code in sorted(unmatched_provinces.keys()):
                warnings.warn(
                    f"births table: province code {code} ({unmatched_provinces[code]}) "
                    "not in tracked cities"
                )

        return out

    def _normalize_marriages(self, frame: pd.DataFrame, ctx: RunContext) -> Iterable[CanonicalRecord]:
        city_by_prov = {c.ine_prov: c for c in ctx.config.cities}
        out: list[CanonicalRecord] = []
        unmatched_provinces: dict[str, str] = {}

        for row in frame.itertuples(index=False):
            meta = row.metadata
            if not row.periodo_nombre.startswith("M"):
                continue
            # Get province code
            _, prov_code = self._dim(meta, "Provincias")
            if not prov_code or prov_code == "00":  # Skip Total Nacional
                continue
            city = city_by_prov.get(prov_code)
            if city is None:
                # Track unmatched provinces for reporting
                prov_name, _ = self._dim(meta, "Provincias")
                if prov_code not in unmatched_provinces:
                    unmatched_provinces[prov_code] = prov_name
                continue
            out.append(
                self._record(
                    metric_id="marriages",
                    geo_id=province(city.ine_prov),
                    period=self._monthly(row.anyo, row.periodo_nombre),
                    value=row.valor,
                    unit="marriages",
                )
            )

        # Log any unmatched provinces
        if unmatched_provinces:
            import warnings
            for code in sorted(unmatched_provinces.keys()):
                warnings.warn(
                    f"marriages table: province code {code} ({unmatched_provinces[code]}) "
                    "not in tracked cities"
                )

        return out
