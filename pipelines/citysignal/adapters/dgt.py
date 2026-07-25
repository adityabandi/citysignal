"""DGT — monthly vehicle registrations by province, from raw microdata.

The DGT ("dgt-en-cifras") portal's pre-aggregated statistical tables
(Matriculaciones-Series-históricas, Matriculaciones-Tablas-Estadísticas) turn
out, on inspection, to be **annual only** — no monthly, province-level table
is published anywhere on the portal. What is published monthly, and does
carry a province field, is the raw matriculaciones microdata: one row per
registration transaction nationwide, as a fixed-width text file inside a
ZIP, one ZIP per month back to December 2014. This adapter downloads those
ZIPs and aggregates them to province counts itself — the same shape of
decision as MIVAU's rent_reference (dividing two published components
because the ratio itself isn't published), just one step further upstream.

**Discovering the files.** ``https://www.dgt.es/menusecundario/dgt-en-cifras/
matraba-listados/matriculaciones-automoviles-mensual.html`` lists a direct
link to every monthly ZIP published since December 2014, all on one page, no
pagination. ``discover`` parses that page rather than reconstructing the URL
pattern by hand, per the framework's own guidance — the file naming pads the
month in the filename (``..._202506.zip``) but not in the URL path
(``/2025/6/...``, not ``/2025/06/...``), which is exactly the kind of
inconsistency that punishes a guessed URL. Only the most recent
``MONTHS_BACK`` links are turned into fetch plans; the full 2014-present run
is available if a future need justifies the extra bandwidth, but "several
years" of monthly history doesn't need all eleven.

**Fixed-width layout.** The file has no delimiter — DGT's own interface
specification, "Documento de interfaz de Envío de Datos (Matriculaciones)"
(published at ``sedeapl.dgt.gob.es/IEST_INTER/pdfs/disenoRegistro/vehiculos/
matriculaciones/MATRICULACIONES_MATRABA.pdf``), lists 69 fields in order as
fixed ``CHAR(n)`` widths with no gaps between them, which is enough to derive
byte offsets by summing preceding widths — the same approach this codebase
uses for INE table ids and MIVAU CDN paths: a stable identifier, verified by
hand against real rows (province code, postal code and INE municipality code
all agreeing on the same province in every row checked), rather than
guessed. Only two fields are read: ``COD_PROVINCIA_MAT`` (byte 154:156, the
province where the registration was filed — DGT's own province-of-record
field) and ``CLAVE_TRAMITE`` (byte 156:157, the transaction type), because
those are the only two this metric needs and the file is large enough
(150MB+ uncompressed for Spain in a single month) that reading it as 69
named pandas columns would be wasteful. Only ``CLAVE_TRAMITE == "1"``
("Matriculación ordinaria y de ciclomotores") rows are counted — transfers,
temporary plates, de-registrations and plan-renove exits are excluded, so
the metric is genuinely "new registrations", not "registry transactions".

**Province codes are DGT's old plate-prefix letters, not INE numbers.** The
interface spec's own ``COD_PROVINCIA_MAT`` code table uses the historic
vehicle-plate letters (``M`` Madrid, ``B`` Barcelona, ``V`` Valencia, ``MA``
Málaga, ``SE`` Sevilla, ``IB`` Balears, ``BI`` Bizkaia, ``Z`` Zaragoza) —
this adapter maps those eight directly to INE province codes rather than
attempting a general DGT-to-INE table, since only our eight matter.

**Madrid runs far above the other seven provinces** — roughly 3-4x
Barcelona's count in every month checked — because Spain's national vehicle
fleets, leasing companies and rental-car operators register administratively
through Madrid regardless of where the vehicle is actually used. That is a
real, well-documented feature of how Spanish registration statistics work,
not a parsing bug; see the note on the metric in ``metrics.yml``.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from typing import Iterable

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.record import CanonicalRecord, province

LISTING_URL = (
    "https://www.dgt.es/menusecundario/dgt-en-cifras/matraba-listados/"
    "matriculaciones-automoviles-mensual.html"
)
_ZIP_LINK = re.compile(
    r'https://www\.dgt\.es/microdatos/salida/\d{4}/\d{1,2}/vehiculos/matriculaciones/'
    r'export_mensual_mat_(\d{6})\.zip'
)

# Fixed-width field layout, derived from DGT's own "Documento de interfaz de
# Envío de Datos (Matriculaciones)" field table (name, CHAR length, in
# document order) and verified against real rows — see module docstring.
# Only the two fields this adapter reads are named; everything else is
# folded into an anonymous filler so the byte math stays correct.
_FIELD_WIDTHS: tuple[int, ...] = (
    8, 1, 8, 30, 22, 1, 21, 2, 1, 5, 6, 6, 6, 3, 2, 2, 2, 2, 24,  # fields 1-19
    2,  # field 20: COD_PROVINCIA_VEH
)
_PROV_MAT_START = sum(_FIELD_WIDTHS)  # byte offset of field 21, COD_PROVINCIA_MAT
_PROV_MAT_END = _PROV_MAT_START + 2
_CLAVE_TRAMITE_START = _PROV_MAT_END
_CLAVE_TRAMITE_END = _CLAVE_TRAMITE_START + 1
_MIN_LINE_LEN = _CLAVE_TRAMITE_END

# DGT's historic vehicle-plate province letters -> INE province code, for our
# eight provinces only (see COD_PROVINCIA_MAT table in the interface spec).
_DGT_TO_INE_PROVINCE = {
    "M": "28",   # Madrid
    "B": "08",   # Barcelona
    "V": "46",   # Valencia/València
    "MA": "29",  # Málaga
    "SE": "41",  # Sevilla
    "IB": "07",  # Balears (Illes)
    "BI": "48",  # Bizkaia
    "Z": "50",   # Zaragoza
}

# How many of the most recent monthly files to fetch. ~4 years of monthly
# history comfortably clears the 24-observation floor for z-scoring without
# asking for the full 2014-present run every time this adapter runs.
MONTHS_BACK = 48


class DgtAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="dgt",
        publisher="Dirección General de Tráfico",
        license="Reuse permitted (attribution)",
        attribution="Source: DGT (dgt.es)",
        docs_url="https://www.dgt.es/menusecundario/dgt-en-cifras/",
        cadence="monthly",
        geo_level="province",
        max_age_days=90,
        formats=("zip",),
        kind="official",
        revisions_allowed=False,
        min_rows=0,
        notes=(
            "Vehicle registrations by province, aggregated from DGT's monthly "
            "microdata export (no pre-aggregated monthly/province table exists on "
            "the portal). Counts CLAVE_TRAMITE=1 (ordinary registrations) only."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        try:
            listing = ctx.fetcher.get(FetchPlan(url=LISTING_URL, fmt="html", label="listing"))
        except Exception as exc:  # noqa: BLE001
            raise AdapterFailure(f"dgt: could not fetch the monthly-file listing ({exc})") from exc
        if listing is None:
            raise AdapterFailure("dgt: monthly-file listing returned no content")

        html = listing.text()
        found: dict[str, str] = {}
        for match in re.finditer(_ZIP_LINK, html):
            found[match.group(1)] = match.group(0)  # yyyymm -> full url
        if not found:
            raise AdapterFailure("dgt: no monthly ZIP links found on the listing page")

        recent = sorted(found.items(), reverse=True)[:MONTHS_BACK]
        return [
            FetchPlan(
                url=url,
                fmt="zip",
                label=f"matriculaciones:{yyyymm}",
                # Interchangeable: a corrupt or unpublished month costs that
                # month, not the source.
                optional=True,
                meta={"period": f"{yyyymm[:4]}-{yyyymm[4:6]}"},
            )
            for yyyymm, url in recent
        ]

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload.content))
        except zipfile.BadZipFile as exc:
            raise AdapterFailure(f"{payload.plan.label}: not a readable ZIP ({exc})") from exc

        names = [n for n in archive.namelist() if n.lower().endswith(".txt")]
        if not names:
            raise AdapterFailure(f"{payload.plan.label}: no .txt file inside the ZIP")

        counts: Counter[str] = Counter()
        with archive.open(names[0]) as handle:
            for raw_line in handle:
                line = raw_line.decode("latin-1", errors="replace")
                if len(line) < _MIN_LINE_LEN:
                    continue
                if line[_CLAVE_TRAMITE_START:_CLAVE_TRAMITE_END] != "1":
                    continue
                prov = line[_PROV_MAT_START:_PROV_MAT_END].strip()
                counts[prov] += 1

        if not counts:
            raise AdapterFailure(f"{payload.plan.label}: no ordinary-registration rows parsed")

        return pd.DataFrame(
            [{"dgt_province": code, "count": n} for code, n in counts.items()]
        )

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        period = plan.meta["period"]
        out: list[CanonicalRecord] = []
        for row in frame.itertuples(index=False):
            ine_prov = _DGT_TO_INE_PROVINCE.get(row.dgt_province)
            if ine_prov is None:
                continue
            out.append(
                CanonicalRecord(
                    metric_id="vehicle_registrations",
                    geo_id=province(ine_prov),
                    period=period,
                    value=float(row.count),
                    unit="vehicles",
                    source_id=self.manifest.source_id,
                )
            )
        return out
