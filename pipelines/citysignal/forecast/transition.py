"""The crash question, asked in the only form that can be scored.

"Will there be a crash?" cannot be graded. "What is the probability Madrid enters
Stress or Dislocation at some point in the next twelve months?" can: the regimes
are defined by published rules, the window closes, and the answer is yes or no.
So that is what this module estimates, and it is scored with a Brier score
against what actually happened.

Two estimators, both transparent:

**Base rate.** How often, historically, a city in this configuration was in a
distressed regime twelve months later. This is the baseline, and it is a hard one
— distress is rare, so "it won't happen" is right most of the time and any model
has to beat that to be worth anything.

**Analog frequency.** Find the historical moments across all eight cities whose
four-index state most resembles now, and report how many of them were followed by
distress within the window. Pooling across cities is what makes this possible at
all: one city has too few episodes to estimate a rare event, and the 2008 cycle
is the only severe one in the record.

That last point is the honest limit of this whole exercise, and the reason the
output carries `episodes_in_history`. A probability derived from two or three
past episodes is a probability with very little information in it, and the site
should say so rather than printing a confident percentage.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

DISTRESS_DEFAULT = ("stress", "dislocation")


@dataclass(slots=True)
class TransitionEstimate:
    probability: float | None
    model: str
    episodes_in_history: int
    neighbours_used: int = 0
    base_rate: float | None = None
    confidence: str = "low"
    detail: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "probability": None if self.probability is None else round(self.probability, 4),
            "model": self.model,
            "episodes_in_history": self.episodes_in_history,
            "neighbours_used": self.neighbours_used,
            "base_rate": None if self.base_rate is None else round(self.base_rate, 4),
            "confidence": self.confidence,
            **self.detail,
        }


def _entered_distress(
    timeline: list[dict], start_index: int, window: int, into: tuple[str, ...]
) -> bool:
    """Did a distressed regime occur in the `window` periods after `start_index`?"""
    for step in range(1, window + 1):
        position = start_index + step
        if position >= len(timeline):
            return False
        if timeline[position].get("rule_id") in into:
            return True
    return False


def base_rate(
    timelines: dict[str, list[dict]], *, window: int, into: tuple[str, ...] = DISTRESS_DEFAULT
) -> tuple[float | None, int]:
    """Unconditional frequency of entering distress within the window.

    Pooled over every city, because a single city does not contain enough
    episodes of a rare event to estimate its rate.
    """
    hits = total = episodes = 0
    for timeline in timelines.values():
        in_episode = False
        for index in range(len(timeline) - window):
            total += 1
            if _entered_distress(timeline, index, window, into):
                hits += 1
            # Count contiguous distressed stretches once, so a long crisis does
            # not read as dozens of independent events.
            distressed = timeline[index].get("rule_id") in into
            if distressed and not in_episode:
                episodes += 1
            in_episode = distressed

    if total == 0:
        return None, 0
    return hits / total, episodes


def analog_transition(
    timelines: dict[str, list[dict]],
    index_histories: dict[str, dict[str, dict[str, float]]],
    *,
    city_slug: str,
    window: int,
    into: tuple[str, ...] = DISTRESS_DEFAULT,
    k: int = 20,
    embargo: int = 12,
) -> TransitionEstimate:
    """Probability from the k most similar historical configurations.

    `index_histories` maps city slug → index_id → period → value, i.e. the four
    sub-indices over time. The state vector is those four values; distance is
    Euclidean over whichever of them both moments share.
    """
    rate, episodes = base_rate(timelines, window=window, into=into)

    own_history = index_histories.get(city_slug, {})
    index_ids = sorted(own_history)
    if not index_ids:
        return TransitionEstimate(rate, "base_rate", episodes, base_rate=rate, confidence="low")

    def state_at(slug: str, period: str) -> list[float] | None:
        vector = []
        for index_id in index_ids:
            value = index_histories.get(slug, {}).get(index_id, {}).get(period)
            if value is None:
                return None
            vector.append(value)
        return vector

    own_periods = sorted(set.intersection(*(set(own_history[i]) for i in index_ids)))
    if not own_periods:
        return TransitionEstimate(rate, "base_rate", episodes, base_rate=rate, confidence="low")

    target = state_at(city_slug, own_periods[-1])
    if target is None:
        return TransitionEstimate(rate, "base_rate", episodes, base_rate=rate, confidence="low")

    candidates: list[tuple[float, bool]] = []
    for slug, timeline in timelines.items():
        by_period = {entry["period"]: position for position, entry in enumerate(timeline)}
        for period, position in by_period.items():
            if position + window >= len(timeline):
                continue
            if slug == city_slug:
                # Adjacent months are nearly identical; without an embargo the
                # model mostly retrieves the present and learns nothing.
                own_position = len(own_periods) - 1
                if abs(own_position - position) <= embargo:
                    continue
            vector = state_at(slug, period)
            if vector is None:
                continue
            distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(vector, target)))
            candidates.append((distance, _entered_distress(timeline, position, window, into)))

    if len(candidates) < 8:
        return TransitionEstimate(
            rate, "base_rate", episodes, base_rate=rate, confidence="low",
            detail={"reason": "too few comparable historical configurations"},
        )

    candidates.sort(key=lambda pair: pair[0])
    neighbours = candidates[: min(k, len(candidates))]
    hits = sum(1 for _, hit in neighbours if hit)

    # Laplace smoothing, so the estimate can never be exactly 0% or 100%.
    #
    # Without it, twenty quiet neighbours produce "0.0% chance of distress", which
    # is a claim no amount of this data supports — the record contains one severe
    # cycle, and the absence of a rare event among twenty samples is weak evidence
    # that it cannot happen rather than proof. Add-one keeps the number honest
    # about its own resolution: twenty clean neighbours become roughly 5%, not
    # zero, and no configuration is ever declared impossible.
    probability = (hits + 1) / (len(neighbours) + 2)

    # Confidence is about how much history stands behind the number, not about
    # how extreme it is. Two past episodes cannot support a confident claim
    # however tidy the arithmetic looks.
    if episodes >= 6 and len(neighbours) >= 15:
        confidence = "moderate"
    elif episodes >= 3:
        confidence = "low"
    else:
        confidence = "very low"

    return TransitionEstimate(
        probability=probability,
        model="analog_frequency",
        episodes_in_history=episodes,
        neighbours_used=len(neighbours),
        base_rate=rate,
        confidence=confidence,
        detail={"nearest_distance": round(neighbours[0][0], 3)},
    )
