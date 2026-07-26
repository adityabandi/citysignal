"""Building permits (visados de dirección de obra) by autonomous community, monthly.

The Ministerio de Transportes y Movilidad Sostenible publishes construction
statistics including visados (building permits filed with the architecture board
before construction begins). These precede housing starts, which precede
completions by roughly 1-2 years, making them the earliest supply-side signal
in Spanish housing data. The series goes back to 1992.

The data is available as a single CSV file with monthly granularity, covering
the Comunidad de Madrid and Spain overall.
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any, Iterable

from ..framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.record import CanonicalRecord, ccaa

log = logging.getLogger(__name__)

# Single verified CSV source
DATA_URL = (
    "https://datos.comunidad.madrid/dataset/6e5b1601-1af0-49bf-813b-227a093b7af1/"
    "resource/532735ad-4ce6-4a29-ad11-bb86916c0cf4/download/"
    "extraccionesvisados-de-direccion-de-obra-nueva-por-uso-de-la-edificacion."
    "-comunidad-de-madrid-y-.csv"
)


def _num(value: Any) -> float | None:
    """Parse a value to float, handling Spanish number formats."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("nan", "null", "none", "_", "-"):
        return None
    try:
        # Handle period as decimal separator (as in the file)
        return float(text)
    except ValueError:
        return None


class VisadosAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="visados",
        publisher="Ministerio de Transportes y Movilidad Sostenible",
        license="Reuse permitted (attribution)",
        attribution="Source: Ministry of Transport, Building Permits Statistics",
        docs_url="https://www.transportes.gob.es/informacion-para-el-ciudadano/informacion-estadistica/vivienda-y-actuaciones-urbanas",
        cadence="monthly",
        geo_level="ccaa",
        max_age_days=120,
        formats=("csv",),
        kind="official",
        min_rows=2,
        notes=(
            "Monthly building permits (visados de dirección de obra) filed with "
            "the architecture board, by autonomous community. The earliest signal of housing "
            "supply arriving, preceding starts by 1-2 years and completions by "
            "2-4 years. Series includes data back to 1992."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        """Return the single CSV source."""
        return [FetchPlan(url=DATA_URL, fmt="csv", label="visados-monthly")]

    def parse(self, payload: RawPayload, ctx: RunContext) -> dict[str, list[dict]]:
        """Load the CSV file and parse rows into dictionaries by (Año, Periodo, Territorio, Medida, Uso edificación, Indicador)."""
        text = payload.text()
        reader = csv.DictReader(
            io.StringIO(text),
            delimiter=";",
        )

        if reader.fieldnames is None:
            raise AdapterFailure("CSV has no header row")

        expected_cols = {
            "Año",
            "Periodo",
            "Territorio",
            "Uso edificación",
            "Medida",
            "Indicador",
            "Valor",
            "Unidad",
            "Estado dato",
        }
        actual_cols = set(reader.fieldnames)
        if not expected_cols.issubset(actual_cols):
            raise AdapterFailure(
                f"CSV is missing expected columns. Expected subset: {expected_cols}, "
                f"got: {actual_cols}"
            )

        rows = list(reader)
        if not rows:
            raise AdapterFailure("CSV contains no data rows")

        log.info("visados: parsed %d rows from CSV", len(rows))
        return {"rows": rows}

    def normalize(
        self, data: dict[str, list[dict]], plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        """Convert CSV rows to canonical records, filtering and emitting three metrics."""
        rows = data.get("rows", [])

        for row in rows:
            try:
                año = row.get("Año", "").strip()
                periodo = row.get("Periodo", "").strip()
                territorio = row.get("Territorio", "").strip()
                uso = row.get("Uso edificación", "").strip()
                medida = row.get("Medida", "").strip()
                indicador = row.get("Indicador", "").strip()
                valor_str = row.get("Valor", "").strip()
                unidad = row.get("Unidad", "").strip()

                # Filter 1: Territorio must be "Comunidad de Madrid"
                if territorio != "Comunidad de Madrid":
                    continue

                # Filter 2: Indicador must be "Serie" (not Tasa de variación)
                if indicador != "Serie":
                    continue

                # Filter 3: Periodo must start with "M" (monthly, not annual)
                if not periodo.startswith("M"):
                    continue

                # Skip rows with no value
                if not valor_str:
                    continue

                value = _num(valor_str)
                if value is None:
                    continue

                # Parse year and period: Período like "M01" -> month 01
                try:
                    year = int(año)
                    month = int(periodo[1:])
                    if not (1 <= month <= 12):
                        continue
                except (ValueError, IndexError):
                    continue

                period = f"{year:04d}-{month:02d}"

                # Emit visados_new_build: housing buildings
                if (
                    medida == "Número de edificios"
                    and uso == "Edificios destinados a vivienda"
                ):
                    yield CanonicalRecord(
                        metric_id="visados_new_build",
                        geo_id=ccaa("13"),
                        period=period,
                        value=value,
                        unit="buildings",
                        source_id=self.manifest.source_id,
                    )

                # Emit visados_area: construction area
                elif (
                    medida == "Superficie a construir"
                    and uso == "Edificios destinados a vivienda"
                ):
                    yield CanonicalRecord(
                        metric_id="visados_area",
                        geo_id=ccaa("13"),
                        period=period,
                        value=value,
                        unit="m2",
                        source_id=self.manifest.source_id,
                    )

                # Emit visados_budget: execution budget
                elif (
                    medida == "Presupuesto de ejecución material"
                    and uso == "Edificios destinados a vivienda"
                ):
                    # Check if the value is in euros or thousands of euros
                    # by inspecting the Unidad column
                    if unidad.lower() in ("miles de euros", "miles de €"):
                        # Scale from thousands to euros
                        value = value * 1000
                    yield CanonicalRecord(
                        metric_id="visados_budget",
                        geo_id=ccaa("13"),
                        period=period,
                        value=value,
                        unit="eur",
                        source_id=self.manifest.source_id,
                    )

            except Exception as exc:  # noqa: BLE001
                log.debug("visados: error processing row: %s", exc)
                continue
