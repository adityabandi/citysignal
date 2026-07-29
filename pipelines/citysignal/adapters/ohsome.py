"""OpenStreetMap historical POI density via the ohsome API.

The ohsome REST API (HeiGIT, Heidelberg Institute) exposes the full OSM edit
history as time series. This adapter counts amenities (restaurants, bars,
shops) and normalises by total mapped POI density to isolate commercial
activity from mapping effort itself. Raw OSM counts are published alongside so
the normalisation is auditable.

The critical issue here is that OSM growth confounds two signals: actual
commercial density and the labour of OSM mappers. From 2014–2018, restaurant
counts in Madrid's districts nearly quadrupled, not because Madrid gained
800 restaurants, but because volunteers were adding data retroactively. The
ratio (food POI ÷ all POI) absorbs that mapping effort: a coordinated push
to improve a district will add to both numerator and denominator, and the
ratio stays level unless the commercial mix actually changed.

Raw counts remain available for research; the ratio is the published metric.
Data before 2019 is less trustworthy (mapping pushes, less complete coverage),
so monthly data runs from 2019-01 onward.

Two new ratio metrics measure neighbourhood change rather than size:
- osm_tourist_retail: souvenir shops (shop=gift) ÷ pharmacies (amenity=pharmacy) × 100.
  Spanish pharmacies are licence-capped by law; their count is pinned to population and
  controlled by the same mapper pool, making them an unusually stable denominator.
  Rising ratio indicates touristification of residential cores (e.g. Centro vs Salamanca).
- osm_cafe_bar_ratio: specialty cafés (amenity=cafe) ÷ traditional bars (amenity=bar or pub) × 100.
  Specialty cafés replacing bars is a visible edge of gentrification; the gradient tracks
  affluence gradients in Madrid (Salamanca > Centro > Vallecas).

**Worked example: why betting shops fail as a metric.** shop=bookmaker (casas de apuestas)
per pharmacy should identify gambling concentration, but it fails: Centro 0.08, Salamanca
0.01, Vallecas 0.00 — exactly backwards. Puente de Vallecas is Madrid's best-documented
gambling-problem neighbourhood, subject to street protests and municipal regulation. OSM
has simply not mapped betting shops there. Publishing this metric would assert "no gambling
in Vallecas" — false and opposite reality. An OSM count needs discrimination testing before
publication: a category volunteers do not map looks identical to a category that does not exist.

Geography: district-level observations computed from district bounding boxes
derived from madrid-districts.geojson. A bbox is coarser than the true
polygon boundary (ohsome accepts bpolys GeoJSON if geometry streaming can be
made to work); all observations run at district geo_level.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Iterable

import pandas as pd

from ..framework.adapter import BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.record import CanonicalRecord, district

log = logging.getLogger(__name__)

# ohsome API endpoint for element counts across time
API = "https://api.ohsome.org/v1/elements/count"
# Monthly data from 2019-01 onward — before that, OSM mapping pushes dominate the signal
START = "2019-01-01"


def _month_period(timestamp: str) -> str:
    """Convert ISO date string to YYYY-MM period format."""
    return timestamp[:7]


def _compute_bbox(geometry: dict) -> tuple[float, float, float, float]:
    """Compute min-max bbox from a GeoJSON geometry (Polygon).

    Returns (minLon, minLat, maxLon, maxLat) for the bbox parameter.
    """
    coords = geometry.get("coordinates", [[]])[0]
    if not coords:
        return (0.0, 0.0, 0.0, 0.0)
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return (min(lons), min(lats), max(lons), max(lats))


class OhsomeAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="ohsome",
        publisher="HeiGIT (Heidelberg Institute for Geoinformation Technology)",
        license="ODbL 1.0 (OpenStreetMap data)",
        attribution="Source: ohsome API (openstreetmap.org), HeiGIT",
        docs_url="https://ohsome.org/",
        cadence="monthly",
        geo_level="district",
        max_age_days=45,
        formats=("json",),
        read_timeout=180.0,
        kind="research",
        redistribute=True,
        revisions_allowed=False,
        aggregates_across_plans=True,
        notes=(
            "Commercial POI density via OSM history. Raw counts are mapping-effort-contaminated "
            "(2014–2018 reflect mapper activity, not commercial growth); ratio to total POI "
            "absorbs this. Ratio metrics (tourist retail, cafe-bar) reveal neighbourhood change "
            "rather than size. Monthly data from 2019-01 onward, district-level for Madrid only. "
            "Bbox is coarser than true boundary; bpolys GeoJSON future enhancement."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        """Discover districts and build fetch plans for food and total POI counts."""
        # Load Madrid district bboxes from geojson
        # ctx.data_dir is data/history/, so go up to root
        try:
            geojson_path = ctx.config.root / "data" / "geo" / "madrid-districts.geojson"
            with open(geojson_path) as f:
                geojson = json.load(f)
        except FileNotFoundError as e:
            raise RuntimeError(f"madrid-districts.geojson not found at {geojson_path}: {e}") from e

        plans: list[FetchPlan] = []
        # ohsome has about 1 week lag. To stay safe, always request through
        # the first of the month before last month started, ensuring we're always
        # within the available data window. P1M intervals mean the end date should
        # be a 1st-of-month date for clean month boundaries.
        from datetime import timedelta
        today = date.today()
        # Go back to first of last month (safe buffer)
        first_of_this = today.replace(day=1)
        last_of_prev = first_of_this - timedelta(days=1)
        end_date = last_of_prev.replace(day=1).isoformat()

        for feature in geojson.get("features", []):
            props = feature.get("properties", {})
            district_code = props.get("district_code")
            district_name = props.get("name", "")
            if not district_code:
                continue

            # Compute bbox from polygon
            bbox = _compute_bbox(feature.get("geometry", {}))
            if bbox == (0.0, 0.0, 0.0, 0.0):
                log.warning("skipping district %s: no valid coordinates", district_code)
                continue

            bbox_str = f"{bbox[0]:.3f},{bbox[1]:.3f},{bbox[2]:.3f},{bbox[3]:.3f}"

            # Plan 1: Food amenities (restaurants, cafes, bars, fast food)
            plans.append(
                FetchPlan(
                    url=API,
                    fmt="json",
                    label=f"food:{district_code}",
                    params={
                        "bboxes": bbox_str,
                        "filter": "amenity=restaurant or amenity=cafe or amenity=bar or amenity=fast_food",
                        "time": f"{START}/{end_date}/P1M",
                    },
                    meta={
                        "district_code": district_code,
                        "district_name": district_name,
                        "kind": "food",
                    },
                )
            )

            # Plan 2: Total mapped POI (any node with an amenity tag) — for normalization
            plans.append(
                FetchPlan(
                    url=API,
                    fmt="json",
                    label=f"total_poi:{district_code}",
                    params={
                        "bboxes": bbox_str,
                        "filter": "type:node and amenity=*",
                        "time": f"{START}/{end_date}/P1M",
                    },
                    meta={
                        "district_code": district_code,
                        "district_name": district_name,
                        "kind": "total_poi",
                    },
                )
            )

            # Plan 3: Gift/souvenir shops (numerator for osm_tourist_retail)
            plans.append(
                FetchPlan(
                    url=API,
                    fmt="json",
                    label=f"gift:{district_code}",
                    params={
                        "bboxes": bbox_str,
                        "filter": "shop=gift",
                        "time": f"{START}/{end_date}/P1M",
                    },
                    meta={
                        "district_code": district_code,
                        "district_name": district_name,
                        "kind": "gift",
                    },
                )
            )

            # Plan 4: Pharmacies (denominator for osm_tourist_retail, stable due to legal caps)
            plans.append(
                FetchPlan(
                    url=API,
                    fmt="json",
                    label=f"pharmacy:{district_code}",
                    params={
                        "bboxes": bbox_str,
                        "filter": "amenity=pharmacy",
                        "time": f"{START}/{end_date}/P1M",
                    },
                    meta={
                        "district_code": district_code,
                        "district_name": district_name,
                        "kind": "pharmacy",
                    },
                )
            )

            # Plan 5: Cafés (numerator for osm_cafe_bar_ratio)
            plans.append(
                FetchPlan(
                    url=API,
                    fmt="json",
                    label=f"cafe:{district_code}",
                    params={
                        "bboxes": bbox_str,
                        "filter": "amenity=cafe",
                        "time": f"{START}/{end_date}/P1M",
                    },
                    meta={
                        "district_code": district_code,
                        "district_name": district_name,
                        "kind": "cafe",
                    },
                )
            )

            # Plan 6: Bars and pubs (denominator for osm_cafe_bar_ratio)
            plans.append(
                FetchPlan(
                    url=API,
                    fmt="json",
                    label=f"bar_pub:{district_code}",
                    params={
                        "bboxes": bbox_str,
                        "filter": "amenity=bar or amenity=pub",
                        "time": f"{START}/{end_date}/P1M",
                    },
                    meta={
                        "district_code": district_code,
                        "district_name": district_name,
                        "kind": "bar_pub",
                    },
                )
            )

        return plans

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        """Extract time series from ohsome response."""
        data = payload.json()
        items = data.get("result", [])
        if not items:
            raise RuntimeError(f"empty result from {payload.plan.label}")

        # ohsome returns [{timestamp: "2019-01-01T00:00:00Z", value: N}, ...]
        df = pd.DataFrame(items)
        if df.empty:
            raise RuntimeError(f"no data points from {payload.plan.label}")

        # Extract YYYY-MM period from ISO timestamp
        df["period"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m")
        return df[["period", "value"]]

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        """Stage data for finalize() to compute ratios."""
        meta = plan.meta
        district_code = meta["district_code"]
        kind = meta["kind"]

        for row in frame.itertuples():
            key = (district_code, row.period)
            if kind == "food":
                self._food_counts[key] = int(row.value)
            elif kind == "total_poi":
                self._total_poi[key] = int(row.value)
            elif kind == "gift":
                self._gift_counts[key] = int(row.value)
            elif kind == "pharmacy":
                self._pharmacy_counts[key] = int(row.value)
            elif kind == "cafe":
                self._cafe_counts[key] = int(row.value)
            elif kind == "bar_pub":
                self._bar_pub_counts[key] = int(row.value)

        return ()

    def finalize(
        self, records: list[CanonicalRecord], ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        """Compute normalized ratios and emit records."""
        out: list[CanonicalRecord] = list(records)

        for (district_code, period), food_count in sorted(self._food_counts.items()):
            total_count = self._total_poi.get((district_code, period), 0)

            # Emit raw count (with caveat about mapping effort in manifest notes)
            out.append(
                CanonicalRecord(
                    metric_id="osm_food_count",
                    geo_id=district("28079", district_code),
                    period=period,
                    value=float(food_count),
                    unit="count",
                    source_id=self.manifest.source_id,
                )
            )

            # Emit normalized ratio if we have a valid denominator
            if total_count > 0:
                ratio = 100.0 * food_count / total_count
                out.append(
                    CanonicalRecord(
                        metric_id="osm_food_share",
                        geo_id=district("28079", district_code),
                        period=period,
                        value=round(ratio, 3),
                        unit="percent",
                        source_id=self.manifest.source_id,
                    )
                )

        # Emit tourist_retail ratio: gift shops / pharmacies * 100
        for (district_code, period), gift_count in sorted(self._gift_counts.items()):
            pharmacy_count = self._pharmacy_counts.get((district_code, period), 0)
            if pharmacy_count > 0:
                ratio = 100.0 * gift_count / pharmacy_count
                out.append(
                    CanonicalRecord(
                        metric_id="osm_tourist_retail",
                        geo_id=district("28079", district_code),
                        period=period,
                        value=round(ratio, 3),
                        unit="index",
                        source_id=self.manifest.source_id,
                    )
                )

        # Emit cafe_bar_ratio: cafés / (bars + pubs) * 100
        for (district_code, period), cafe_count in sorted(self._cafe_counts.items()):
            bar_pub_count = self._bar_pub_counts.get((district_code, period), 0)
            if bar_pub_count > 0:
                ratio = 100.0 * cafe_count / bar_pub_count
                out.append(
                    CanonicalRecord(
                        metric_id="osm_cafe_bar_ratio",
                        geo_id=district("28079", district_code),
                        period=period,
                        value=round(ratio, 3),
                        unit="index",
                        source_id=self.manifest.source_id,
                    )
                )

        return out

    # Staging state, rebuilt per run
    _food_counts: dict[tuple[str, str], int]
    _total_poi: dict[tuple[str, str], int]
    _gift_counts: dict[tuple[str, str], int]
    _pharmacy_counts: dict[tuple[str, str], int]
    _cafe_counts: dict[tuple[str, str], int]
    _bar_pub_counts: dict[tuple[str, str], int]

    def __init__(self) -> None:
        self._food_counts = {}
        self._total_poi = {}
        self._gift_counts = {}
        self._pharmacy_counts = {}
        self._cafe_counts = {}
        self._bar_pub_counts = {}
