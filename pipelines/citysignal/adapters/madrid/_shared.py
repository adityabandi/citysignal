"""Small helpers shared by the Madrid district adapters.

Nothing here subclasses ``BaseAdapter`` — the registry only picks up adapter
classes defined directly in a module (see ``framework/registry.py``), so this
file is free to hold plain functions without being mistaken for a fourth
source.
"""

from __future__ import annotations

import re
import struct
import unicodedata
from datetime import date

# Madrid's 21 districts: 2-digit code -> accent-stripped, upper-cased name, as
# printed by datos.madrid.es' own district-boundary dataset (300497). Every
# Madrid source spells these slightly differently (accents, "FUENCARRAL-EL
# PARDO" vs "Fuencarral - El Pardo"), so adapters normalise through
# ``normalize_district_name`` before looking a name up here rather than
# matching source strings directly.
MADRID_DISTRICTS: tuple[tuple[str, str], ...] = (
    ("01", "CENTRO"),
    ("02", "ARGANZUELA"),
    ("03", "RETIRO"),
    ("04", "SALAMANCA"),
    ("05", "CHAMARTIN"),
    ("06", "TETUAN"),
    ("07", "CHAMBERI"),
    ("08", "FUENCARRAL-EL PARDO"),
    ("09", "MONCLOA-ARAVACA"),
    ("10", "LATINA"),
    ("11", "CARABANCHEL"),
    ("12", "USERA"),
    ("13", "PUENTE DE VALLECAS"),
    ("14", "MORATALAZ"),
    ("15", "CIUDAD LINEAL"),
    ("16", "HORTALEZA"),
    ("17", "VILLAVERDE"),
    ("18", "VILLA DE VALLECAS"),
    ("19", "VICALVARO"),
    ("20", "SAN BLAS-CANILLEJAS"),
    ("21", "BARAJAS"),
)
DISTRICT_CODE_BY_NAME: dict[str, str] = {name: code for code, name in MADRID_DISTRICTS}


def normalize_district_name(raw: str) -> str:
    """'Distrito de ChamartÃ­n' / 'Fuencarral - El Pardo' -> 'CHAMARTIN' / 'FUENCARRAL-EL PARDO'."""
    text = raw.replace("Distrito de", "").replace("DISTRITO DE", "")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"\s+", " ", text).strip().upper()
    return text


def district_code_from_name(raw: str) -> str | None:
    return DISTRICT_CODE_BY_NAME.get(normalize_district_name(raw))


SPANISH_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def period_from_spanish_label(label: str) -> str:
    """'2026-Julio' (datos.madrid.es' own catalogue key) -> '2026-07'."""
    year, month_name = label.split("-", 1)
    month = SPANISH_MONTHS[month_name.strip().lower()]
    return f"{int(year):04d}-{month:02d}"


def decode_spanish_text(content: bytes) -> str:
    """Madrid open-data CSVs are inconsistent about encoding — some are UTF-8
    with a BOM, older ones plain UTF-8, and the general rule of thumb for
    Spanish municipal portals is latin-1/cp1252. Try them in that order rather
    than assume."""
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Minimal pure-Python dBASE III (.dbf) reader.
#
# The VUT geoportal dataset ships as an ESRI shapefile bundle. The project has
# no geopandas/pyshp/fiona dependency and this adapter only needs the
# attribute table (district, units), not the point geometry, so a ~40-line
# reader for the well-documented dBASE III record format is cheaper than
# adding a GIS dependency for one field lookup.
# ---------------------------------------------------------------------------


def read_dbf(data: bytes) -> list[dict[str, str]]:
    header_len = struct.unpack("<H", data[8:10])[0]
    record_len = struct.unpack("<H", data[10:12])[0]
    num_records = struct.unpack("<I", data[4:8])[0]

    fields: list[tuple[str, int]] = []
    pos = 32
    while data[pos] != 0x0D:  # 0x0D marks the end of the field descriptor array
        name = data[pos : pos + 11].split(b"\x00")[0].decode("latin-1")
        length = data[pos + 16]
        fields.append((name, length))
        pos += 32

    rows: list[dict[str, str]] = []
    offset = header_len
    for _ in range(num_records):
        record = data[offset : offset + record_len]
        offset += record_len
        if not record or record[0:1] == b"*":  # deletion flag
            continue
        fpos = 1
        row: dict[str, str] = {}
        for name, length in fields:
            raw = record[fpos : fpos + length]
            row[name] = raw.decode("utf-8", errors="replace").strip()
            fpos += length
        rows.append(row)
    return rows


def parse_dbf_date(value: str) -> date | None:
    value = value.strip()
    if len(value) == 8 and value.isdigit():
        return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))
    return None
