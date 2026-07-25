"""Pure transforms over a single (metric, geography) series.

Every function takes a period-indexed mapping and returns another, so the derive
engine stays a chain of small comparable steps. Nothing here fits anything: no
model, no smoothing that could invent a turning point, no imputation.
"""

from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Mapping

from ..framework.record import Cadence, period_cadence, period_end, period_shift, periods_per_year

Series = Mapping[str, float]


def sorted_periods(series: Series) -> list[str]:
    return sorted(series)


def change(series: Series, periods: int) -> dict[str, float]:
    """Absolute change over n periods. For rates, where a ratio is meaningless."""
    out: dict[str, float] = {}
    for period, value in series.items():
        prior = series.get(period_shift(period, -periods))
        if prior is None or value is None:
            continue
        out[period] = value - prior
    return out


def yoy(series: Series) -> dict[str, float]:
    """Year-over-year percent change, calendar-aware for every cadence."""
    if not series:
        return {}
    step = periods_per_year(period_cadence(next(iter(series))))
    out: dict[str, float] = {}
    for period, value in series.items():
        prior = series.get(period_shift(period, -step))
        if prior in (None, 0) or value is None:
            continue
        out[period] = 100.0 * (value - prior) / abs(prior)
    return out


def rolling(series: Series, window: int, *, how: str = "mean") -> dict[str, float]:
    periods = sorted_periods(series)
    out: dict[str, float] = {}
    for index in range(window - 1, len(periods)):
        chunk = [series[p] for p in periods[index - window + 1 : index + 1]]
        if any(v is None for v in chunk):
            continue
        out[periods[index]] = sum(chunk) if how == "sum" else mean(chunk)
    return out


def zscore(
    series: Series,
    *,
    min_observations: int = 24,
    clip: float = 4.0,
) -> dict[str, float]:
    """Standardise against the series' own expanding past.

    The current observation is excluded from its own mean and standard deviation,
    so a single extreme value cannot flatten the score that is meant to reveal it.
    Values are clipped rather than dropped: an outlier should read as extreme, not
    as infinite.
    """
    periods = sorted_periods(series)
    out: dict[str, float] = {}
    for index, period in enumerate(periods):
        history = [series[p] for p in periods[:index] if series[p] is not None]
        if len(history) < min_observations:
            continue
        spread = pstdev(history)
        if spread == 0 or math.isnan(spread):
            continue
        value = series[period]
        if value is None:
            continue
        score = (value - mean(history)) / spread
        out[period] = max(-clip, min(clip, round(score, 4)))
    return out


def ratio(numerator: Series, denominator: Series, *, scale: float = 1.0) -> dict[str, float]:
    """Align two series by period and divide. Missing denominators drop the point."""
    out: dict[str, float] = {}
    for period, value in numerator.items():
        base = denominator.get(period)
        if base in (None, 0) or value is None:
            continue
        out[period] = scale * value / base
    return out


def forward_fill(series: Series, target_periods: list[str], *, max_gap: int) -> dict[str, float]:
    """Carry an annual denominator across the months it covers.

    Bounded deliberately: a population count from three years ago is not a valid
    denominator for this month, and pretending otherwise produces a per-capita
    series that drifts for reasons that have nothing to do with the numerator.
    """
    if not series:
        return {}
    out: dict[str, float] = {}
    for target in target_periods:
        if target in series:
            out[target] = series[target]
            continue
        for step in range(1, max_gap + 1):
            candidate = period_shift(target, -step)
            if candidate in series:
                out[target] = series[candidate]
                break
    return out


def latest(series: Series) -> tuple[str, float] | None:
    if not series:
        return None
    period = max(series)
    return period, series[period]


def to_monthly(series: Series) -> dict[str, float]:
    """Project a series of any cadence onto the months it actually covers.

    Indices combine measures published monthly, quarterly and annually. Left as
    they are, their period keys never coincide and nothing can be combined. So
    each observation is spread across the months it describes — a Q1 reading
    holds for January, February and March — and never beyond them. An annual
    figure for 2025 contributes to 2025's months and then stops, rather than
    being dragged forward to stand in for a year it says nothing about.
    """
    if not series:
        return {}
    cadence = period_cadence(next(iter(series)))
    if cadence == "monthly":
        return dict(series)

    out: dict[str, float] = {}
    for period, value in series.items():
        if value is None:
            continue
        if cadence == "annual":
            months = [f"{period}-{m:02d}" for m in range(1, 13)]
        elif cadence == "quarterly":
            year, quarter = period[:4], int(period[-1])
            months = [f"{year}-{m:02d}" for m in range((quarter - 1) * 3 + 1, quarter * 3 + 1)]
        elif cadence == "weekly":
            months = [period_end(period).isoformat()[:7]]
        else:  # daily
            months = [period[:7]]
        for month in months:
            out[month] = value
    return out


def min_observations_for(cadence: Cadence) -> int:
    """How much history a measure needs before it may be standardised.

    Scaled to what each cadence can realistically offer: two years of months,
    three years of quarters, eight years of annual figures. A flat 24 would mean
    a quarterly series needs six years before it can say anything, which in
    practice silences most of the official statistics this project depends on.
    """
    return {"daily": 60, "weekly": 52, "monthly": 24, "quarterly": 12, "annual": 8}.get(cadence, 24)


TRANSFORMS = {
    "level": lambda s: dict(s),
    "yoy": yoy,
    "change_12": lambda s: change(s, 12),
    "change_4": lambda s: change(s, 4),
    "roll_3": lambda s: rolling(s, 3),
    "roll_12": lambda s: rolling(s, 12),
}
