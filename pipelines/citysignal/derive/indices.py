"""Sub-indices and archetype signature matching."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..framework.config import City, Config
from .store import HistoryStore
from .transforms import TRANSFORMS, min_observations_for, ratio, to_monthly, zscore


@dataclass(slots=True)
class ComponentScore:
    metric_id: str
    label: str
    transform: str
    geo_id: str
    geo_level: str
    weight: float
    period: str
    raw: float
    z: float
    oriented: float
    """z multiplied by the metric's direction, so positive always means hotter."""
    source_id: str
    unit: str


@dataclass(slots=True)
class IndexResult:
    index_id: str
    label: str
    question: str
    value: float | None
    period: str | None
    components: list[ComponentScore] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    insufficient: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "index_id": self.index_id,
            "label": self.label,
            "question": self.question,
            "value": self.value,
            "period": self.period,
            "insufficient": self.insufficient,
            "components": [
                {
                    "metric_id": c.metric_id,
                    "label": c.label,
                    "transform": c.transform,
                    "geo_id": c.geo_id,
                    "geo_level": c.geo_level,
                    "weight": c.weight,
                    "period": c.period,
                    "raw": c.raw,
                    "z": c.z,
                    "oriented": c.oriented,
                    "source_id": c.source_id,
                    "unit": c.unit,
                }
                for c in self.components
            ],
            "missing": self.missing,
        }


class IndexEngine:
    def __init__(self, config: Config, store: HistoryStore) -> None:
        self.config = config
        self.store = store
        self.rules = config.ruleset("indices")
        self._z_cache: dict[tuple[str, str, str], dict[str, float]] = {}
        self._raw_cache: dict[tuple[str, str, str], dict[str, float]] = {}

    # ---- shared standardised signal -------------------------------------
    def oriented_z(
        self, city: City, metric_id: str, transform: str
    ) -> tuple[dict[str, float], ComponentScore | None]:
        """Standardised, direction-oriented series for one city and metric."""
        cache_key = (city.slug, metric_id, transform)
        cached = self._z_cache.get(cache_key)
        series = self.store.for_city(city, metric_id)
        if series is None or not series.values:
            return {}, None

        meta = self.config.metrics[metric_id]
        direction = float(meta.get("direction", 1))

        if cached is None:
            if transform == "per_1000_dwellings":
                stock = self.store.for_city(city, "dwelling_stock")
                if stock is None or not stock.values:
                    return {}, None
                from .transforms import forward_fill

                denominator = forward_fill(
                    stock.values, sorted(series.values), max_gap=_max_gap(meta["cadence"])
                )
                transformed = ratio(series.values, denominator, scale=1000.0)
            else:
                func = TRANSFORMS.get(transform)
                if func is None:
                    raise KeyError(f"unknown transform {transform!r}")
                transformed = func(series.values)

            # Standardise in the series' own cadence — a quarterly measure must be
            # compared with its own quarters, not with a monthly grid it never had.
            scores = zscore(
                transformed,
                min_observations=min_observations_for(meta["cadence"]),
                clip=self.rules.get("zscore_clip", 4.0),
            )
            oriented = {period: round(z * direction, 4) for period, z in scores.items()}
            # Only then project onto months, so indices of mixed cadence can be
            # combined at all.
            cached = to_monthly(oriented)
            self._z_cache[cache_key] = cached
            self._raw_cache[cache_key] = to_monthly(transformed)

        if not cached:
            return {}, None

        period = max(cached)
        raw = self._raw_cache[cache_key][period]
        component = ComponentScore(
            metric_id=metric_id,
            label=meta.get("label", metric_id),
            transform=transform,
            geo_id=series.key.geo_id,
            geo_level=series.key.geo_level,
            weight=1.0,
            period=period,
            raw=round(raw, 4),
            z=round(cached[period] * direction, 4),
            oriented=cached[period],
            source_id=series.source_id,
            unit=series.unit,
        )
        return cached, component

    # ---- indices ---------------------------------------------------------
    def compute(self, city: City) -> dict[str, IndexResult]:
        results: dict[str, IndexResult] = {}
        for index_id, spec in self.rules["indices"].items():
            components: list[ComponentScore] = []
            missing: list[str] = []
            for entry in spec["components"]:
                _, component = self.oriented_z(city, entry["metric"], entry["transform"])
                if component is None:
                    missing.append(entry["metric"])
                    continue
                component.weight = float(entry.get("weight", 1.0))
                components.append(component)

            min_components = int(spec.get("min_components", 2))
            if len(components) < min_components:
                results[index_id] = IndexResult(
                    index_id=index_id,
                    label=spec["label"],
                    question=spec.get("question", ""),
                    value=None,
                    period=None,
                    components=components,
                    missing=missing,
                    insufficient=True,
                )
                continue

            weight_total = sum(c.weight for c in components)
            value = sum(c.oriented * c.weight for c in components) / weight_total
            results[index_id] = IndexResult(
                index_id=index_id,
                label=spec["label"],
                question=spec.get("question", ""),
                value=round(value, 4),
                period=max(c.period for c in components),
                components=components,
                missing=missing,
            )
        return results

    def index_history(self, city: City, index_id: str) -> dict[str, float]:
        """The index recomputed at every period, for the regime timeline."""
        spec = self.rules["indices"][index_id]
        weighted: dict[str, list[tuple[float, float]]] = {}
        for entry in spec["components"]:
            scores, _ = self.oriented_z(city, entry["metric"], entry["transform"])
            weight = float(entry.get("weight", 1.0))
            for period, value in scores.items():
                weighted.setdefault(period, []).append((value, weight))

        min_components = int(spec.get("min_components", 2))
        out: dict[str, float] = {}
        for period, pairs in weighted.items():
            if len(pairs) < min_components:
                continue
            total = sum(w for _, w in pairs)
            out[period] = round(sum(v * w for v, w in pairs) / total, 4)
        return out


