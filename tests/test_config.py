"""Cross-references between the registries.

Most adapter bugs are really registry bugs — a metric emitted under the wrong
unit, a rule referring to a signal nobody computes, a source with no licence
recorded. These are cheap to catch here and expensive to catch in production.
"""

from __future__ import annotations

import pytest

from citysignal.derive.rules import RuleEvaluator
from citysignal.framework.registry import discover_adapters

VALID_LEVELS = {
    "municipality",
    "district",
    "barrio",
    "province",
    "ccaa",
    "nation",
    "airport",
    "port",
    "fua",
}
VALID_CADENCES = {"daily", "weekly", "monthly", "quarterly", "annual"}


def test_eight_cities_with_codes(config):
    assert len(config.cities) == 8
    for city in config.cities:
        assert len(city.ine_mun) == 5, city.slug
        assert len(city.ine_prov) == 2, city.slug
        assert city.ine_mun.startswith(city.ine_prov), (
            f"{city.slug}: municipality code {city.ine_mun} is not inside province {city.ine_prov}"
        )
        assert city.wikipedia.get("es"), city.slug


def test_city_slugs_and_codes_unique(config):
    slugs = [c.slug for c in config.cities]
    codes = [c.ine_mun for c in config.cities]
    assert len(set(slugs)) == len(slugs)
    assert len(set(codes)) == len(codes)


def test_every_metric_is_well_formed(config):
    for metric_id, meta in config.metrics.items():
        assert meta.get("unit"), metric_id
        assert meta.get("cadence") in VALID_CADENCES, metric_id
        assert meta.get("geo_level") in VALID_LEVELS, metric_id
        assert meta.get("direction") in (1, -1), metric_id
        assert meta.get("source_id") in config.sources, (
            f"{metric_id} names source {meta.get('source_id')!r}, which is not in sources.yml"
        )
        low, high = meta["range"]
        assert low < high, metric_id


def test_every_source_declares_licence_and_attribution(config):
    for source_id, source in config.sources.items():
        for field in ("publisher", "license", "attribution", "kind", "cadence", "max_age_days"):
            assert source.get(field) is not None, f"{source_id} is missing {field}"
        assert source["kind"] in {"official", "research", "commercial"}, source_id


def test_registered_adapters_match_sources(config):
    for source_id, adapter in discover_adapters().items():
        assert source_id in config.sources, f"adapter {source_id} has no sources.yml entry"
        assert adapter.manifest.source_id == source_id


def test_index_components_reference_real_metrics(config):
    rules = config.rules["indices-v1"]
    for index_id, spec in rules["indices"].items():
        assert spec["components"], index_id
        for component in spec["components"]:
            assert component["metric"] in config.metrics, (
                f"{index_id} references unknown metric {component['metric']}"
            )


def test_regime_rules_parse_and_have_a_fallback(config):
    rules = config.rules["regimes-v1"]
    signals = {index_id: 0.0 for index_id in config.rules["indices-v1"]["indices"]}
    deltas = {(index_id, n): 0.0 for index_id in signals for n in (1, 2, 3, 4, 12)}
    evaluator = RuleEvaluator(signals, deltas)

    for rule in rules["rules"]:
        # Raises on an unknown signal name or an unsupported expression node.
        evaluator.evaluate(rule["when"])

    assert rules["rules"][-1]["when"].strip() in {"true", "True"}, (
        "the last rule must always match, or a city can end up unclassifiable"
    )
    assert len({r["id"] for r in rules["rules"]}) == len(rules["rules"])


def test_signature_signals_reference_real_metrics(config):
    for archetype_id, spec in config.rules["signatures-v1"]["archetypes"].items():
        assert len(spec["signals"]) >= 5, f"{archetype_id} is too thin to be a signature"
        for signal in spec["signals"]:
            assert signal["metric"] in config.metrics, (
                f"{archetype_id} references unknown metric {signal['metric']}"
            )
            assert signal["expect"] in {"up", "down"}, archetype_id


def test_leadlag_pairs_reference_real_metrics(config):
    for pair in config.rules["leadlag-v1"]["pairs"]:
        assert pair["signal"] in config.metrics, pair
        assert pair["outcome"] in config.metrics, pair
        assert pair["signal"] != pair["outcome"]


def test_rule_evaluator_refuses_arbitrary_code(config):
    evaluator = RuleEvaluator({"distress": 1.0})
    with pytest.raises(Exception):
        evaluator.evaluate("__import__('os').system('echo pwned')")
    with pytest.raises(Exception):
        evaluator.evaluate("distress.__class__")
