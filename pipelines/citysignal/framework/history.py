"""The committed history store.

One CSV per (source_id, metric_id), stably sorted, atomically written, so a run
that changes nothing produces a zero-byte git diff. That property is what makes
the git log usable as an audit trail rather than noise.

History is append-only. A publisher that restates a past value gets a new row
with revision+1 when the manifest allows revisions, and a quarantine entry when
it does not — we never overwrite a number we already published.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .record import CSV_COLUMNS, CanonicalRecord


@dataclass(slots=True)
class MergeOutcome:
    added: int = 0
    revised: int = 0
    unchanged: int = 0
    quarantined: list[dict[str, object]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.quarantined is None:
            self.quarantined = []

    @property
    def changed(self) -> bool:
        return bool(self.added or self.revised)


def history_path(root: Path, source_id: str, metric_id: str) -> Path:
    return root / "history" / source_id / f"{metric_id}.csv"


def read_history(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _sort_key(row: dict[str, str]) -> tuple:
    return (
        row.get("geo_id", ""),
        row.get("period", ""),
        int(row.get("revision") or 0),
    )


def write_history(path: Path, rows: Sequence[dict[str, object]]) -> None:
    """Atomic, stably sorted, LF-terminated write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=_sort_key)  # type: ignore[arg-type]
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
        writer.writeheader()
        for row in ordered:
            writer.writerow({column: _fmt(row.get(column)) for column in CSV_COLUMNS})
    os.replace(tmp, path)


def _fmt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if value != value:  # NaN
            return ""
        # Trim float noise so re-runs are byte-identical.
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def _values_equal(existing: str, incoming: object) -> bool:
    return existing == _fmt(incoming)


def merge_records(
    path: Path,
    records: Iterable[CanonicalRecord],
    *,
    revisions_allowed: bool,
) -> MergeOutcome:
    """Merge new observations into a metric's history file."""
    existing = read_history(path)
    outcome = MergeOutcome()

    # Current view: highest revision per (geo_id, period).
    latest: dict[tuple[str, str], dict[str, str]] = {}
    for row in existing:
        key = (row["geo_id"], row["period"])
        current = latest.get(key)
        if current is None or int(row.get("revision") or 0) >= int(current.get("revision") or 0):
            latest[key] = row

    merged: list[dict[str, object]] = list(existing)  # type: ignore[arg-type]
    seen_this_run: set[tuple[str, str]] = set()

    for record in records:
        key = (record.geo_id, record.period)
        if key in seen_this_run:
            continue  # adapter emitted a duplicate; first one wins
        seen_this_run.add(key)
        prior = latest.get(key)

        if prior is None:
            merged.append(record.to_row())
            outcome.added += 1
            continue

        if _values_equal(prior.get("value", ""), record.value):
            outcome.unchanged += 1
            continue

        if not revisions_allowed:
            outcome.quarantined.append(
                {
                    **record.to_row(),
                    "prior_value": prior.get("value"),
                    "reason": "unexpected_revision",
                }
            )
            continue

        revised = record.to_row()
        revised["revision"] = int(prior.get("revision") or 0) + 1
        revised["quality_flag"] = "revised"
        # Preserve the original ingestion date of the series point we are restating.
        merged.append(revised)
        outcome.revised += 1

    if outcome.changed:
        write_history(path, merged)
    elif not path.exists() and merged:
        write_history(path, merged)

    return outcome


def current_view(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    """Collapse a history file to one row per (geo_id, period): the latest revision."""
    latest: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        key = (row["geo_id"], row["period"])
        current = latest.get(key)
        if current is None or int(row.get("revision") or 0) >= int(current.get("revision") or 0):
            latest[key] = row
    return sorted(latest.values(), key=_sort_key)


def append_quarantine(root: Path, source_id: str, rows: Sequence[dict[str, object]]) -> None:
    if not rows:
        return
    path = root / "quality" / "quarantine" / f"{source_id}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(CSV_COLUMNS) + ["prior_value", "reason"]
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow({key: _fmt(row.get(key)) for key in fieldnames})