def _max_gap(cadence: str) -> int:
    """How far an annual denominator may be carried, in the numerator's periods."""
    return {"monthly": 12, "quarterly": 4, "annual": 1, "weekly": 52, "daily": 365}.get(cadence, 4)


class SignatureEngine:
    """How much of each archetype's declared pattern the city currently shows."""

    def __init__(self, config: Config, index_engine: IndexEngine) -> None:
        self.config = config
        self.engine = index_engine
        self.rules = config.ruleset("signatures")

    def match(self, city: City) -> list[dict[str, Any]]:
        threshold = float(self.rules.get("threshold", 0.5))
        min_coverage = float(self.rules.get("min_coverage", 0.34))
        out: list[dict[str, Any]] = []

        for archetype_id, spec in self.rules["archetypes"].items():
            firing: list[dict[str, Any]] = []
            against: list[dict[str, Any]] = []
            unavailable: list[str] = []

            for signal in spec["signals"]:
                metric_id = signal["metric"]
                scores, component = self.engine.oriented_z(city, metric_id, signal["transform"])
                if component is None:
                    unavailable.append(metric_id)
                    continue

                # `expect` is stated in the metric's own terms (rents up, jobs
                # down), so compare against the plain z-score rather than the
                # direction-oriented one.
                z = component.z
                matched = z >= threshold if signal["expect"] == "up" else z <= -threshold
                entry = {
                    "metric_id": metric_id,
                    "label": component.label,
                    "expect": signal["expect"],
                    "z": z,
                    "raw": component.raw,
                    "unit": component.unit,
                    "period": component.period,
                    "geo_level": component.geo_level,
                    "source_id": component.source_id,
                }
                (firing if matched else against).append(entry)

            available = len(firing) + len(against)
            total = len(spec["signals"])
            coverage = available / total if total else 0.0

            out.append(
                {
                    "archetype_id": archetype_id,
                    "label": spec["label"],
                    "summary": (spec.get("summary") or "").strip(),
                    "firing": len(firing),
                    "available": available,
                    "total": total,
                    "coverage": round(coverage, 3),
                    "score": round(len(firing) / available, 3) if available else None,
                    "insufficient_coverage": coverage < min_coverage,
                    "signals_firing": firing,
                    "signals_against": against,
                    "signals_unavailable": sorted(unavailable),
                    "signatures_version": self.rules["version"],
                }
            )

        out.sort(key=lambda a: (a["score"] is None, -(a["score"] or 0), -a["coverage"]))
        return out
