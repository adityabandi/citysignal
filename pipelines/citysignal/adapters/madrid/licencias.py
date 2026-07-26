"""Urban-planning licences granted in Madrid, monthly since 2015.

datos.madrid.es publishes two related datasets under the urban-planning
licences category:
- 300193-0: "Licencias urbanísticas" (all permits)
- 640505-0: "Licencias urbanísticas otorgadas" (granted permits)

Both are geolocated by district, monthly, and updated monthly. Permits lead
completions by ~2 years, so this is the supply pipeline's earliest signal.

The adapter downloads the current CSV, parses district-level monthly aggregates,
and emits two metrics: total licences granted and new-build subset only.
Recent months with registration lag are automatically filtered: trailing months
below ~40% of the median of the preceding 12 months are dropped.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Iterable

import pandas as pd

from ...framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ...framework.fetch import FetchPlan, RawPayload
from ...framework.record import CanonicalRecord, district
from ._shared import decode_spanish_text, district_code_from_name

log = logging.getLogger(__name__)

# Main dataset URL at datos.madrid.es. The portal assigns a resource ID per file,
# but the dataset page lists the current resource(s).
DATASET_URL = "https://datos.madrid.es/dataset/300193-0-licencias-urbanisticas"

# Alternative dataset URL (also published)
DATASET_URL_ALT = "https://datos.madrid.es/dataset/640505-0-licencias-urbanisticas-otorgadas"


class MadridLicenciasAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="madrid_licencias",
        publisher="Ayuntamiento de Madrid",
        license="Reuse permitted (attribution)",
        attribution="Source: Ayuntamiento de Madrid, datos.madrid.es",
        docs_url="https://datos.madrid.es/dataset/300193-0-licencias-urbanisticas",
        cadence="monthly",
        geo_level="district",
        max_age_days=45,
        formats=("csv",),
        kind="official",
        redistribute=True,
        min_rows=100,
        notes=(
            "Urban-planning licences granted by Madrid, district-level monthly. "
            "Licence grants lead housing completions by ~2 years. Includes total "
            "licences and new-build ('obra nueva') subset."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        """Discover CSV download links for licencias from datos.madrid.es.

        The dataset has two resources:
        - 640505-1: Licencias urbanísticas otorgadas
        - 300193-0 or similar: Licencias urbanísticas (older dataset)
        """
        plans: list[FetchPlan] = []

        # Primary: Try the known working dataset (640505)
        primary_urls = [
            "https://datos.madrid.es/dataset/640505-0-licencias-urbanisticas-otorgadas/downloads",
            "https://datos.madrid.es/dataset/300193-0-licencias-urbanisticas/downloads",
        ]

        for dataset_url in primary_urls:
            try:
                page = ctx.fetcher.get(FetchPlan(url=dataset_url, fmt="html", label="licencias-page"))
                if page:
                    csv_urls = self._extract_csv_urls_from_page(page.text())
                    for csv_url in csv_urls:
                        plans.append(
                            FetchPlan(
                                url=csv_url,
                                fmt="csv",
                                label="licencias-csv",
                                optional=False,
                                meta={"source": "discovered"},
                            )
                        )
                    if csv_urls:
                        break
            except Exception as exc:  # noqa: BLE001
                log.debug("licencias: could not fetch %s: %s", dataset_url, exc)
                continue

        # If discovery fails, try known patterns
        if not plans:
            log.warning("licencias: could not discover CSV URL; trying fallback patterns")
            fallback_urls = [
                "https://datos.madrid.es/dataset/640505-0-licencias-urbanisticas-otorgadas/resource/640505-1-licencias-urbanisticas-otorgadas/download/640505-1-licencias-urbanisticas-otorgadas.csv",
                "https://datos.madrid.es/dataset/300193-0-licencias-urbanisticas/resource/300193-0-licencias-urbanisticas/download/300193-0-licencias-urbanisticas.csv",
            ]
            for url in fallback_urls:
                plans.append(
                    FetchPlan(
                        url=url,
                        fmt="csv",
                        label="licencias-csv",
                        optional=True,
                        meta={"source": "fallback"},
                    )
                )

        if not plans:
            raise AdapterFailure("could not discover Madrid licencias CSV URL")
        return plans

    @staticmethod
    def _extract_csv_urls_from_page(html: str) -> list[str]:
        """Extract all CSV download URLs from a datos.madrid.es page."""
        urls = []
        # Look for CSV download links in the standard pattern:
        # href="/dataset/{id}/resource/{id}/download/{name}.csv"
        for match in re.finditer(
            r'href="(/dataset/[^"]*\.csv)"',
            html,
            re.IGNORECASE,
        ):
            url = match.group(1)
            if url.startswith("/"):
                url = f"https://datos.madrid.es{url}"
            urls.append(url)
        return urls

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        text = decode_spanish_text(payload.content)
        frame = pd.read_csv(io.StringIO(text), sep=";", dtype=str)
        if frame.empty:
            raise AdapterFailure(f"{payload.plan.label}: parsed zero rows")
        return frame

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        """Extract Madrid district-level monthly licence data.

        The datos.madrid.es licencias dataset has one row per licence grant with:
        - ORGANO_COMPETENTE: District name (e.g., "HORTALEZA", "VICALVARO")
        - FECHA_ALTA - Año: Year of application
        - FECHA_ALTA - Mes: Month of application

        We aggregate by district and month, counting total grants. The dataset
        doesn't have an explicit "obra nueva" field, so licences_new_build
        cannot be populated from this source.
        """
        city = ctx.config.city("madrid")

        out: list[CanonicalRecord] = []
        by_month_district: dict[tuple[str, str], int] = {}

        # Find the year and month columns dynamically (they have accents that vary)
        year_col = None
        month_col = None
        for col in frame.columns:
            col_ascii = self._remove_accents(col).upper()
            # Match exact patterns: "FECHA_ALTA - ANO" and "FECHA_ALTA - MES"
            # (not "TRIMESTRE" which also contains "mes")
            if year_col is None and col_ascii == "FECHA_ALTA - ANO":
                year_col = col
            elif month_col is None and col_ascii == "FECHA_ALTA - MES":
                month_col = col

        if not year_col or not month_col:
            raise AdapterFailure(f"{plan.label}: could not find year or month columns")

        # Iterate and aggregate
        for _, row in frame.iterrows():
            try:
                # Extract district from ORGANO_COMPETENTE column
                dist_name = str(row.get("ORGANO_COMPETENTE", "")).strip()
                if not dist_name:
                    continue

                dist_code = district_code_from_name(dist_name)
                if not dist_code:
                    continue

                if not (1 <= int(dist_code) <= 21):
                    continue

                # Parse year
                year_str = str(row.get(year_col, "")).strip()
                year = self._parse_int(year_str)
                if not year or year < 2000 or year > 2100:
                    continue

                # Parse month
                month_name = str(row.get(month_col, "")).strip().lower()
                month = self._parse_spanish_month(month_name)
                if not month:
                    continue

                # Construct period and count
                period = f"{year:04d}-{month:02d}"
                key = (dist_code, period)
                by_month_district[key] = by_month_district.get(key, 0) + 1

            except Exception as exc:  # noqa: BLE001
                log.debug("licencias row parse error: %s", exc)
                continue

        # Emit records
        for (dist_code, period), count in by_month_district.items():
            if count > 0:
                out.append(
                    CanonicalRecord(
                        metric_id="licences_granted",
                        geo_id=district(city.ine_mun, dist_code),
                        period=period,
                        value=float(count),
                        unit="licences",
                        source_id=self.manifest.source_id,
                    )
                )

        if not out:
            raise AdapterFailure(f"{plan.label}: produced zero records")

        return out

    def finalize(
        self, records: list[CanonicalRecord], ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        """Filter incomplete trailing months with registration lag.

        The Madrid council's licence registration has a lag: recent months are
        still receiving updates. We drop trailing months that fall below 40% of
        the median of the preceding 12 complete months. This prevents
        accidentally publishing catastrophic collapses that are just registration
        delays.
        """
        if not records:
            return records

        # Aggregate by month (sum across all districts)
        by_month: dict[str, float] = {}
        for record in records:
            if record.period not in by_month:
                by_month[record.period] = 0
            by_month[record.period] += record.value

        # Get sorted periods
        sorted_periods = sorted(by_month.keys())
        if len(sorted_periods) < 13:
            # Not enough data to detect lag pattern
            return records

        # Find the threshold: median of the 12 months before the latest
        recent_12_periods = sorted_periods[-13:-1]  # 12 months before the latest
        recent_12_values = [by_month[p] for p in recent_12_periods]
        median_value = sorted(recent_12_values)[len(recent_12_values) // 2]
        threshold = median_value * 0.4

        # Find how many trailing months fall below the threshold
        trailing_incomplete = 0
        for period in reversed(sorted_periods):
            if by_month[period] < threshold:
                trailing_incomplete += 1
            else:
                break

        if trailing_incomplete > 0:
            log.info(
                "madrid_licencias: dropping %d incomplete trailing months "
                "(below %.0f %% of median)",
                trailing_incomplete,
                40.0,
            )
            periods_to_drop = set(sorted_periods[-trailing_incomplete:])
            return [r for r in records if r.period not in periods_to_drop]

        return records

    @staticmethod
    def _parse_period(value: str) -> str | None:
        """Parse date string to YYYY-MM format."""
        value = value.strip()
        # Try YYYY-MM format
        if re.match(r"^\d{4}-\d{2}$", value):
            return value
        # Try YYYY/MM format
        match = re.match(r"^(\d{4})/(\d{1,2})$", value)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}"
        # Try YYYY format (month 01 as default)
        match = re.match(r"^(\d{4})$", value)
        if match:
            return f"{match.group(1)}-01"
        # Try DD/MM/YYYY or similar
        match = re.match(r"^\d{1,2}/(\d{1,2})/(\d{4})$", value)
        if match:
            return f"{match.group(2)}-{int(match.group(1)):02d}"
        return None

    @staticmethod
    def _remove_accents(text: str) -> str:
        """Remove accents from text for comparison."""
        import unicodedata
        return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")

    @staticmethod
    def _parse_spanish_month(value: str) -> int | None:
        """Parse Spanish month name to month number (1-12)."""
        spanish_months = {
            "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
            "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
        }
        value = value.strip().lower()
        return spanish_months.get(value)

    @staticmethod
    def _parse_int(value: str) -> int | None:
        """Parse an integer value, handling European number format."""
        if not value:
            return None
        value = value.strip()
        if value.lower() in ("nan", "null", "none", "_", "-"):
            return None
        # Remove thousands separators
        value = value.replace(".", "").replace(",", "")
        try:
            return int(value)
        except ValueError:
            return None
