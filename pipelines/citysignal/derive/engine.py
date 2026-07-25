"""Turn committed history into the JSON the site reads.

Ordered and deterministic: transforms, then sub-indices, then the regime, then
signature matching, then the lead-lag lab, then export. No network, no model, no
randomness — running this twice on the same history produces byte-identical
output, which is what makes the committed result reviewable in a diff.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..framework.config import City, Config
from ..framework.record import period_shift, utc_today, period_end
from .indices import IndexEngine, SignatureEngine
from .leadlag import LeadLagLab
from .rules import classify
from .store import SCOPE_LABELS, HistoryStore
from .transforms import robust_outlier_score, yoy

SERIES_TAIL = 96  # periods of history shipped per metric — eight years of months
# 3.5 median absolute deviations is the conventional robust-outlier cut. Set
# against real releases: a partial quarter of court statistics scores above 4,
# while genuine seasonal swings in tourism and hiring stay well below.
OUTLIER_THRESHOLD = 3.5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )


class DeriveEngine:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.store = HistoryStore(config)
        self.indices = IndexEngine(config, self.store)
        self.signatures = SignatureEngine(config, self.indices)
        self.lab = LeadLagLab(config, self.store)
        self.regime_rules = config.ruleset("regimes")
        self.health = self._load_health()
        self.provisional = self._provisional_releases()

    def _provisional_releases(self) -> set[tuple[str, str]]:
        """Metric-periods where every geography moved sharply the same way.

        Official statistics rarely swing 40% in the same direction in eight
        provinces at once. When the newest release does that, the likely causes
        are a partial period, a definition change or a parsing error — not a
        simultaneous national event. Flagging the release as provisional keeps it
        visible on the city page, with a note, while keeping it off the front page
        until a later release either confirms or corrects it.

        This is deliberately cross-sectional: a per-series outlier test cannot see
        it, because each individual series looks merely unusual rather than wrong.
        """
        by_metric: dict[str, dict[str, dict[str, float]]] = {}
        for series in self.store:
            by_metric.setdefault(series.key.metric_id, {})[series.key.geo_id] = series.values

        flagged: set[tuple[str, str]] = set()
        for metric_id, geographies in by_metric.items():
            if len(geographies) < 4:
                continue  # too few geographies to read a cross-section from
            latest = max(max(values) for values in geographies.values() if values)
            changes = [
                change
                for values in geographies.values()
                if (change := yoy(values).get(latest)) is not None
            ]
            if len(changes) < 4:
                continue
            same_direction = max(
                sum(1 for c in changes if c > 0), sum(1 for c in changes if c < 0)
            )
            median_move = sorted(abs(c) for c in changes)[len(changes) // 2]
            if same_direction / len(changes) >= 0.6 and median_move > 40:
                flagged.add((metric_id, latest))
        return flagged

    def _load_health(self) -> dict[str, Any]:
        path = self.config.data_dir / "quality" / "health.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text()).get("sources", {})

    # ---- per city --------------------------------------------------------
    def city_payload(self, city: City) -> dict[str, Any]:
        index_results = self.indices.compute(city)
        histories = {
            index_id: self.indices.index_history(city, index_id) for index_id in index_results
        }
        regime = self._regime(index_results, histories)
        regime_timeline = self._regime_timeline(histories)

        sections: dict[str, list[dict[str, Any]]] = {}
        for metric_id, meta in self.config.metrics.items():
            series = self.store.for_city(city, metric_id)
            if series is None or not series.values:
                continue
            sections.setdefault(meta.get("section", "other"), []).append(
                self._metric_card(city, metric_id, meta, series)
            )
        for cards in sections.values():
            cards.sort(key=lambda c: c["label"])

        return {
            "slug": city.slug,
            "name": city.name,
            "province": city.province_name,
            "ccaa": city.ccaa_name,
            "geo": {
                "municipality": city.geo_id,
                "province": city.province_geo_id,
                "ccaa": city.ccaa_geo_id,
                "airports": list(city.airports),
                "ports": list(city.ports),
                "fua": city.fua_id,
            },
            "deep_dive": city.deep_dive,
            "regime": regime,
            "regime_timeline": regime_timeline,
            "indices": {k: v.to_dict() for k, v in index_results.items()},
            "index_history": {
                k: [{"period": p, "value": v} for p, v in sorted(hist.items())[-SERIES_TAIL:]]
                for k, hist in histories.items()
            },
            "districts": self._districts(city),
            "signatures": self.signatures.match(city),
            "leadlag": [r.to_dict() for r in self.lab.evaluate_city(city)],
            "sections": sections,
            "generated_at": _now(),
        }

    def _metric_card(
        self, city: City, metric_id: str, meta: dict, series: Any
    ) -> dict[str, Any]:
        periods = sorted(series.values)[-SERIES_TAIL:]
        changes = yoy(series.values)
        latest_period = periods[-1]
        health = self.health.get(series.source_id, {})

        # A publisher's most recent period is routinely partial or restated. If it
        # is a wild departure from its own recent normal, say so on the card and
        # keep it out of the headline rather than presenting it as a discovery.
        outlier = robust_outlier_score(series.values, latest_period)
        cross_sectional = (metric_id, latest_period) in self.provisional
        suspect = cross_sectional or (outlier is not None and outlier >= OUTLIER_THRESHOLD)

        return {
            "metric_id": metric_id,
            "label": meta.get("label", metric_id),
            "plain": (meta.get("plain") or "").strip() or None,
            "label_es": meta.get("label_es"),
            "unit": series.unit,
            "cadence": meta["cadence"],
            "section": meta.get("section", "other"),
            "lag": meta.get("lag"),
            "direction": meta.get("direction", 1),
            "note": (meta.get("note") or "").strip() or None,
            "geo_id": series.key.geo_id,
            "geo_level": series.key.geo_level,
            # The scope label is not decoration: a province series shown on a city
            # page must say so, every time it is drawn.
            "scope_label": SCOPE_LABELS.get(series.key.geo_level, series.key.geo_level),
            "is_city_level": series.key.geo_level == "municipality",
            "latest": {
                "period": latest_period,
                "value": series.values[latest_period],
                "yoy": changes.get(latest_period),
            },
            "suspect": suspect,
            "outlier_score": outlier,
            "suspect_note": (
                (
                    "Every city moved sharply the same way in this release, which is far "
                    "more often a partial period, a definition change or a parsing error "
                    "than a simultaneous national event. Treated as provisional and kept "
                    "off the front page until a later release confirms it."
                    if cross_sectional
                    else f"The latest release sits {outlier} median absolute deviations from "
                    "this measure's recent normal. Treated as provisional — publishers "
                    "commonly release a partial period first — and kept off the front page "
                    "until a later release confirms it."
                )
                if suspect
                else None
            ),
            "series": [{"period": p, "value": series.values[p]} for p in periods],
            "source_id": series.source_id,
            "source": {
                "publisher": health.get("publisher"),
                "license": health.get("license"),
                "attribution": health.get("attribution"),
                "kind": health.get("kind", "official"),
                "docs_url": health.get("docs_url"),
            },
            # Two distinct dates, never conflated: what the data is about, and
            # when we last asked the publisher for it.
            "observation_end": period_end(latest_period).isoformat(),
            "last_checked": health.get("last_checked"),
            "staleness_days": (utc_today() - period_end(latest_period)).days,
            "max_age_days": health.get("max_age_days"),
            "fresh": self._freshness(latest_period, health.get("max_age_days")),
        }

    def _districts(self, city: City) -> dict[str, Any]:
        """Sub-city series, for cities that actually publish them.

        Only Madrid does, and only for a handful of measures. A city with no
        district data gets an empty payload rather than a map interpolated from
        the municipal total — a coarse map is honest, an invented one is not.
        """
        out: dict[str, Any] = {"metrics": {}, "codes": []}
        codes: set[str] = set()

        for metric_id, meta in self.config.metrics.items():
            if meta.get("geo_level") != "district":
                continue
            series_list = self.store.districts_of(city, metric_id)
            if not series_list:
                continue

            per_district: dict[str, Any] = {}
            for series in series_list:
                if not series.values:
                    continue
                code = series.key.geo_id.rsplit("-", 1)[-1]
                codes.add(code)
                periods = sorted(series.values)[-SERIES_TAIL:]
                latest = periods[-1]
                per_district[code] = {
                    "latest": {"period": latest, "value": series.values[latest]},
                    "yoy": yoy(series.values).get(latest),
                    "series": [{"period": p, "value": series.values[p]} for p in periods],
                }

            if per_district:
                out["metrics"][metric_id] = {
                    "label": meta.get("label", metric_id),
                    "plain": (meta.get("plain") or "").strip() or None,
                    "unit": meta["unit"],
                    "section": meta.get("section", "other"),
                    "districts": per_district,
                }

        out["codes"] = sorted(codes)
        return out

    @staticmethod
    def _freshness(period: str, max_age_days: int | None) -> str:
        if max_age_days is None:
            return "unknown"
        age = (utc_today() - period_end(period)).days
        if age <= max_age_days:
            return "fresh"
        if age <= max_age_days * 2:
            return "stale"
        return "failing"

    # ---- regime ----------------------------------------------------------
    def _regime(self, index_results: dict, histories: dict[str, dict[str, float]]) -> dict[str, Any]:
        values = {k: v.value for k, v in index_results.items()}
        deltas = self._deltas(histories)
        verdict = classify(self.regime_rules, values, deltas)

        available = [k for k, v in values.items() if v is not None]
        verdict["inputs"] = {k: v for k, v in values.items()}
        verdict["available_indices"] = available
        verdict["confident"] = len(available) >= 3
        verdict["period"] = max(
            (v.period for v in index_results.values() if v.period), default=None
        )
        return verdict

    @staticmethod
    def _deltas(histories: dict[str, dict[str, float]]) -> dict[tuple[str, int], float | None]:
        out: dict[tuple[str, int], float | None] = {}
        for index_id, history in histories.items():
            if not history:
                continue
            latest = max(history)
            for step in (1, 2, 3, 4, 12):
                prior_period = period_shift(latest, -step)
                prior = history.get(prior_period)
                out[(index_id, step)] = (
                    round(history[latest] - prior, 4) if prior is not None else None
                )
        return out

    def _regime_timeline(self, histories: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
        """Replay the same rules over history, with hysteresis on sticky regimes."""
        periods = sorted({p for hist in histories.values() for p in hist})
        sticky = set(self.regime_rules.get("sticky_regimes", []))
        hysteresis = int(self.regime_rules.get("hysteresis_periods", 1))

        timeline: list[dict[str, Any]] = []
        held: str | None = None
        exit_streak = 0

        for period in periods[-SERIES_TAIL:]:
            values = {k: hist.get(period) for k, hist in histories.items()}
            if sum(v is not None for v in values.values()) < 2:
                continue
            deltas = {
                (index_id, step): (
                    hist[period] - hist[period_shift(period, -step)]
                    if period in hist and period_shift(period, -step) in hist
                    else None
                )
                for index_id, hist in histories.items()
                for step in (1, 2, 3, 4, 12)
            }
            verdict = classify(self.regime_rules, values, deltas)
            rule_id = verdict["rule_id"]

            # A city does not leave Stress on one good month.
            if held in sticky and rule_id != held:
                exit_streak += 1
                if exit_streak < hysteresis:
                    rule_id = held
                    verdict = {**verdict, "rule_id": held, "held_by_hysteresis": True}
                else:
                    exit_streak = 0
            else:
                exit_streak = 0
            held = rule_id

            timeline.append(
                {
                    "period": period,
                    "rule_id": rule_id,
                    "label": verdict["label"] if rule_id == verdict["rule_id"] else held,
                    "held_by_hysteresis": bool(verdict.get("held_by_hysteresis")),
                    **{f"index_{k}": v for k, v in values.items()},
                }
            )
        return timeline

    # ---- whole build -----------------------------------------------------
    def build(self) -> dict[str, Any]:
        out_dir = self.config.data_dir / "derived"
        cities_payloads: list[dict[str, Any]] = []

        for city in self.config.cities:
            payload = self.city_payload(city)
            _write(out_dir / "cities" / f"{city.slug}.json", payload)
            cities_payloads.append(payload)

        overview = self._overview(cities_payloads)
        _write(out_dir / "overview.json", overview)
        _write(out_dir / "sources.json", self._sources())
        _write(out_dir / "signals.json", self._signals(cities_payloads))

        manifest = {
            "generated_at": _now(),
            "cities": [c["slug"] for c in cities_payloads],
            "metrics_available": sorted(self.store.metric_ids),
            "rules": {
                "indices": self.indices.rules["version"],
                "regimes": self.regime_rules["version"],
                "signatures": self.signatures.rules["version"],
                "leadlag": self.config.ruleset("leadlag").get("version"),
            },
            "counts": {
                "series": sum(1 for _ in self.store),
                "observations": sum(len(s.values) for s in self.store),
            },
        }
        _write(out_dir / "manifest.json", manifest)

        return {
            "cities": len(cities_payloads),
            "metrics": len(self.store.metric_ids),
            "output_files": len(cities_payloads) + 4,
        }

    def _overview(self, payloads: list[dict[str, Any]]) -> dict[str, Any]:
        movers: list[dict[str, Any]] = []
        for payload in payloads:
            for cards in payload["sections"].values():
                for card in cards:
                    change = card["latest"].get("yoy")
                    if change is None or card["fresh"] == "failing" or card["suspect"]:
                        continue
                    movers.append(
                        {
                            "city": payload["slug"],
                            "city_name": payload["name"],
                            "metric_id": card["metric_id"],
                            "geo_id": card["geo_id"],
                            "label": card["label"],
                            "yoy": change,
                            "value": card["latest"]["value"],
                            "unit": card["unit"],
                            "period": card["latest"]["period"],
                            "geo_level": card["geo_level"],
                            "scope_label": card["scope_label"],
                            "direction": card["direction"],
                        }
                    )
        movers.sort(key=lambda m: abs(m["yoy"]), reverse=True)

        # A national or shared-region series belongs to every city that reads it,
        # so it would otherwise fill the table with eight identical rows. Keep the
        # first occurrence of each distinct series.
        seen: set[tuple[str, str]] = set()
        deduped: list[dict[str, Any]] = []
        for mover in movers:
            key = (mover["metric_id"], mover["geo_id"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(mover)
        movers = deduped

        stale = [
            {
                "source_id": source_id,
                "status": entry.get("status"),
                "latest_observation": entry.get("latest_observation"),
                "staleness_days": entry.get("staleness_days"),
                "last_error": entry.get("last_error"),
            }
            for source_id, entry in sorted(self.health.items())
            if entry.get("status") == "failed" or entry.get("fresh") is False
        ]

        return {
            "generated_at": _now(),
            "headline": self._headline(movers),
            "cities": [
                {
                    "slug": p["slug"],
                    "name": p["name"],
                    "regime": p["regime"],
                    "indices": {k: v["value"] for k, v in p["indices"].items()},
                    "top_signature": p["signatures"][0] if p["signatures"] else None,
                    "timeline": p["regime_timeline"][-36:],
                }
                for p in payloads
            ],
            "movers": movers[:24],
            "attention": stale,
        }

    @staticmethod
    def _headline(movers: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The single most striking change, stated as a sentence.

        Composed deterministically from the same numbers the table shows, so the
        front page can lead with a finding without anyone writing copy each week
        and without a language model inventing one. Attention measures are
        excluded: "Wikipedia views rose 40%" is not a finding about a city.
        """
        candidates = [
            m
            for m in movers
            if m["geo_level"] in {"municipality", "province"}
            and not m["metric_id"].startswith(("wiki_", "news_", "search_"))
            and abs(m["yoy"]) >= 5
        ]
        if not candidates:
            return None

        top = candidates[0]
        direction = "rose" if top["yoy"] > 0 else "fell"
        return {
            **top,
            "sentence": (
                f"{top['label']} in {top['city_name']} {direction} "
                f"{abs(top['yoy']):.0f}% over the year to {top['period']}."
            ),
            "qualifier": (
                f"Published at {top['scope_label']} level."
                if top["geo_level"] != "municipality"
                else "Published for the municipality itself."
            ),
        }

    def _signals(self, payloads: list[dict[str, Any]]) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for payload in payloads:
            for result in payload["leadlag"]:
                rows.append({**result, "city_name": payload["name"]})

        by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            by_pair.setdefault((row["signal_metric"], row["outcome_metric"]), []).append(row)

        summary = []
        for (signal, outcome), group in sorted(by_pair.items()):
            tested = [r for r in group if r["verdict"] != "insufficient_data"]
            leading = [r for r in tested if r["verdict"] == "leading"]
            summary.append(
                {
                    "signal_metric": signal,
                    "signal_label": self.config.metrics.get(signal, {}).get("label", signal),
                    "outcome_metric": outcome,
                    "outcome_label": self.config.metrics.get(outcome, {}).get("label", outcome),
                    "cities_tested": len(tested),
                    "cities_leading": len(leading),
                    "median_lag": (
                        sorted(r["best_lag"] for r in leading)[len(leading) // 2] if leading else None
                    ),
                    "verdict": (
                        "leading"
                        if len(leading) >= max(2, len(tested) // 2)
                        else ("no_evidence" if tested else "insufficient_data")
                    ),
                    "rows": sorted(group, key=lambda r: r["city"]),
                }
            )
        summary.sort(key=lambda s: (-s["cities_leading"], s["signal_metric"]))

        return {
            "generated_at": _now(),
            "version": self.config.ruleset("leadlag").get("version"),
            "method": (
                "Signals and outcomes are compared as year-over-year change. The lag is "
                "chosen on the earlier part of each series and scored on a held-out tail "
                "the search never saw, then required to beat a seasonal-naive baseline."
            ),
            "pairs": summary,
        }

    def _sources(self) -> dict[str, Any]:
        return {
            "generated_at": _now(),
            "sources": [
                {**entry, "declared": self.config.sources.get(source_id, {})}
                for source_id, entry in sorted(self.health.items())
            ],
        }


def run_derive(config: Config) -> dict[str, Any]:
    return DeriveEngine(config).build()
