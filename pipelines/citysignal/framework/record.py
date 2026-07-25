"""Canonical record, period grammar and geography identifiers.

Every value that reaches ``data/history`` is one CanonicalRecord. The grammar here
is deliberately strict: a period must match the cadence its metric declares, and a
geo_id must name the level it was actually published at. Silently promoting a
provincial series to "the city" is the failure mode this module exists to prevent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from typing import Iterable, Literal

GeoLevel = Literal[
    "municipality",
    "district",
    "barrio",
    "province",
    "ccaa",
    "nation",
    "airport",
    "port",
    "fua",
]

Cadence = Literal["daily", "weekly", "monthly", "quarterly", "annual"]

QualityFlag = Literal["ok", "revised", "suspect", "estimated", "stale_denominator"]

CSV_COLUMNS = (
    "metric_id",
    "geo_id",
    "geo_level",
    "period",
    "value",
    "unit",
    "source_id",
    "observation_end",
    "published_at",
    "fetched_at",
    "quality_flag",
    "revision",
)

_PERIOD_PATTERNS: dict[str, re.Pattern[str]] = {
    "annual": re.compile(r"^\d{4}$"),
    "quarterly": re.compile(r"^\d{4}-Q[1-4]$"),
    "monthly": re.compile(r"^\d{4}-(0[1-9]|1[0-2])$"),
    "weekly": re.compile(r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$"),
    "daily": re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
}

_GEO_PATTERNS: dict[str, re.Pattern[str]] = {
    "municipality": re.compile(r"^mun-\d{5}$"),
    "district": re.compile(r"^dist-\d{5}-\d{2}$"),
    "barrio": re.compile(r"^barrio-\d{5}-\d{3}$"),
    "province": re.compile(r"^prov-\d{2}$"),
    "ccaa": re.compile(r"^ccaa-\d{2}$"),
    "nation": re.compile(r"^(es|eu)$"),
    "airport": re.compile(r"^apt-[A-Z]{3}$"),
    "port": re.compile(r"^port-[A-Z]{3}$"),
    "fua": re.compile(r"^fua-[A-Z]{2}\d{3}[A-Z]\d$"),
}


class RecordError(ValueError):
    """Raised when a record violates the canonical grammar."""


def period_cadence(period: str) -> Cadence:
    """Infer cadence from a period string, raising if it matches nothing."""
    for cadence, pattern in _PERIOD_PATTERNS.items():
        if pattern.match(period):
            return cadence  # type: ignore[return-value]
    raise RecordError(f"unparseable period: {period!r}")


def validate_period(period: str, cadence: Cadence) -> None:
    pattern = _PERIOD_PATTERNS.get(cadence)
    if pattern is None:
        raise RecordError(f"unknown cadence: {cadence!r}")
    if not pattern.match(period):
        raise RecordError(f"period {period!r} does not match cadence {cadence!r}")


def period_end(period: str) -> date:
    """Last calendar day covered by a period. Used for freshness arithmetic."""
    cadence = period_cadence(period)
    if cadence == "annual":
        return date(int(period), 12, 31)
    if cadence == "quarterly":
        year, quarter = int(period[:4]), int(period[-1])
        month = quarter * 3
        return _month_end(year, month)
    if cadence == "monthly":
        return _month_end(int(period[:4]), int(period[5:7]))
    if cadence == "weekly":
        year, week = int(period[:4]), int(period[6:])
        return date.fromisocalendar(year, week, 7)
    return date.fromisoformat(period)


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - _ONE_DAY


def period_shift(period: str, periods: int) -> str:
    """Shift a period by n steps of its own cadence. Calendar-aware."""
    cadence = period_cadence(period)
    if cadence == "annual":
        return f"{int(period) + periods:04d}"
    if cadence == "quarterly":
        year, quarter = int(period[:4]), int(period[-1])
        total = (year * 4 + (quarter - 1)) + periods
        return f"{total // 4:04d}-Q{total % 4 + 1}"
    if cadence == "monthly":
        year, month = int(period[:4]), int(period[5:7])
        total = (year * 12 + (month - 1)) + periods
        return f"{total // 12:04d}-{total % 12 + 1:02d}"
    if cadence == "weekly":
        anchor = date.fromisocalendar(int(period[:4]), int(period[6:]), 1)
        shifted = anchor + _ONE_DAY * 7 * periods
        iso = shifted.isocalendar()
        return f"{iso.year:04d}-W{iso.week:02d}"
    return (date.fromisoformat(period) + _ONE_DAY * periods).isoformat()


def periods_per_year(cadence: Cadence) -> int:
    return {"annual": 1, "quarterly": 4, "monthly": 12, "weekly": 52, "daily": 365}[cadence]


from datetime import timedelta as _timedelta  # noqa: E402  (kept local to the arithmetic above)

_ONE_DAY = _timedelta(days=1)


def geo_level_of(geo_id: str) -> GeoLevel:
    for level, pattern in _GEO_PATTERNS.items():
        if pattern.match(geo_id):
            return level  # type: ignore[return-value]
    raise RecordError(f"unparseable geo_id: {geo_id!r}")


def validate_geo(geo_id: str, geo_level: str) -> None:
    pattern = _GEO_PATTERNS.get(geo_level)
    if pattern is None:
        raise RecordError(f"unknown geo_level: {geo_level!r}")
    if not pattern.match(geo_id):
        raise RecordError(f"geo_id {geo_id!r} does not match level {geo_level!r}")


def municipality(ine_code: str) -> str:
    return f"mun-{str(ine_code).zfill(5)}"


def province(ine_code: str) -> str:
    return f"prov-{str(ine_code).zfill(2)}"


def ccaa(ine_code: str) -> str:
    return f"ccaa-{str(ine_code).zfill(2)}"


def district(ine_mun: str, district_code: str | int) -> str:
    return f"dist-{str(ine_mun).zfill(5)}-{str(district_code).zfill(2)}"


def barrio(ine_mun: str, barrio_code: str | int) -> str:
    return f"barrio-{str(ine_mun).zfill(5)}-{str(barrio_code).zfill(3)}"


def airport(iata: str) -> str:
    return f"apt-{iata.upper()}"


def port(code: str) -> str:
    return f"port-{code.upper()}"


NATION = "es"


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


@dataclass(frozen=True, slots=True)
class CanonicalRecord:
    metric_id: str
    geo_id: str
    period: str
    value: float | None
    unit: str
    source_id: str
    geo_level: GeoLevel | None = None
    observation_end: str | None = None
    published_at: str | None = None
    fetched_at: str | None = None
    quality_flag: QualityFlag = "ok"
    revision: int = 0

    def __post_init__(self) -> None:
        level = self.geo_level or geo_level_of(self.geo_id)
        validate_geo(self.geo_id, level)
        period_cadence(self.period)  # raises on garbage
        object.__setattr__(self, "geo_level", level)
        if self.observation_end is None:
            object.__setattr__(self, "observation_end", period_end(self.period).isoformat())
        if self.fetched_at is None:
            object.__setattr__(self, "fetched_at", utc_today().isoformat())
        if self.value is not None:
            object.__setattr__(self, "value", float(self.value))

    @property
    def key(self) -> tuple[str, str, str]:
        """Identity of an observation, ignoring revision."""
        return (self.metric_id, self.geo_id, self.period)

    def to_row(self) -> dict[str, object]:
        row = asdict(self)
        return {column: row[column] for column in CSV_COLUMNS}


def records_to_rows(records: Iterable[CanonicalRecord]) -> list[dict[str, object]]:
    return [record.to_row() for record in records]
