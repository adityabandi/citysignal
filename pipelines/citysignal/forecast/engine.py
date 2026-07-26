"""Issuing forecasts, freezing them, and scoring them when reality arrives.

The freeze is the product. A forecast written to `data/forecasts/` is never
edited and never deleted: it records what was predicted, when, from which data
vintage, by which model, under which frozen target definition. When the outcome
period matures, a separate pass appends the score to `data/forecasts/scores.csv`
and leaves the original file untouched.

That makes the git history the audit trail. Anyone can check out the commit that
introduced a forecast, recompute the vintage hash from the history as it stood in
that same commit, and confirm the prediction was produced from the data it
claims. Improving the record after the fact would require rewriting public
history, which is exactly the property that makes a track record worth reading —
and the reason the accuracy page can be trusted when it reports losses.

Nothing here decides whether a model is any good. `evaluate.py` does that on
held-out folds, and this module publishes whatever won, including when the winner
is "same month last year".
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from ..framework.config import Config
from ..framework.record import period_shift
from .evaluate import choose_model, score, walk_forward
from .models import BASELINES, analog
from .vintage import VintageStore

FORECAST_DIR = "forecasts"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class Target:
    target_id: str
    metric_id: str | None
    geo_level: str
    horizon: int
    unit_of_horizon: str
    question: str
    why: str
    kind: str = "level"
    direction_only: bool = False
    scoring: str | None = None
    into: tuple[str, ...] = ()


class ForecastEngine:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.spec = config.forecast_config("targets")
        self.vintage = VintageStore(config)
        self.targets = self._load_targets()

    def _load_targets(self) -> list[Target]:
        out = []
        for target_id, raw in (self.spec.get("targets") or {}).items():
            out.append(
                Target(
                    target_id=target_id,
                    metric_id=raw.get("metric"),
                    geo_level=raw.get("geo", "municipality"),
                    horizon=int(raw["horizon"]),
                    unit_of_horizon=raw.get("unit_of_horizon", "months"),
                    question=(raw.get("question") or "").strip(),
                    why=(raw.get("why") or "").strip(),
                    kind=raw.get("kind", "level"),
                    direction_only=bool(raw.get("direction_only")),
                    scoring=raw.get("scoring"),
                    into=tuple(raw.get("into") or ()),
                )
            )
        return out

    # ---- geography -------------------------------------------------------
    def _geo_id(self, target: Target, city) -> str:
        return {
            "municipality": city.geo_id,
            "province": city.province_geo_id,
            "ccaa": city.ccaa_geo_id,
        }.get(target.geo_level, city.geo_id)

    # ---- the run ---------------------------------------------------------
    def run(self, *, city_slug: str = "madrid", as_of: date | None = None) -> dict:
        """Evaluate every target, then freeze a forecast for each that can carry one."""
        city = self.config.city(city_slug)
        when = as_of or date.today()
        visible = self.vintage.as_of(when)
        vintage_hash = self.vintage.vintage_hash(when)
        basis = self.vintage.basis_summary(when)

        results = []
        for target in self.targets:
            if target.kind != "level" or not target.metric_id:
                # Regime-transition and ranking targets need their own machinery;
                # declared now, scored once the level targets have a record.
                results.append({
                    "target_id": target.target_id,
                    "status": "not_implemented",
                    "question": target.question,
                    "why": target.why,
                    "kind": target.kind,
                })
                continue

            geo_id = self._geo_id(target, city)
            series = visible.get((target.metric_id, geo_id), {})
            meta = self.config.metrics.get(target.metric_id, {})
            cadence = meta.get("cadence", "monthly")
            min_train = int((self.spec.get("min_train") or {}).get(cadence, 24))
            embargo = int((self.spec.get("embargo") or {}).get(cadence, 1))

            if len(series) < min_train + target.horizon + 4:
                results.append({
                    "target_id": target.target_id,
                    "status": "insufficient_data",
                    "question": target.question,
                    "why": target.why,
                    "metric_id": target.metric_id,
                    "geo_id": geo_id,
                    "geo_level": target.geo_level,
                    "observations": len(series),
                    "needed": min_train + target.horizon + 4,
                })
                continue

            folds = walk_forward(
                series,
                horizon=target.horizon,
                cadence=cadence,
                min_train=min_train,
                embargo=embargo,
            )
            scores = score(folds, skill_margin=float(self.spec.get("skill_margin", 0.05)))
            if not scores:
                results.append({
                    "target_id": target.target_id,
                    "status": "insufficient_data",
                    "question": target.question,
                    "why": target.why,
                    "observations": len(series),
                    "folds": len(folds),
                })
                continue

            winner = choose_model(scores)
            history = [series[p] for p in sorted(series)]
            fn = BASELINES.get(winner)
            prediction = (
                fn(history, horizon=target.horizon, cadence=cadence)
                if fn
                else analog(history, horizon=target.horizon, cadence=cadence)
            )

            latest_period = max(series)
            for_period = period_shift(latest_period, target.horizon)

            record = {
                "target_id": target.target_id,
                "question": target.question,
                "why": target.why,
                "metric_id": target.metric_id,
                "geo_id": geo_id,
                "geo_level": target.geo_level,
                "city": city_slug,
                "cadence": cadence,
                "horizon": target.horizon,
                "unit_of_horizon": target.unit_of_horizon,
                "unit": meta.get("unit"),
                "issued_at": _now(),
                "as_of": when.isoformat(),
                "from_period": latest_period,
                "for_period": for_period,
                "last_observed": history[-1],
                "model": prediction.model,
                "model_verdict": scores[winner].verdict if winner in scores else "unknown",
                "targets_version": self.spec.get("version", "targets-v1"),
                "data_vintage": vintage_hash,
                "vintage_basis": basis,
                "direction_only": target.direction_only,
                "p_direction_up": prediction.p_direction_up,
                "quantiles": None if target.direction_only else {
                    k: round(v, 3) for k, v in prediction.quantiles.items()
                },
                "skill": {name: s.to_dict() for name, s in sorted(scores.items())},
                "status": "issued",
            }
            results.append(record)

        return {
            "generated_at": _now(),
            "as_of": when.isoformat(),
            "city": city_slug,
            "data_vintage": vintage_hash,
            "vintage_basis": basis,
            "revisions_in_history": self.vintage.revision_count(),
            "targets_version": self.spec.get("version"),
            "forecasts": results,
        }

    # ---- freezing --------------------------------------------------------
    def freeze(self, payload: dict) -> list[Path]:
        """Write each issued forecast to its own immutable file.

        A file that already exists is left exactly as it was. Re-running in the
        same period must never change a published prediction — that is the whole
        guarantee — so the only thing a second run can do is nothing.
        """
        written: list[Path] = []
        base = self.config.data_dir / FORECAST_DIR
        for record in payload["forecasts"]:
            if record.get("status") != "issued":
                continue
            directory = base / record["target_id"]
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{record['from_period']}.json"
            if path.exists():
                continue  # already frozen; never rewrite
            path.write_text(
                json.dumps(record, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            written.append(path)
        return written

    # ---- scoring matured forecasts ---------------------------------------
    def score_matured(self) -> dict:
        """Compare frozen forecasts against what actually happened.

        Only forecasts whose outcome period has both arrived and been published
        are scored; the rest stay pending. Appends to scores.csv and never edits
        the frozen forecast itself.
        """
        base = self.config.data_dir / FORECAST_DIR
        if not base.exists():
            return {"scored": 0, "pending": 0}

        actuals = self.vintage.as_of(date.today())
        scores_path = base / "scores.csv"
        existing: set[tuple[str, str]] = set()
        rows: list[dict] = []
        if scores_path.exists():
            with scores_path.open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    rows.append(row)
                    existing.add((row["target_id"], row["from_period"]))

        scored = pending = 0
        for path in sorted(base.glob("*/*.json")):
            record = json.loads(path.read_text())
            key = (record["target_id"], record["from_period"])
            if key in existing:
                continue
            series = actuals.get((record["metric_id"], record["geo_id"]), {})
            actual = series.get(record["for_period"])
            if actual is None:
                pending += 1
                continue

            quantiles = record.get("quantiles") or {}
            p50 = quantiles.get("p50")
            realised_up = actual > record["last_observed"]
            rows.append({
                "target_id": record["target_id"],
                "from_period": record["from_period"],
                "for_period": record["for_period"],
                "model": record["model"],
                "issued_at": record["issued_at"],
                "predicted_p50": "" if p50 is None else round(p50, 3),
                "actual": round(actual, 3),
                "abs_error": "" if p50 is None else round(abs(actual - p50), 3),
                "pct_error": "" if not p50 else round((actual - p50) / abs(p50) * 100, 2),
                "inside_80": (
                    ""
                    if not quantiles
                    else int(quantiles.get("p10", 0) <= actual <= quantiles.get("p90", 0))
                ),
                "p_direction_up": record.get("p_direction_up") or "",
                "direction_correct": (
                    ""
                    if record.get("p_direction_up") is None
                    else int((record["p_direction_up"] > 0.5) == realised_up)
                ),
                "scored_at": _now(),
            })
            scored += 1

        if rows:
            fields = list(rows[0].keys())
            scores_path.parent.mkdir(parents=True, exist_ok=True)
            with scores_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
                writer.writeheader()
                writer.writerows(sorted(rows, key=lambda r: (r["target_id"], r["from_period"])))

        return {"scored": scored, "pending": pending, "total": len(rows)}
