"""The lead-lag lab: does a candidate leading signal actually lead anything?

Search interest, news volume and Wikipedia attention are easy to publish and easy
to over-claim. With enough candidate series, something will correlate by accident.
So every "leading" label here has to be earned against three constraints:

* the pairs tested are declared in config, not chosen after seeing the answer;
* correlation is measured on a held-out tail the ranking never saw, so a lag that
  only worked in-sample is visible as such;
* the winning lag must beat a seasonal-naive baseline — "this month looks like
  the same month last year" — because most of what a city series does is season.

A signal that stops beating the baseline loses its label in public and keeps its
history. That is the whole point: the lab is allowed to report that a signal we
liked does not work.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Mapping

from ..framework.config import City, Config
from .store import HistoryStore
from .transforms import yoy


@dataclass(slots=True)
class LeadLagResult:
    signal_metric: str
    outcome_metric: str
    city: str
    best_lag: int | None
    in_sample_r: float | None
    holdout_r: float | None
    baseline_r: float | None
    observations: int
    verdict: str
    """leading | coincident | no_evidence | insufficient_data"""

    def to_dict(self) -> dict[str, object]:
        return {
            "signal_metric": self.signal_metric,
            "outcome_metric": self.outcome_metric,
            "city": self.city,
            "best_lag": self.best_lag,
            "in_sample_r": self.in_sample_r,
            "holdout_r": self.holdout_r,
            "baseline_r": self.baseline_r,
            "observations": self.observations,
            "verdict": self.verdict,
        }


def correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    sx, sy = pstdev(xs), pstdev(ys)
    if sx == 0 or sy == 0 or math.isnan(sx) or math.isnan(sy):
        return None
    mx, my = mean(xs), mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / len(xs)
    return round(cov / (sx * sy), 4)


def _aligned(
    signal: Mapping[str, float], outcome: Mapping[str, float], lag: int
) -> tuple[list[float], list[float], list[str]]:
    """Pair signal at period p with outcome at p+lag."""
    from ..framework.record import period_shift

    xs: list[float] = []
    ys: list[float] = []
    periods: list[str] = []
    for period, value in sorted(signal.items()):
        try:
            target = period_shift(period, lag)
        except Exception:  # noqa: BLE001 — mismatched cadences simply do not pair
            continue
        if target in outcome:
            xs.append(value)
            ys.append(outcome[target])
            periods.append(target)
    return xs, ys, periods


class LeadLagLab:
    def __init__(self, config: Config, store: HistoryStore) -> None:
        self.config = config
        self.store = store
        self.pairs = (config.rules.get("leadlag-v1") or {}).get("pairs", [])
        self.max_lag = int((config.rules.get("leadlag-v1") or {}).get("max_lag", 12))
        self.holdout = int((config.rules.get("leadlag-v1") or {}).get("holdout_periods", 12))
        self.min_observations = int(
            (config.rules.get("leadlag-v1") or {}).get("min_observations", 30)
        )

    def evaluate_city(self, city: City) -> list[LeadLagResult]:
        results: list[LeadLagResult] = []
        for pair in self.pairs:
            results.append(self._evaluate_pair(city, pair["signal"], pair["outcome"]))
        return results

    def _evaluate_pair(self, city: City, signal_metric: str, outcome_metric: str) -> LeadLagResult:
        signal_series = self.store.for_city(city, signal_metric)
        outcome_series = self.store.for_city(city, outcome_metric)

        blank = LeadLagResult(
            signal_metric=signal_metric,
            outcome_metric=outcome_metric,
            city=city.slug,
            best_lag=None,
            in_sample_r=None,
            holdout_r=None,
            baseline_r=None,
            observations=0,
            verdict="insufficient_data",
        )
        if signal_series is None or outcome_series is None:
            return blank

        # Both sides are compared as year-over-year change: levels of two trending
        # series correlate for reasons that have nothing to do with one leading
        # the other.
        signal = yoy(signal_series.values)
        outcome = yoy(outcome_series.values)
        if len(signal) < self.min_observations or len(outcome) < self.min_observations:
            return blank

        # The ranking never sees the tail.
        cutoff = sorted(outcome)[-self.holdout] if len(outcome) > self.holdout else None
        if cutoff is None:
            return blank
        train_outcome = {p: v for p, v in outcome.items() if p < cutoff}
        test_outcome = {p: v for p, v in outcome.items() if p >= cutoff}

        best_lag, best_r = None, 0.0
        for lag in range(0, self.max_lag + 1):
            xs, ys, _ = _aligned(signal, train_outcome, lag)
            r = correlation(xs, ys)
            if r is None:
                continue
            if abs(r) > abs(best_r):
                best_lag, best_r = lag, r

        if best_lag is None:
            return blank

        xs, ys, _ = _aligned(signal, test_outcome, best_lag)
        holdout_r = correlation(xs, ys)

        # Seasonal-naive baseline: last year's value of the outcome itself,
        # evaluated on exactly the same held-out periods.
        baseline_r = self._seasonal_baseline(outcome_series.values, test_outcome)

        verdict = self._verdict(best_lag, holdout_r, baseline_r, len(xs))
        return LeadLagResult(
            signal_metric=signal_metric,
            outcome_metric=outcome_metric,
            city=city.slug,
            best_lag=best_lag,
            in_sample_r=round(best_r, 4),
            holdout_r=holdout_r,
            baseline_r=baseline_r,
            observations=len(xs),
            verdict=verdict,
        )

    @staticmethod
    def _seasonal_baseline(
        raw_outcome: Mapping[str, float], test_outcome: Mapping[str, float]
    ) -> float | None:
        from ..framework.record import period_shift

        xs: list[float] = []
        ys: list[float] = []
        for period, value in test_outcome.items():
            prior = raw_outcome.get(period_shift(period, -12))
            two_years = raw_outcome.get(period_shift(period, -24))
            if prior in (None, 0) or two_years in (None, 0):
                continue
            xs.append(100.0 * (prior - two_years) / abs(two_years))
            ys.append(value)
        return correlation(xs, ys)

    @staticmethod
    def _verdict(
        lag: int, holdout_r: float | None, baseline_r: float | None, observations: int
    ) -> str:
        if holdout_r is None or observations < 6:
            return "insufficient_data"
        if abs(holdout_r) < 0.3:
            return "no_evidence"
        if baseline_r is not None and abs(holdout_r) <= abs(baseline_r):
            # It correlates, but no better than knowing what season it is.
            return "no_evidence"
        return "leading" if lag > 0 else "coincident"
