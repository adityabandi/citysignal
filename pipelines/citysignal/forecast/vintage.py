"""Reconstructing what was knowable on a given date.

A backtest that reads today's history is not a backtest. It is a description of
the past written with tomorrow's newspaper open on the desk, and it will report
skill that evaporates the moment the model forecasts something real.

Two mechanisms keep that from happening here.

**Revisions.** The history store is append-only: when a publisher restates a
figure, the old row survives and a new one arrives with `revision + 1`. So the
value *as first published* is still on disk, and a vintage view can pick the
highest revision that existed at time T rather than the newest one that exists
now. Spanish housing statistics are revised routinely and sometimes heavily, so
this is not a hypothetical.

**Publication lag.** Knowing which revision existed requires knowing when it
appeared, and `published_at` is recorded for exactly one of our seventeen sources.
For the rest, availability is modelled: period end plus a declared lag from
`config/forecast/publication-lags.yml`. That is an assumption, and every fold and
frozen forecast records which basis it used — `published_at` where a real date
exists, `declared_lag` where the lag stood in for one. A reader can tell the
difference, which matters more than the estimate being perfect.

The lags err long. Assuming a number arrived later than it did makes a model look
worse than it is; for a product whose entire claim is a public track record, that
is the only safe direction to be wrong in.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from ..framework.config import Config
from ..framework.record import period_end


@dataclass(frozen=True, slots=True)
class VintageRow:
    metric_id: str
    geo_id: str
    period: str
    value: float
    revision: int
    source_id: str
    basis: str  # "published_at" | "declared_lag"


class VintageStore:
    """Every history row, tagged with the date it became knowable."""

    def __init__(self, config: Config) -> None:
        self.config = config
        lag_config = config.forecast_config("publication-lags")
        self._lags: dict[str, int] = dict(lag_config.get("lags", {}))
        self._default_lag: int = int(lag_config.get("default_lag_days", 60))
        self._rows: list[tuple[date, VintageRow]] = []
        self._load()

    # ---- loading ---------------------------------------------------------
    def _load(self) -> None:
        history_dir = self.config.data_dir / "history"
        if not history_dir.exists():
            return
        for path in sorted(history_dir.glob("*/*.csv")):
            source_id = path.parent.name
            # A disabled source is withheld from the site, so it must be withheld
            # from the models too — otherwise a forecast depends on evidence a
            # reader cannot inspect.
            if not self.config.sources.get(source_id, {}).get("enabled", True):
                continue
            with path.open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    parsed = self._parse(row, source_id)
                    if parsed is not None:
                        self._rows.append(parsed)
        self._rows.sort(key=lambda pair: (pair[0], pair[1].metric_id, pair[1].geo_id))

    def _parse(self, row: dict[str, str], source_id: str) -> tuple[date, VintageRow] | None:
        raw_value = row.get("value")
        if not raw_value:
            return None
        try:
            value = float(raw_value)
        except ValueError:
            return None

        published = (row.get("published_at") or "").strip()
        if published:
            try:
                known_from = date.fromisoformat(published[:10])
                basis = "published_at"
            except ValueError:
                known_from, basis = self._lagged(row["period"], source_id), "declared_lag"
        else:
            known_from, basis = self._lagged(row["period"], source_id), "declared_lag"

        return known_from, VintageRow(
            metric_id=row["metric_id"],
            geo_id=row["geo_id"],
            period=row["period"],
            value=value,
            revision=int(row.get("revision") or 0),
            source_id=source_id,
            basis=basis,
        )

    def _lagged(self, period: str, source_id: str) -> date:
        lag = self._lags.get(source_id, self._default_lag)
        return period_end(period) + timedelta(days=lag)

    # ---- the point of the class -----------------------------------------
    def as_of(self, when: date) -> dict[tuple[str, str], dict[str, float]]:
        """Series as they stood on `when`, keyed by (metric_id, geo_id).

        Where a period has several revisions, the latest one that had appeared by
        `when` wins — which is the number a forecaster would actually have had,
        not the corrected figure published months later.
        """
        chosen: dict[tuple[str, str, str], tuple[int, float]] = {}
        for known_from, row in self._rows:
            if known_from > when:
                break  # rows are date-sorted, so nothing later can qualify
            key = (row.metric_id, row.geo_id, row.period)
            existing = chosen.get(key)
            if existing is None or row.revision > existing[0]:
                chosen[key] = (row.revision, row.value)

        out: dict[tuple[str, str], dict[str, float]] = {}
        for (metric_id, geo_id, period), (_, value) in chosen.items():
            out.setdefault((metric_id, geo_id), {})[period] = value
        return out

    def vintage_hash(self, when: date) -> str:
        """A fingerprint of exactly what the model was allowed to see.

        Committed with every frozen forecast. Anyone can recompute it from the
        repository at that commit and confirm the forecast was produced from the
        data it claims — the difference between a track record and a scoreboard
        somebody typed.
        """
        digest = hashlib.sha256()
        for known_from, row in self._rows:
            if known_from > when:
                break
            digest.update(
                f"{row.metric_id}|{row.geo_id}|{row.period}|{row.revision}|{row.value}\n".encode()
            )
        return f"sha256:{digest.hexdigest()[:32]}"

    def basis_summary(self, when: date) -> dict[str, int]:
        """How many visible rows rest on a real publication date vs an assumed lag."""
        counts = {"published_at": 0, "declared_lag": 0}
        for known_from, row in self._rows:
            if known_from > when:
                break
            counts[row.basis] += 1
        return counts

    def revision_count(self) -> int:
        """Rows that are restatements rather than first publications."""
        return sum(1 for _, row in self._rows if row.revision > 0)
