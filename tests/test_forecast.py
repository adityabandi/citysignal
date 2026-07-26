"""Tests for the properties the track record depends on.

None of these test whether the forecasts are any good — nothing can do that
except time. They test that the machinery cannot cheat: that a backtest sees only
what was knowable at the time, that a published forecast is never quietly
rewritten, and that two runs on the same data agree. If any of them fail, the
accuracy page stops being evidence of anything.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from citysignal.forecast.engine import ForecastEngine
from citysignal.forecast.evaluate import pinball_loss, score, walk_forward
from citysignal.forecast.models import analog, drift, random_walk, seasonal_naive
from citysignal.forecast.vintage import VintageStore


# ---------------------------------------------------------------- vintages
def test_vintage_hides_the_future(config):
    """The whole point: an earlier vintage must see strictly less."""
    store = VintageStore(config)
    early = store.as_of(date(2015, 6, 15))
    late = store.as_of(date(2026, 7, 1))

    early_transfers = early.get(("property_transfers", "prov-28"), {})
    late_transfers = late.get(("property_transfers", "prov-28"), {})

    assert early_transfers, "expected some 2015-visible history to test against"
    assert len(early_transfers) < len(late_transfers)
    assert max(early_transfers) < "2015-07", "a 2015 vintage must not contain later periods"


def test_vintage_respects_publication_lag(config):
    """A closed period is still unknowable until its publication lag has elapsed.

    INE's declared lag is 45 days, so June's transfers are not available on 2 July.
    Without this, a backtest reads numbers that did not exist yet and reports
    skill that cannot survive contact with a real forecast.
    """
    store = VintageStore(config)
    transfers = store.as_of(date(2026, 7, 2)).get(("property_transfers", "prov-28"), {})
    assert "2026-06" not in transfers


def test_vintage_hash_is_stable_and_specific(config):
    store = VintageStore(config)
    first = store.vintage_hash(date(2020, 1, 1))
    again = store.vintage_hash(date(2020, 1, 1))
    later = store.vintage_hash(date(2024, 1, 1))
    assert first == again, "identical vintages must hash alike or the audit trail is worthless"
    assert first != later


# ------------------------------------------------------------------ models
def test_baselines_return_ordered_quantiles():
    history = [100 + (i % 12) * 5 + i * 0.4 for i in range(80)]
    for model in (seasonal_naive, random_walk, drift):
        q = model(history, horizon=3, cadence="monthly").quantiles
        assert q["p10"] <= q["p50"] <= q["p90"], f"{model.__name__} crossed its interval"


@pytest.mark.parametrize("horizon", [1, 3, 6, 12])
def test_seasonal_naive_is_exact_on_a_pure_cycle(horizon):
    """A series that only repeats must be predicted perfectly by repetition.

    The expected value is derived from the target's own phase rather than by
    counting back from the end of the list — the arithmetic that way round is
    easy to get wrong, and getting it wrong here would either hide a real
    off-by-one in the baseline or invent one that is not there.
    """
    period = 12
    history = [float(i % period) for i in range(120)]
    target_index = len(history) - 1 + horizon
    expected = float(target_index % period)

    prediction = seasonal_naive(history, horizon=horizon, cadence="monthly")
    assert prediction.quantiles["p50"] == pytest.approx(expected, abs=1e-9)


def test_models_refuse_short_history():
    """Too little data must yield nan, never a confident guess."""
    for model in (seasonal_naive, random_walk, drift, analog):
        p50 = model([1.0, 2.0, 3.0], horizon=6, cadence="monthly").quantiles["p50"]
        assert p50 != p50, f"{model.__name__} guessed from three observations"


def test_analog_embargo_shrinks_the_candidate_pool():
    """Without an embargo the model retrieves its own neighbourhood and looks miraculous."""
    history = [float(i % 24) for i in range(200)]
    unembargoed = analog(history, horizon=3, cadence="monthly", embargo=0)
    embargoed = analog(history, horizon=3, cadence="monthly", embargo=36)
    assert embargoed.n_train < unembargoed.n_train


def test_pinball_penalises_width():
    """A wide interval must cost more than a tight one that is equally centred.

    This is why skill is judged on pinball loss and not on error at the midpoint:
    coverage alone can be bought with uselessly wide bounds.
    """
    tight = pinball_loss(100.0, {"p10": 95.0, "p50": 100.0, "p90": 105.0})
    wide = pinball_loss(100.0, {"p10": 40.0, "p50": 100.0, "p90": 160.0})
    assert wide > tight


# -------------------------------------------------------------- evaluation
def test_walk_forward_never_trains_on_its_target():
    series = {
        f"20{year:02d}-{month:02d}": float(month + year)
        for year in range(10, 26)
        for month in range(1, 13)
    }
    folds = walk_forward(series, horizon=3, cadence="monthly", min_train=48, embargo=1)
    assert folds
    for fold in folds:
        assert fold.train_end < fold.target_period


def test_score_labels_baselines_as_baselines(config):
    store = VintageStore(config)
    series = store.as_of(date.today()).get(("property_transfers", "prov-28"), {})
    if len(series) < 80:
        pytest.skip("not enough history in this checkout")
    folds = walk_forward(series, horizon=3, cadence="monthly", min_train=48, embargo=1)
    scores = score(folds, skill_margin=0.05)
    assert scores
    assert scores["random_walk"].verdict == "baseline"
    assert scores["analog"].verdict in {
        "beats_baseline",
        "no_better_than_baseline",
        "insufficient_data",
    }


# ------------------------------------------------------------------ freeze
def test_frozen_forecasts_are_never_rewritten(config):
    """Re-running must not touch a published forecast. This is the guarantee."""
    engine = ForecastEngine(config)
    payload = engine.run(city_slug="madrid")

    before = {
        path: path.read_bytes() for path in (config.data_dir / "forecasts").glob("*/*.json")
    }
    if not before:
        pytest.skip("no frozen forecasts in this checkout yet")

    engine.freeze(payload)  # a second freeze of the same period

    for path, original in before.items():
        assert path.read_bytes() == original, f"{path} was rewritten by a later run"


def test_frozen_forecasts_are_wellformed(config):
    required = {
        "target_id",
        "metric_id",
        "geo_id",
        "geo_level",
        "for_period",
        "from_period",
        "issued_at",
        "data_vintage",
        "model",
        "targets_version",
    }
    paths = list((config.data_dir / "forecasts").glob("*/*.json"))
    if not paths:
        pytest.skip("no frozen forecasts in this checkout yet")
    for path in paths:
        record = json.loads(path.read_text())
        assert not required - record.keys(), f"{path} missing {required - record.keys()}"
        assert record["for_period"] > record["from_period"]
        quantiles = record.get("quantiles")
        if quantiles:
            assert quantiles["p10"] <= quantiles["p50"] <= quantiles["p90"]


def test_engine_is_deterministic(config):
    """Two runs on identical data must agree, or the record is not reproducible."""
    engine = ForecastEngine(config)
    first = engine.run(city_slug="madrid")
    second = engine.run(city_slug="madrid")

    def comparable(payload):
        return [
            {k: v for k, v in record.items() if k != "issued_at"}
            for record in payload["forecasts"]
        ]

    assert comparable(first) == comparable(second)
    assert first["data_vintage"] == second["data_vintage"]


def test_targets_reference_real_metrics_at_the_right_geography(config):
    """A target naming a missing metric, or the wrong scope, would fail silently forever."""
    spec = config.forecast_config("targets")
    for target_id, raw in spec["targets"].items():
        metric_id = raw.get("metric")
        if metric_id is None:
            continue
        assert metric_id in config.metrics, f"{target_id} names unknown metric {metric_id}"
        declared, actual = raw.get("geo"), config.metrics[metric_id].get("geo_level")
        if declared and actual:
            assert declared == actual, (
                f"{target_id} asks for {metric_id} at {declared} "
                f"but it is published at {actual}"
            )
