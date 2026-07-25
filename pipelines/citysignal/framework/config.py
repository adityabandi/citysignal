"""Loading and cross-checking the YAML registries in config/."""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@dataclass(frozen=True, slots=True)
class City:
    slug: str
    name: str
    ine_mun: str
    ine_prov: str
    ine_ccaa: str
    ccaa_name: str
    province_name: str
    airports: tuple[str, ...]
    ports: tuple[str, ...]
    fua_id: str | None
    insideairbnb: str | None
    deep_dive: bool
    wikipedia: dict[str, str]
    aliases: tuple[str, ...] = ()

    @property
    def geo_id(self) -> str:
        return f"mun-{self.ine_mun}"

    @property
    def province_geo_id(self) -> str:
        return f"prov-{self.ine_prov}"

    @property
    def ccaa_geo_id(self) -> str:
        return f"ccaa-{self.ine_ccaa}"


@dataclass(frozen=True, slots=True)
class Config:
    root: Path
    cities: tuple[City, ...]
    metrics: dict[str, dict]
    sources: dict[str, dict]
    baskets: dict[str, Any]
    rules: dict[str, Any]

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    def city(self, slug: str) -> City:
        for city in self.cities:
            if city.slug == slug:
                return city
        raise KeyError(slug)

    def city_by_mun(self, ine_mun: str) -> City | None:
        code = str(ine_mun).zfill(5)
        for city in self.cities:
            if city.ine_mun == code:
                return city
        return None

    def city_by_province(self, ine_prov: str) -> list[City]:
        code = str(ine_prov).zfill(2)
        return [c for c in self.cities if c.ine_prov == code]

    @property
    def municipality_codes(self) -> set[str]:
        return {c.ine_mun for c in self.cities}

    @property
    def province_codes(self) -> set[str]:
        return {c.ine_prov for c in self.cities}

    def metrics_for(self, source_id: str) -> dict[str, dict]:
        return {k: v for k, v in self.metrics.items() if v.get("source_id") == source_id}


@functools.lru_cache(maxsize=4)
def load_config(root: Path | None = None) -> Config:
    root = Path(root) if root else repo_root()
    config_dir = root / "config"

    raw_cities = _load(config_dir / "cities.yml")["cities"]
    cities = tuple(
        City(
            slug=c["slug"],
            name=c["name"],
            ine_mun=str(c["ine_mun"]).zfill(5),
            ine_prov=str(c["ine_prov"]).zfill(2),
            ine_ccaa=str(c["ine_ccaa"]).zfill(2),
            ccaa_name=c["ccaa_name"],
            province_name=c["province_name"],
            airports=tuple(c.get("airports") or ()),
            ports=tuple(c.get("ports") or ()),
            fua_id=c.get("fua_id"),
            insideairbnb=c.get("insideairbnb"),
            deep_dive=bool(c.get("deep_dive")),
            wikipedia=dict(c.get("wikipedia") or {}),
            aliases=tuple(c.get("aliases") or ()),
        )
        for c in raw_cities
    )

    metrics_doc = _load(config_dir / "metrics.yml") or {}
    metrics: dict[str, dict] = {}
    for metric_id, meta in (metrics_doc.get("metrics") or {}).items():
        meta = dict(meta)
        if "range" in meta and meta["range"] is not None:
            meta["range"] = tuple(meta["range"])
        metrics[metric_id] = meta

    sources_doc = _load(config_dir / "sources.yml") or {}
    sources = dict(sources_doc.get("sources") or {})

    baskets: dict[str, Any] = {}
    basket_dir = config_dir / "baskets"
    if basket_dir.exists():
        for path in sorted(basket_dir.glob("*.yml")):
            baskets[path.stem] = _load(path)

    rules: dict[str, Any] = {}
    rules_dir = config_dir / "rules"
    if rules_dir.exists():
        for path in sorted(rules_dir.glob("*.yml")):
            rules[path.stem] = _load(path)

    return Config(
        root=root,
        cities=cities,
        metrics=metrics,
        sources=sources,
        baskets=baskets,
        rules=rules,
    )
