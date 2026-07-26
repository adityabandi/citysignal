"""The model ladder, cheapest rung first.

The ordering is deliberate and it is the point of the file. Baselines come first
because they are what everything else has to beat, and because in a surprising
number of cases they win. "Same month last year" is a genuinely strong forecast
for Madrid hotel nights, and any product claiming to predict tourism demand
without checking that first is selling confidence rather than information.

So every model here returns the same shape — quantiles plus a direction
probability — and `evaluate.py` scores them against each other on held-out data.
Nothing is promoted to the site on the strength of looking sophisticated.

Deliberately absent: anything that cannot be explained in a sentence. No boosted
trees, no neural nets, no automatic feature search. With a couple of hundred
monthly observations, flexible models fit noise beautifully and forecast nothing,
and a black box cannot be argued with when it is wrong — which it will publicly
be. Ridge on declared lags and a nearest-neighbour search over history are both
inspectable by a reader who wants to know why the number is what it is.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median

# Quantiles carried by every probabilistic forecast.
QUANTILES = (0.1, 0.5, 0.9)


@dataclass(slots=True)
class Prediction:
    quantiles: dict[str, float]  # {"p10": ..., "p50": ..., "p90": ...}
    p_direction_up: float | None = None
    model: str = ""
    n_train: int = 0
    detail: dict[str, object] = field(default_factory=dict)

    @property
    def point(self) -> float:
        return self.quantiles["p50"]


def _q(sorted_values: list[float], q: float) -> float:
    """Empirical quantile by linear interpolation.

    Written out rather than imported so the arithmetic is visible: these numbers
    become published intervals, and a silent difference in interpolation
    convention would shift every one of them.
    """
    if not sorted_values:
        return math.nan
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = q * (len(sorted_values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[low]
    weight = position - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def _periods_per_year(cadence: str) -> int:
    return {"monthly": 12, "quarterly": 4, "annual": 1}.get(cadence, 12)


# --------------------------------------------------------------------- L0
def seasonal_naive(history: list[float], *, horizon: int, cadence: str) -> Prediction:
    """The value from one full cycle ago.

    The baseline to beat for anything seasonal. Its interval comes from how wrong
    this rule has historically been at this horizon, which makes it a fair
    competitor rather than a straw man — a point forecast with no interval would
    lose every probabilistic comparison by construction.
    """
    season = _periods_per_year(cadence)
    if len(history) < season + horizon:
        return Prediction({f"p{int(q*100)}": math.nan for q in QUANTILES}, model="seasonal_naive")

    point = history[-season + (horizon - 1)] if horizon <= season else history[-season]

    errors = []
    for index in range(season + horizon, len(history)):
        predicted = history[index - season]
        errors.append(history[index] - predicted)

    if errors:
        spread = sorted(errors)
        return Prediction(
            quantiles={
                "p10": point + _q(spread, 0.1),
                "p50": point + _q(spread, 0.5),
                "p90": point + _q(spread, 0.9),
            },
            p_direction_up=sum(1 for e in errors if e > 0) / len(errors),
            model="seasonal_naive",
            n_train=len(history),
        )
    return Prediction({"p10": point, "p50": point, "p90": point}, model="seasonal_naive")


def random_walk(history: list[float], *, horizon: int, cadence: str) -> Prediction:
    """Tomorrow looks like today.

    Hard to beat for slow-moving level series such as appraised value, where most
    of the variance is a trend the last observation already contains.
    """
    if len(history) < horizon + 2:
        return Prediction({f"p{int(q*100)}": math.nan for q in QUANTILES}, model="random_walk")

    point = history[-1]
    changes = [history[i] - history[i - horizon] for i in range(horizon, len(history))]
    spread = sorted(changes)
    return Prediction(
        quantiles={
            "p10": point + _q(spread, 0.1),
            "p50": point + _q(spread, 0.5),
            "p90": point + _q(spread, 0.9),
        },
        p_direction_up=sum(1 for c in changes if c > 0) / len(changes),
        model="random_walk",
        n_train=len(history),
    )


def drift(history: list[float], *, horizon: int, cadence: str) -> Prediction:
    """Random walk that keeps travelling in the direction it has been going.

    Uses the average per-period change over the trailing cycle, which is enough
    to beat a flat random walk on a series in a sustained trend and enough to be
    badly wrong at a turning point. Both facts belong in the record.
    """
    season = _periods_per_year(cadence)
    if len(history) < max(season, horizon) + 2:
        return Prediction({f"p{int(q*100)}": math.nan for q in QUANTILES}, model="drift")

    window = history[-season:] if len(history) >= season else history
    per_period = (window[-1] - window[0]) / max(len(window) - 1, 1)
    point = history[-1] + per_period * horizon

    errors = []
    for index in range(season + horizon, len(history)):
        past = history[:index]
        past_window = past[-season:]
        step = (past_window[-1] - past_window[0]) / max(len(past_window) - 1, 1)
        errors.append(history[index] - (past[-1] + step * horizon))

    if errors:
        spread = sorted(errors)
        return Prediction(
            quantiles={
                "p10": point + _q(spread, 0.1),
                "p50": point + _q(spread, 0.5),
                "p90": point + _q(spread, 0.9),
            },
            p_direction_up=sum(1 for e in errors if e > 0) / len(errors),
            model="drift",
            n_train=len(history),
        )
    return Prediction({"p10": point, "p50": point, "p90": point}, model="drift")


# --------------------------------------------------------------------- L2
def analog(
    history: list[float],
    *,
    horizon: int,
    cadence: str,
    state: list[list[float]] | None = None,
    k: int = 12,
    embargo: int = 12,
) -> Prediction:
    """What happened next, the last k times things looked like this.

    A nearest-neighbour search over the city's own past. `state` is the sequence
    of index vectors (demand momentum, housing pressure, supply response,
    distress) aligned with `history`; where it is absent the state is the recent
    shape of the series itself, which still finds "periods that looked like this
    one" and is honest about being less informed.

    Two properties make this worth having beside the linear model. It produces an
    empirical outcome distribution rather than an assumed one, so a genuinely
    bimodal situation reports as a wide interval instead of a confident middle.
    And it is directly explicable: the answer comes with the list of dates it was
    drawn from, so a reader can go and look at them.

    The embargo excludes neighbours within `embargo` periods of the target.
    Adjacent months are nearly identical by construction, so without it the
    model mostly retrieves itself and reports spectacular, meaningless accuracy.
    """
    n = len(history)
    if n < horizon + embargo + 6:
        return Prediction({f"p{int(q*100)}": math.nan for q in QUANTILES}, model="analog")

    def vector_at(index: int) -> list[float] | None:
        if state is not None:
            return state[index] if index < len(state) and state[index] else None
        if index < 3:
            return None
        recent = history[index - 3 : index + 1]
        base = abs(recent[0]) or 1.0
        return [(value - recent[0]) / base for value in recent[1:]]

    target = vector_at(n - 1)
    if target is None:
        return Prediction({f"p{int(q*100)}": math.nan for q in QUANTILES}, model="analog")

    candidates: list[tuple[float, int, float]] = []
    for index in range(len(target) + 1, n - horizon):
        if n - 1 - index <= embargo:
            continue  # too close to the present to be an independent precedent
        vector = vector_at(index)
        if vector is None or len(vector) != len(target):
            continue
        distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(vector, target)))
        base = history[index]
        if base == 0:
            continue
        outcome = (history[index + horizon] - base) / abs(base)
        candidates.append((distance, index, outcome))

    if len(candidates) < 4:
        return Prediction({f"p{int(q*100)}": math.nan for q in QUANTILES}, model="analog")

    candidates.sort(key=lambda triple: triple[0])
    neighbours = candidates[: min(k, len(candidates))]
    outcomes = sorted(change for _, _, change in neighbours)
    anchor = history[-1]

    return Prediction(
        quantiles={
            "p10": anchor * (1 + _q(outcomes, 0.1)),
            "p50": anchor * (1 + _q(outcomes, 0.5)),
            "p90": anchor * (1 + _q(outcomes, 0.9)),
        },
        p_direction_up=sum(1 for change in outcomes if change > 0) / len(outcomes),
        model="analog",
        n_train=len(candidates),
        detail={"neighbours": len(neighbours), "median_change_pct": round(median(outcomes) * 100, 2)},
    )


BASELINES = {"seasonal_naive": seasonal_naive, "random_walk": random_walk, "drift": drift}
