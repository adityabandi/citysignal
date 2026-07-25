"""Reading committed history back out, and resolving which geography a city's
version of a metric actually lives at.

This is where the product's central honesty rule is enforced in code: a metric
declares the level it is published at, and a city gets the series at that level —
province foreclosures stay province foreclosures. The scope travels with the
series all the way to the chart label, so nothing can quietly become "Madrid's
number" on the way.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..framework.config import City, Config
from ..framework.history import current_view


@dataclass(frozen=True, slots=True)
class SeriesKey:
    metric_id: str
    geo_id: str
    geo_level: str


@dataclass(slots=True)
class Series:
    key: SeriesKey
    values: dict[str, float]
    source_id: str
    unit: str
    observation_end: str | None = None
    fetched_at: str | None = None
    quality_flags: dict[str, str] | None = None

    @property
    def latest_period(self) -> str | None:
        return max(self.values) if self.values else None

    @property
    def latest_value(self) -> float | None:
        period = self.latest_period
        return self.values[period] if period else None


SCOPE_LABELS = {
    "municipality": "municipality",
    "district": "district",
    "barrio": "neighbourhood",
    "province": "province",
    "ccaa": "autonomous community",
    "nation": "Spain",
    "airport": "airport",
    "port": "port",
    "fua": "functional urban area",
}


class HistoryStore:
    """Every committed series, indexed by (metric_id, geo_id)."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._series: dict[tuple[str, str], Series] = {}
        self._load()

    def _load(self) -> None:
        history_dir = self.config.data_dir / "history"
        if not history_dir.exists():
            return
        for path in sorted(history_dir.glob("*/*.csv")):
            with path.open(newline="", encoding="utf-8") as handle:
                rows = current_view(list(csv.DictReader(handle)))
            for row in rows:
                if not row.get("value"):
                    continue
                key = (row["metric_id"], row["geo_id"])
                series = self._series.get(key)
                if series is None:
                    series = Series(
                        key=SeriesKey(row["metric_id"], row["geo_id"], row["geo_level"]),
                        values={},
                        source_id=row["source_id"],
                        unit=row["unit"],
                        quality_flags={},
                    )
                    self._series[key] = series
                try:
                    series.values[row["period"]] = float(row["value"])
                except ValueError:
                    continue
                if row.get("quality_flag") and row["quality_flag"] != "ok":
                    series.quality_flags[row["period"]] = row["quality_flag"]  # type: ignore[index]
                if row.get("fetched_at"):
                    series.fetched_at = max(series.fetched_at or "", row["fetched_at"])

        for series in self._series.values():
            series.observation_end = series.latest_period

    def __iter__(self) -> Iterator[Series]:
        return iter(self._series.values())

    def get(self, metric_id: str, geo_id: str) -> Series | None:
        return self._series.get((metric_id, geo_id))

    def geo_for(self, city: City, metric_id: str) -> str | None:
        """Which geography carries this metric for this city."""
        meta = self.config.metrics.get(metric_id)
        if meta is None:
            return None
        level = meta["geo_level"]
        if level == "municipality":
            return city.geo_id
        if level == "province":
            return city.province_geo_id
        if level == "ccaa":
            return city.ccaa_geo_id
        if level == "nation":
            return "es"
        if level == "airport":
            return f"apt-{city.airports[0]}" if city.airports else None
        if level == "port":
            return f"port-{city.ports[0]}" if city.ports else None
        if level in {"district", "barrio"}:
            return None  # sub-city series are handled by the map, not the city summary
        return None

    def for_city(self, city: City, metric_id: str) -> Series | None:
        geo_id = self.geo_for(city, metric_id)
        if geo_id is None:
            return None
        return self.get(metric_id, geo_id)

    def districts_of(self, city: City, metric_id: str) -> list[Series]:
        prefix = f"dist-{city.ine_mun}-"
        return [s for (m, g), s in self._series.items() if m == metric_id and g.startswith(prefix)]

    @property
    def metric_ids(self) -> set[str]:
        return {key[0] for key in self._series}
