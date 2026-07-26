"""Walk-forward evaluation: does a model actually know anything?

The protocol is the boring one, chosen because the interesting ones are how
people fool themselves. Train on everything up to period T, skip an embargo gap,
forecast T + horizon, score against what happened, step forward, repeat. No
shuffling — a random split lets a model learn from next year to predict last
year, which inflates every metric and is meaningless for a series that runs
forwards.

Scores are relative, never absolute. "Mean absolute error 412 transfers" is
unreadable; "18% better than same-month-last-year" is a claim. So every model is
scored as a ratio against the best baseline, and a model that cannot beat the
baseline by the margin declared in `targets-v1.yml` is reported as having no
skill. That verdict is published rather than hidden, using the same vocabulary
the lead-lag lab already uses, so the site reads consistently:

    beats_baseline | no_better_than_baseline | insufficient_data

The asymmetry is intentional. A false "no skill" costs a feature. A false "has
skill" costs the only thing the product is actually selling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .models import BASELINES, Prediction, analog


@dataclass(slots=True)
class Fold:
    train_end: str
    target_period: str
    actual: float
    predictions: dict[str, Prediction]


@dataclass(slots=True)
class ModelScore:
    model: str
    folds: int
    mae: float
    mae_ratio: float          # readable, but not what skill is judged on
    pinball: float
    pinball_ratio: float      # vs best baseline; < 1 is better. This decides skill.
    coverage_80: float        # share of actuals inside p10..p90; 0.8 is ideal
    brier: float | None
    verdict: str
    detail: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "folds": self.folds,
            "mae": round(self.mae, 3),
            "mae_ratio": round(self.mae_ratio, 4),
            "pinball": round(self.pinball, 4),
            "pinball_ratio": round(self.pinball_ratio, 4),
            "coverage_80": round(self.coverage_80, 3),
            "brier": round(self.brier, 4) if self.brier is not None else None,
            "verdict": self.verdict,
            **self.detail,
        }


def pinball_loss(actual: float, quantiles: dict[str, float]) -> float:
    """Standard quantile loss — the scoring rule for an interval forecast.

    It penalises a miss asymmetrically by which side it fell on, which is what
    stops a model from buying good coverage with uselessly wide intervals. A
    forecast of "somewhere between zero and infinity" is always right and scores
    terribly here, correctly.
    """
    total = 0.0
    for name, predicted in quantiles.items():
        if predicted is None or math.isnan(predicted):
            return math.nan
        q = int(name[1:]) / 100
        delta = actual - predicted
        total += max(q * delta, (q - 1) * delta)
    return total / max(len(quantiles), 1)


def walk_forward(
    series: dict[str, float],
    *,
    horizon: int,
    cadence: str,
    min_train: int,
    embargo: int,
    state: dict[str, list[float]] | None = None,
) -> list[Fold]:
    periods = sorted(series)
    folds: list[Fold] = []

    for cut in range(min_train, len(periods) - horizon):
        train_periods = periods[: cut + 1 - embargo] if embargo else periods[: cut + 1]
        if len(train_periods) < min_train:
            continue
        target_period = periods[cut + horizon]
        history = [series[p] for p in train_periods]
        state_vectors = [state.get(p, []) for p in train_periods] if state else None

        predictions: dict[str, Prediction] = {}
        for name, fn in BASELINES.items():
            predictions[name] = fn(history, horizon=horizon, cadence=cadence)
        predictions["analog"] = analog(
            history, horizon=horizon, cadence=cadence, state=state_vectors
        )

        folds.append(
            Fold(
                train_end=train_periods[-1],
                target_period=target_period,
                actual=series[target_period],
                predictions=predictions,
            )
        )
    return folds


def score(folds: list[Fold], *, skill_margin: float, min_folds: int = 8) -> dict[str, ModelScore]:
    if len(folds) < min_folds:
        return {}

    names = sorted({name for fold in folds for name in fold.predictions})
    raw: dict[str, dict[str, float]] = {}

    for name in names:
        errors, losses, inside, direction = [], [], 0, []
        usable = 0
        for fold in folds:
            prediction = fold.predictions.get(name)
            if prediction is None or math.isnan(prediction.quantiles.get("p50", math.nan)):
                continue
            usable += 1
            errors.append(abs(fold.actual - prediction.point))
            loss = pinball_loss(fold.actual, prediction.quantiles)
            if not math.isnan(loss):
                losses.append(loss)
            low, high = prediction.quantiles["p10"], prediction.quantiles["p90"]
            if not (math.isnan(low) or math.isnan(high)) and low <= fold.actual <= high:
                inside += 1
            if prediction.p_direction_up is not None:
                # Realised direction relative to where the series stood at train end.
                direction.append((prediction.p_direction_up, fold))

        if usable < min_folds:
            continue
        raw[name] = {
            "folds": usable,
            "mae": sum(errors) / len(errors),
            "pinball": sum(losses) / len(losses) if losses else math.nan,
            "coverage": inside / usable,
        }

    if not raw:
        return {}

    # Skill is judged on pinball loss, not MAE. MAE only sees the midpoint, so a
    # forecast of "somewhere between 85,000 and 176,000" scores well on coverage
    # and costs nothing — which is how a model that knows nothing looks careful.
    # Pinball prices the width of the interval as well as its centre, so it is the
    # rule that matches what we actually publish. MAE is kept alongside because it
    # is the number a reader can interpret.
    baseline_pinballs = [
        stats["pinball"] for name, stats in raw.items()
        if name in BASELINES and not math.isnan(stats["pinball"])
    ]
    best_baseline = min(baseline_pinballs) if baseline_pinballs else math.nan
    baseline_maes = [stats["mae"] for name, stats in raw.items() if name in BASELINES]
    best_baseline_mae = min(baseline_maes) if baseline_maes else math.nan

    out: dict[str, ModelScore] = {}
    for name, stats in raw.items():
        ratio = (
            stats["pinball"] / best_baseline
            if best_baseline and not math.isnan(best_baseline) and not math.isnan(stats["pinball"])
            else math.nan
        )
        mae_ratio = (
            stats["mae"] / best_baseline_mae
            if best_baseline_mae and not math.isnan(best_baseline_mae) else math.nan
        )
        if name in BASELINES:
            verdict = "baseline"
        elif math.isnan(ratio):
            verdict = "insufficient_data"
        elif ratio < 1 - skill_margin:
            verdict = "beats_baseline"
        else:
            verdict = "no_better_than_baseline"
        out[name] = ModelScore(
            model=name,
            folds=int(stats["folds"]),
            mae=stats["mae"],
            mae_ratio=mae_ratio,
            pinball_ratio=ratio,
            pinball=stats["pinball"],
            coverage_80=stats["coverage"],
            brier=None,
            verdict=verdict,
        )
    return out


def choose_model(scores: dict[str, ModelScore]) -> str:
    """Which model is allowed to publish this target.

    Lowest held-out error among anything that cleared the margin; otherwise the
    best baseline. Ties go to the baseline, because a baseline is free, needs no
    explanation, and does not pretend to know anything.
    """
    def rank(s: ModelScore) -> float:
        return s.pinball if not math.isnan(s.pinball) else float("inf")

    skilled = [s for s in scores.values() if s.verdict == "beats_baseline"]
    if skilled:
        return min(skilled, key=rank).model
    baselines = [s for s in scores.values() if s.model in BASELINES]
    if baselines:
        return min(baselines, key=rank).model
    return "seasonal_naive"
