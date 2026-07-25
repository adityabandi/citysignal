"""Viviendas de uso turístico — licensed short-term-rental dwellings by district.

The geoportal dataset (300694) ships as a single ESRI shapefile bundle zipped
up, one point per licensed building with a ``UNIDADES_V`` field giving the
number of touristic-use dwelling units that building's licence covers — a
building can be licensed for more than one unit, so the district total sums
units, not placemarks. Only the attribute table is needed (district name,
unit count), not the point geometry, so the .dbf member is read directly with
the pure-Python reader in ``_shared.py`` rather than adding a geopandas/fiona
dependency for one field.

The ``DISTRITO`` field is a free-text "Distrito de <name>" string, inconsistently
accented ('Distrito de Chamartín' vs a handful of unaccented 'Distrito de
Chamartin' rows — both forms occur in the same file), so it is matched to a
district code through ``normalize_district_name`` rather than a literal string
table.

This is what the Ayuntamiento itself licenses under this dataset: change-of-use
licences it grants for touristic housing. It is materially smaller and less
Centro-concentrated than the regional (Comunidad de Madrid) touristic-dwelling
registry that most "how many Airbnbs does Madrid have" figures come from — that
registry is not published on datos.madrid.es and is not fetched here. Centro is
still the single largest district in this file, which is the sanity signal this
adapter can actually offer.

The dataset carries no snapshot date of its own (only per-licence decree
dates); like the Censo de Locales this is a current-state file, so each run
emits one observation for the run's own calendar month, and — as with any
current-state source — history accumulates naturally as the pipeline is run
month over month.
"""

from __future__ import annotations

import io
import zipfile
from typing import Iterable

import pandas as pd

from ...framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ...framework.fetch import FetchPlan, RawPayload
from ...framework.record import CanonicalRecord, district, utc_today
from ._shared import district_code_from_name, read_dbf

VUT_URL = (
    "https://datos.madrid.es/dataset/300694-0-viviendas-turisticas-geoportal/"
    "resource/300694-0-viviendas-turisticas-geoportal/download/"
    "300694-0-viviendas-turisticas-geoportal.zip"
)
DBF_MEMBER_SUFFIX = "VIVIENDAS_USO_TURISTICO.dbf"


class MadridVutAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="madrid_vut",
        publisher="Ayuntamiento de Madrid",
        license="Reuse permitted (attribution)",
        attribution="Source: Ayuntamiento de Madrid, viviendas de uso turístico",
        docs_url="https://datos.madrid.es/dataset/300694-0-viviendas-turisticas-geoportal",
        cadence="monthly",
        geo_level="district",
        max_age_days=120,
        formats=("zip",),
        kind="official",
        redistribute=True,
        min_rows=1,
        notes=(
            "Ayuntamiento de Madrid change-of-use licences for touristic "
            "housing (geoportal shapefile, attribute table only), summed to "
            "district level by UNIDADES_V. Narrower than the Comunidad de "
            "Madrid regional VUT registry, which is not published as open "
            "data and is not used here."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        return [FetchPlan(url=VUT_URL, fmt="zip", label="vut-geoportal")]

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload.content))
        except zipfile.BadZipFile as exc:
            raise AdapterFailure(f"VUT download is not a valid zip: {exc}") from exc

        member = next((n for n in archive.namelist() if n.endswith(DBF_MEMBER_SUFFIX)), None)
        if member is None:
            raise AdapterFailure(
                f"no {DBF_MEMBER_SUFFIX} member found in VUT zip (contents: {archive.namelist()})"
            )
        rows = read_dbf(archive.read(member))
        if not rows:
            raise AdapterFailure("VUT shapefile attribute table parsed to zero rows")
        return pd.DataFrame(rows)

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        city = ctx.config.city("madrid")
        period = utc_today().isoformat()[:7]

        if "DISTRITO" not in frame.columns or "UNIDADES_V" not in frame.columns:
            raise AdapterFailure(f"VUT attribute table missing DISTRITO/UNIDADES_V (got {list(frame.columns)})")

        totals: dict[str, float] = {}
        unmatched = 0
        for row in frame.itertuples(index=False):
            code = district_code_from_name(getattr(row, "DISTRITO"))
            if code is None:
                unmatched += 1
                continue
            try:
                units = float(getattr(row, "UNIDADES_V") or 0)
            except ValueError:
                units = 0.0
            totals[code] = totals.get(code, 0.0) + units

        if not totals:
            raise AdapterFailure("VUT: no row's DISTRITO field matched a known Madrid district")
        if unmatched:
            # A handful of unparsable district labels is a data quirk; a
            # majority would mean the field format changed under us.
            if unmatched > len(frame) * 0.1:
                raise AdapterFailure(f"VUT: {unmatched}/{len(frame)} rows had an unmatched DISTRITO value")

        return [
            CanonicalRecord(
                metric_id="vut_licensed",
                geo_id=district(city.ine_mun, code),
                period=period,
                value=total,
                unit="dwellings",
                source_id=self.manifest.source_id,
            )
            for code, total in totals.items()
        ]
