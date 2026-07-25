"""The derive engine, on synthetic history with known answers.

Golden-style rather than snapshot-style: each test states the arithmetic it
expects, so a change in behaviour has to be argued for rather than re-recorded.
"""

from __future__ import annotations

import math

import pytest

from citysignal.derive.rules import RuleError, RuleEvaluator, classify
from citysignal.derive.transforms import (
    forward_fill,
    min_observations_for,
    ratio,
    to_monthly,
    yoy,
    zscore,
)


def monthly(start_year: int, values: list[float]) -> dict[str, float]:
    out = {}
    for i, value in enumerate(values):
        year = start_year + i // 12
        month = i % 12 + 1
        out[f"{year}-{month:02d}"] = value
    return out


def test_yoy_is_calendar_aware_for_months():
    series = monthly(2024, [100] * 12 + [110] * 12)
    changes = yoy(series)
    assert changes["2025-01"] == pytest.approx(10.0)
    assert "2024-01" not in changes, "the first year has nothing to compare against"


def test_yoy_handles_quarters():
    series = {"2024-Q1": 100, "2024-Q2": 100, "2025-Q1": 150, "2025-Q2": 50}
    changes = yoy(series)
    assert changes["2025-Q1"] == pytest.approx(50.0)
    assert changes["2025-Q2"] == pytest.approx(-50.0)


def test_yoy_skips_a_zero_base_rather_than_dividing_by_it():
    assert yoy({**monthly(2024, [0] * 12), "2025-01": 5}) == {}


def test_zscore_excludes_the_current_value_from_its_own_baseline():
    # Two years wobbling around 10, then a jump to 20. The jump must read as
    # extreme, which it cannot do if it is allowed to inflate the standard
    # deviation it is then measured against.
    series = monthly(2023, [10.0, 10.5] * 12 + [20.0])
    scores = zscore(series, min_observations=24)
    assert scores["2025-01"] == 4.0, "a jump off a tight baseline should clip at the maximum"


def test_zscore_refuses_a_baseline_with_no_variance():
    # A perfectly flat history has nothing to standardise against; inventing a
    # score there would be dividing by zero and calling the result a signal.
    assert zscore(monthly(2023, [10.0] * 24 + [20.0]), min_observations=24) == {}


def test_zscore_needs_enough_history():
    assert zscore(monthly(2024, [1, 2, 3, 4, 5]), min_observations=24) == {}


def test_zscore_is_clipped_not_unbounded():
    series = monthly(2023, [10.0, 11.0] * 12 + [10_000.0])
    scores = zscore(series, min_observations=24, clip=4.0)
    assert max(abs(v) for v in scores.values()) <= 4.0


def test_min_observations_scales_with_cadence():
    # A flat 24 would mean six years before a quarterly series could say anything.
    assert min_observations_for("monthly") == 24
    assert min_observations_for("quarterly") == 12
    assert min_observations_for("annual") == 8


def test_to_monthly_spreads_a_quarter_over_its_own_months_only():
    projected = to_monthly({"2025-Q1": 5.0})
    assert projected == {"2025-01": 5.0, "2025-02": 5.0, "2025-03": 5.0}


def test_to_monthly_does_not_drag_an_annual_value_into_the_next_year():
    projected = to_monthly({"2025": 3.0})
    assert set(projected) == {f"2025-{m:02d}" for m in range(1, 13)}
    assert not any(period.startswith("2026") for period in projected)


def test_to_monthly_is_a_no_op_for_monthly_series():
    series = monthly(2025, [1, 2, 3])
    assert to_monthly(series) == series


def test_ratio_drops_points_with_no_denominator():
    result = ratio({"2025-01": 10, "2025-02": 20}, {"2025-01": 5}, scale=1000)
    assert result == {"2025-01": 2000.0}


def test_forward_fill_is_bounded():
    stock = {"2024": 1000.0}
    filled = forward_fill(stock, ["2024-06", "2026-06"], max_gap=12)
    assert "2026-06" not in filled, "a two-year-old denominator must not be carried forward"


# ---- rules -------------------------------------------------------------

RULESET = {
    "version": "test-v1",
    "rules": [
        {"id": "dislocation", "label": "Dislocation", "when": "distress > 2.0 and demand < -1.0"},
        {"id": "stress", "label": "Stress", "when": "distress > 1.0"},
        {"id": "cooling", "label": "Cooling", "when": "delta(pressure, 2) < 0"},
        {"id": "neutral", "label": "None", "when": "true"},
    ],
}


def test_rules_are_ordered_and_first_match_wins():
    verdict = classify(RULESET, {"distress": 3.0, "demand": -2.0, "pressure": 0.0})
    assert verdict["rule_id"] == "dislocation"
    verdict = classify(RULESET, {"distress": 1.5, "demand": 0.0, "pressure": 0.0})
    assert verdict["rule_id"] == "stress"


def test_a_missing_signal_cannot_satisfy_a_threshold():
    verdict = classify(RULESET, {"distress": None, "demand": None, "pressure": None})
    assert verdict["rule_id"] == "neutral"


def test_delta_function_reads_from_the_supplied_deltas():
    verdict = classify(
        RULESET,
        {"distress": 0.0, "demand": 0.0, "pressure": 1.0},
        {("pressure", 2): -0.5},
    )
    assert verdict["rule_id"] == "cooling"


def test_fallback_is_required():
    with pytest.raises(RuleError):
        classify({"version": "v", "rules": [{"id": "x", "label": "X", "when": "distress > 99"}]}, {"distress": 0.0})


def test_evaluator_rejects_anything_that_is_not_a_threshold_expression():
    evaluator = RuleEvaluator({"distress": 1.0})
    for hostile in [
        "__import__('os').system('id')",
        "open('/etc/passwd').read()",
        "distress.__class__.__mro__",
        "[x for x in ()]",
    ]:
        with pytest.raises(Exception):
            evaluator.evaluate(hostile)
