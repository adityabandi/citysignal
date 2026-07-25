"""Period grammar, geography identifiers, and the append-only history store."""

from __future__ import annotations

import pytest

from citysignal.framework.history import current_view, merge_records, read_history, write_history
from citysignal.framework.record import (
    CanonicalRecord,
    RecordError,
    geo_level_of,
    period_cadence,
    period_end,
    period_shift,
)


@pytest.mark.parametrize(
    "period,cadence",
    [
        ("2026", "annual"),
        ("2026-Q3", "quarterly"),
        ("2026-07", "monthly"),
        ("2026-W30", "weekly"),
        ("2026-07-25", "daily"),
    ],
)
def test_period_grammar_accepts(period, cadence):
    assert period_cadence(period) == cadence


@pytest.mark.parametrize("period", ["2026-13", "2026-Q5", "26-07", "2026/07", "July 2026", ""])
def test_period_grammar_rejects(period):
    with pytest.raises(RecordError):
        period_cadence(period)


def test_period_shift_is_calendar_aware():
    assert period_shift("2026-01", -1) == "2025-12"
    assert period_shift("2026-12", 1) == "2027-01"
    assert period_shift("2026-Q1", -1) == "2025-Q4"
    assert period_shift("2026-Q4", 1) == "2027-Q1"
    assert period_shift("2026", 3) == "2029"


def test_period_end_handles_leap_years():
    assert period_end("2024-02").isoformat() == "2024-02-29"
    assert period_end("2026-02").isoformat() == "2026-02-28"
    assert period_end("2026-Q2").isoformat() == "2026-06-30"


@pytest.mark.parametrize(
    "geo_id,level",
    [
        ("mun-28079", "municipality"),
        ("dist-28079-07", "district"),
        ("barrio-28079-074", "barrio"),
        ("prov-28", "province"),
        ("ccaa-13", "ccaa"),
        ("es", "nation"),
        ("apt-MAD", "airport"),
        ("port-PMI", "port"),
    ],
)
def test_geo_identifiers(geo_id, level):
    assert geo_level_of(geo_id) == level


def test_record_rejects_a_geography_that_does_not_match_its_level():
    with pytest.raises(RecordError):
        CanonicalRecord(
            metric_id="foreclosures",
            geo_id="prov-28",
            period="2026-Q1",
            value=1.0,
            unit="cases",
            source_id="cgpj",
            geo_level="municipality",
        )


def _record(period: str, value: float, **kwargs) -> CanonicalRecord:
    return CanonicalRecord(
        metric_id="unemployment_registered",
        geo_id="mun-28079",
        period=period,
        value=value,
        unit="persons",
        source_id="sepe",
        **kwargs,
    )


def test_merge_appends_new_periods(tmp_path):
    path = tmp_path / "metric.csv"
    outcome = merge_records(path, [_record("2026-01", 100), _record("2026-02", 110)], revisions_allowed=False)
    assert (outcome.added, outcome.revised) == (2, 0)
    assert len(read_history(path)) == 2


def test_merge_is_idempotent(tmp_path):
    """A run that changes nothing must produce a byte-identical file."""
    path = tmp_path / "metric.csv"
    merge_records(path, [_record("2026-01", 100)], revisions_allowed=False)
    first = path.read_bytes()

    outcome = merge_records(path, [_record("2026-01", 100)], revisions_allowed=False)
    assert outcome.unchanged == 1
    assert not outcome.changed
    assert path.read_bytes() == first


def test_allowed_revision_appends_and_keeps_the_original(tmp_path):
    path = tmp_path / "metric.csv"
    merge_records(path, [_record("2026-01", 100)], revisions_allowed=True)
    outcome = merge_records(path, [_record("2026-01", 105)], revisions_allowed=True)

    assert outcome.revised == 1
    rows = read_history(path)
    assert len(rows) == 2, "the superseded value must survive as an audit trail"
    assert {r["value"] for r in rows} == {"100", "105"}

    latest = current_view(rows)
    assert len(latest) == 1
    assert latest[0]["value"] == "105"
    assert latest[0]["quality_flag"] == "revised"


def test_unexpected_revision_is_quarantined_not_absorbed(tmp_path):
    path = tmp_path / "metric.csv"
    merge_records(path, [_record("2026-01", 100)], revisions_allowed=False)
    outcome = merge_records(path, [_record("2026-01", 999)], revisions_allowed=False)

    assert outcome.revised == 0
    assert len(outcome.quarantined) == 1
    assert read_history(path)[0]["value"] == "100", "published history must not be overwritten"


def test_history_is_stably_sorted_regardless_of_input_order(tmp_path):
    path_a, path_b = tmp_path / "a.csv", tmp_path / "b.csv"
    records = [_record("2026-03", 3), _record("2026-01", 1), _record("2026-02", 2)]
    merge_records(path_a, records, revisions_allowed=False)
    merge_records(path_b, list(reversed(records)), revisions_allowed=False)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_float_formatting_is_stable(tmp_path):
    """Float noise across runs would churn the diff and destroy the audit trail."""
    path = tmp_path / "metric.csv"
    merge_records(path, [_record("2026-01", 100.0)], revisions_allowed=False)
    assert read_history(path)[0]["value"] == "100"
    outcome = merge_records(path, [_record("2026-01", 100.0000000001)], revisions_allowed=False)
    assert outcome.unchanged == 1
