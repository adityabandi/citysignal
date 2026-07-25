"""Run reporting and the public data-health manifest."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .record import period_end, utc_today


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class AdapterResult:
    source_id: str
    status: str = "ok"  # ok | skipped | failed | partial
    records: int = 0
    added: int = 0
    revised: int = 0
    unchanged: int = 0
    quarantined: int = 0
    plans: int = 0
    skipped_unchanged: int = 0
    error: str | None = None
    error_type: str | None = None
    duration_s: float = 0.0
    latest_observation: str | None = None
    metrics: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HealthStore:
    """data/quality/health.json — what the public Sources page renders."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, Any] = {"sources": {}}
        if path.exists():
            try:
                self._data = json.loads(path.read_text())
            except json.JSONDecodeError:
                self._data = {"sources": {}}
        self._data.setdefault("sources", {})

    def record(self, result: AdapterResult, *, manifest: dict[str, Any]) -> None:
        entry = self._data["sources"].setdefault(result.source_id, {})
        checked = now_iso()
        entry["source_id"] = result.source_id
        entry["last_checked"] = checked
        entry["publisher"] = manifest.get("publisher")
        entry["license"] = manifest.get("license")
        entry["attribution"] = manifest.get("attribution")
        entry["cadence"] = manifest.get("cadence")
        entry["geo_level"] = manifest.get("geo_level")
        entry["max_age_days"] = manifest.get("max_age_days")
        entry["docs_url"] = manifest.get("docs_url")
        entry["kind"] = manifest.get("kind", "official")

        if result.status in {"ok", "skipped", "partial"}:
            entry["last_success"] = checked
            entry["consecutive_failures"] = 0
            entry["last_error"] = None
            if result.latest_observation:
                entry["latest_observation"] = result.latest_observation
        else:
            entry["consecutive_failures"] = int(entry.get("consecutive_failures") or 0) + 1
            entry["last_error"] = result.error
            entry["last_error_type"] = result.error_type

        entry["status"] = result.status
        entry["metrics"] = result.metrics or entry.get("metrics", [])
        entry["staleness_days"] = self._staleness(entry.get("latest_observation"))
        entry["fresh"] = (
            entry["staleness_days"] is not None
            and manifest.get("max_age_days") is not None
            and entry["staleness_days"] <= manifest["max_age_days"]
        )

    @staticmethod
    def _staleness(latest_observation: str | None) -> int | None:
        if not latest_observation:
            return None
        try:
            end = period_end(latest_observation)
        except Exception:  # noqa: BLE001 - a stored ISO date is also acceptable
            try:
                end = datetime.fromisoformat(latest_observation).date()
            except ValueError:
                return None
        return (utc_today() - end).days

    def save(self) -> None:
        self._data["generated_at"] = now_iso()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=1, sort_keys=True, ensure_ascii=False) + "\n")

    @property
    def data(self) -> dict[str, Any]:
        return self._data


def write_run_report(path: Path, results: list[AdapterResult], *, run_id: str) -> None:
    """Consumed by scripts/issue_sync.py to open/close one issue per broken adapter."""
    payload = {
        "run_id": run_id,
        "finished_at": now_iso(),
        "ok": [r.source_id for r in results if r.status in {"ok", "skipped"}],
        "failed": [r.source_id for r in results if r.status == "failed"],
        "partial": [r.source_id for r in results if r.status == "partial"],
        "results": [r.to_dict() for r in results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
