"""End-state checks on whatever the pipeline last produced.

These run in CI against committed data, so they are the gate that decides
whether a weekly refresh is allowed to commit and deploy.
"""

from __future__ import annotations

import json
import subprocess

import pytest

SIZE_BUDGET_MB = 40


@pytest.fixture(scope="module")
def derived(repo_root):
    path = repo_root / "data" / "derived" / "manifest.json"
    if not path.exists():
        pytest.skip("no derived output yet — run `citysignal derive`")
    return repo_root / "data" / "derived"


def test_manifest_lists_every_city(derived, config):
    manifest = json.loads((derived / "manifest.json").read_text())
    assert manifest["cities"] == [c.slug for c in config.cities]
    assert manifest["rules"]["regimes"], "a build must record which rules produced it"


def test_every_city_has_a_payload_and_a_regime(derived, config):
    for city in config.cities:
        payload = json.loads((derived / "cities" / f"{city.slug}.json").read_text())
        assert payload["slug"] == city.slug
        # A regime is always present: "no clear regime" is a real answer, an
        # absent one is a bug.
        assert payload["regime"]["rule_id"]
        assert payload["regime"]["rules_version"]
        assert "indices" in payload and len(payload["indices"]) == 4


def test_no_series_is_shown_under_a_geography_it_was_not_published_at(derived, config):
    """The product's central rule, checked on the actual output."""
    for city in config.cities:
        payload = json.loads((derived / "cities" / f"{city.slug}.json").read_text())
        for cards in payload["sections"].values():
            for card in cards:
                declared = config.metrics[card["metric_id"]]["geo_level"]
                assert card["geo_level"] == declared, (
                    f"{city.slug}/{card['metric_id']} is published at {declared} "
                    f"but was emitted as {card['geo_level']}"
                )
                if card["geo_level"] == "municipality":
                    assert card["geo_id"] == city.geo_id
                elif card["geo_level"] == "province":
                    assert card["geo_id"] == city.province_geo_id


def test_every_card_carries_provenance(derived, config):
    for city in config.cities:
        payload = json.loads((derived / "cities" / f"{city.slug}.json").read_text())
        for cards in payload["sections"].values():
            for card in cards:
                assert card["source_id"]
                assert card["scope_label"]
                assert card["observation_end"], "a card must say what period it describes"
                assert card["fresh"] in {"fresh", "stale", "failing", "unknown"}


def test_index_values_are_in_a_defensible_range(derived, config):
    for city in config.cities:
        payload = json.loads((derived / "cities" / f"{city.slug}.json").read_text())
        for index in payload["indices"].values():
            if index["value"] is None:
                assert index["insufficient"] or not index["components"]
                continue
            # Components are clipped at 4 sigma, so a weighted mean cannot exceed it.
            assert -4.0 <= index["value"] <= 4.0, f"{city.slug}/{index['index_id']}"


def test_signature_scores_never_exceed_their_coverage(derived, config):
    for city in config.cities:
        payload = json.loads((derived / "cities" / f"{city.slug}.json").read_text())
        for signature in payload["signatures"]:
            assert signature["firing"] <= signature["available"] <= signature["total"]
            if signature["score"] is not None:
                assert 0.0 <= signature["score"] <= 1.0


def test_committed_data_stays_within_budget(repo_root):
    output = subprocess.run(
        ["du", "-sk", str(repo_root / "data")], capture_output=True, text=True, check=True
    ).stdout
    megabytes = int(output.split()[0]) / 1024
    assert megabytes < SIZE_BUDGET_MB, (
        f"data/ is {megabytes:.1f} MB, over the {SIZE_BUDGET_MB} MB budget. "
        "Git history never shrinks — aggregate at ingest rather than committing more rows."
    )
