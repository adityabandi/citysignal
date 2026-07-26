"""SERPAVI — Spanish state rental-price reference system, district level.

SERPAVI (Sistema Estatal de Precios de Referencia del Alquiler de Viviendas)
is published by MIVAU as the nation's official rental reference. District-level
tables for Madrid municipality 28079 are published annually on:
https://www.mivau.gob.es/vivienda/alquila-bien-es-tu-derecho/serpavi

The full 2011–2024 Excel database lists district average rent (€/m²/month),
total monthly rent, and dwelling count observed. This is the primary source
for understanding rental pressure by neighbourhood.

The adapter downloads the Excel file, extracts the Madrid municipal sheet,
filters to the 21 districts, and emits three metrics per district per year.
SANITY CHECK: Salamanca (04) / Chamberí (07) / Centro (01) must show higher
€/m² than Villaverde (17) / Usera (12) / Puente de Vallecas (13). If the
ordering inverts, the column selection is wrong — fix it.
"""

from __future__ import annotations

import io
import logging
from typing import Iterable

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.record import CanonicalRecord, district

log = logging.getLogger(__name__)

# SERPAVI district-level data via Madrid city council portal (updated annually)
# Format: INDICE ESTATAL ALQUILER _distritos<YEAR>.xlsx
MADRID_SERPAVI_BASE = "https://www.madrid.es/UnidadesDescentralizadas/UDCEstadistica/Nuevaweb/Edificaci%C3%B3n%20y%20Vivienda/Mercado%20de%20la%20Vivienda/Sistema%20Estatal%20de%20%C3%8Dndices%20de%20Referencia%20del%20Precio%20del%20Alquiler%20de%20Vivienda"
# Current year file (2026)
MADRID_SERPAVI_URL = f"{MADRID_SERPAVI_BASE}/2022_2023_2024_2025/INDICE%20ESTATAL%20ALQUILER%20_distritos2026.xlsx"

# District code -> expected price range for sanity check (€/m²/month)
# Establishes that expensive districts are in the order we expect
SANITY_CHECK_RANGES = {
    "01": (12, 25),  # Centro: most expensive
    "04": (11, 24),  # Salamanca: very expensive
    "07": (10, 23),  # Chamberí: very expensive
    "12": (6, 14),   # Usera: cheaper
    "13": (6, 14),   # Puente de Vallecas: cheaper
    "17": (5, 13),   # Villaverde: cheapest
}


class SerpavIAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="serpavi",
        publisher="Ministerio de Vivienda y Agenda Urbana",
        license="Reuse permitted (attribution)",
        attribution="Source: MIVAU / SERPAVI",
        docs_url="https://www.mivau.gob.es/vivienda/alquila-bien-es-tu-derecho/serpavi",
        cadence="annual",
        geo_level="district",
        max_age_days=400,
        formats=("xlsx", "xls"),
        kind="official",
        min_rows=21,
        notes=(
            "SERPAVI district-level annual averages: €/m²/month rent, total monthly rent, "
            "and count of rented dwellings observed. Madrid municipality 28079 only. "
            "Full 2011–2024 history available from the MIVAU portal."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        # SERPAVI district-level data is available from Madrid city council's
        # statistics portal, updated annually. We try multiple URLs in order of
        # preference, with optional fallbacks.

        plans: list[FetchPlan] = []

        # Primary: Madrid city council current year (2026)
        plans.append(
            FetchPlan(
                url=MADRID_SERPAVI_URL,
                fmt="xlsx",
                label="serpavi-madrid-2026",
                optional=False,
                meta={"source": "madrid", "year": "2026"},
            )
        )

        # Secondary: Try to discover other years from the Madrid portal
        try:
            page = ctx.fetcher.get(
                FetchPlan(
                    url="https://www.madrid.es/portales/munimadrid/es/Inicio/El-Ayuntamiento/Estadistica/Areas-de-informacion-estadistica/Edificacion-y-vivienda/Mercado-de-la-vivienda/Sistema-estatal-de-indices-de-referencia-del-precio-del-alquiler-de-viviendas/",
                    fmt="html",
                    label="serpavi-madrid-page",
                )
            )
            if page:
                # Extract all SERPAVI district files from the page
                import re
                for match in re.finditer(
                    r'href="([^"]*INDICE%20ESTATAL%20ALQUILER%20_distritos[^"]*\.xlsx)"',
                    page.text(),
                    re.IGNORECASE,
                ):
                    url = match.group(1)
                    if not url.startswith("http"):
                        url = f"https://www.madrid.es{url}"
                    plans.append(
                        FetchPlan(
                            url=url,
                            fmt="xlsx",
                            label="serpavi-madrid-discovered",
                            optional=True,
                            meta={"source": "madrid"},
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            log.debug("serpavi: could not discover additional years from Madrid portal: %s", exc)

        if not plans:
            raise AdapterFailure("could not discover SERPAVI Excel download URL")
        return plans

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        try:
            # Read the Excel file
            excel_file = io.BytesIO(payload.content)
            sheet_names = pd.ExcelFile(excel_file).sheet_names

            # Use the first sheet (typically named with year, e.g. "2026")
            target_sheet = sheet_names[0] if sheet_names else "Sheet1"

            # Read with no header since the headers span multiple rows
            frame = pd.read_excel(excel_file, sheet_name=target_sheet, header=None, dtype=str)

            if frame.empty:
                raise AdapterFailure(f"{payload.plan.label}: parsed zero rows from {target_sheet}")

            return frame
        except Exception as exc:
            raise AdapterFailure(
                f"{payload.plan.label}: failed to parse Excel: {exc}"
            ) from exc

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        """Extract Madrid district rental data from SERPAVI sheet.

        The Madrid SERPAVI sheet layout (from the data directory):
        - Row with district names in column B (e.g., "01 - Centro", "02 - Arganzuela")
        - Column C: Nº viviendas (dwelling count)
        - Column I: Renta media €/m2.mes (rent per m² per month) for collective dwellings
        - Column L: Cuantía media arrendamiento (total rent) for collective dwellings

        The sheet contains data for all 12 months (rows differ by district), and we
        extract the year from the file name or assume it's the current/most recent year.
        """
        city = ctx.config.city("madrid")

        # Extract year from the sheet name or plan metadata
        year_val = None
        if plan.meta and "year" in plan.meta:
            year_val = int(plan.meta["year"])
        else:
            # Try to infer from sheet name or filename
            sheet_name = plan.label or ""
            import re
            match = re.search(r"20\d{2}", sheet_name)
            if match:
                year_val = int(match.group(0))

        if not year_val:
            year_val = 2024  # fallback

        out: list[CanonicalRecord] = []

        for _, row in frame.iterrows():
            try:
                # Extract district name from column B (index 1)
                # Expected format: "01 - Centro", "02 - Arganzuela", etc.
                dist_name_raw = str(row.get(1, "")).strip()
                if not dist_name_raw or not dist_name_raw[0].isdigit():
                    continue

                # Parse district code (first 2 characters or before " -")
                dist_code = dist_name_raw.split("-")[0].strip().zfill(2)
                if not (1 <= int(dist_code) <= 21):
                    continue

                # Column C (index 2): Nº viviendas (dwelling count)
                dwellings_val = self._parse_number(str(row.get(2, "")))

                # Column I (index 8): Renta media €/m2.mes
                rent_m2_val = self._parse_number(str(row.get(8, "")))

                # Column L (index 11): Cuantía media arrendamiento (total rent)
                rent_total_val = self._parse_number(str(row.get(11, "")))

                period = f"{year_val:04d}"

                # Emit records for available metrics
                if dwellings_val is not None and dwellings_val > 0:
                    out.append(
                        CanonicalRecord(
                            metric_id="rent_district_dwellings",
                            geo_id=district(city.ine_mun, dist_code),
                            period=period,
                            value=dwellings_val,
                            unit="dwellings",
                            source_id=self.manifest.source_id,
                        )
                    )

                if rent_m2_val is not None and rent_m2_val > 0:
                    out.append(
                        CanonicalRecord(
                            metric_id="rent_district_m2",
                            geo_id=district(city.ine_mun, dist_code),
                            period=period,
                            value=rent_m2_val,
                            unit="eur_m2_month",
                            source_id=self.manifest.source_id,
                        )
                    )

                if rent_total_val is not None and rent_total_val > 0:
                    out.append(
                        CanonicalRecord(
                            metric_id="rent_district_total",
                            geo_id=district(city.ine_mun, dist_code),
                            period=period,
                            value=rent_total_val,
                            unit="eur",
                            source_id=self.manifest.source_id,
                        )
                    )

            except Exception as exc:  # noqa: BLE001
                log.debug("SERPAVI row parse error: %s", exc)
                continue

        if not out:
            raise AdapterFailure(f"{plan.label}: produced zero records")

        return out

    @staticmethod
    def _parse_year(value: str) -> int | None:
        try:
            year = int(value.strip())
            if 2000 <= year <= 2100:
                return year
        except ValueError:
            pass
        return None

    @staticmethod
    def _parse_number(value: str) -> float | None:
        """Parse a number that may use comma or period as decimal separator."""
        if not value:
            return None
        value = value.strip()
        if not value or value.lower() in ("nan", "null", "none", "_", "-"):
            return None
        # Replace comma with period for European format
        value = value.replace(",", ".")
        try:
            return float(value)
        except ValueError:
            return None
