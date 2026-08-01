"""The desk view: an official tape, and the signals nobody else is watching.

Two kinds of number belong on a market desk and they want different treatment.

**The official tape** is the stuff everyone already has — rents, mortgage rates,
transactions, unemployment. It belongs in its real units, because a mortgage rate
means something as 2.85% and nothing as a score.

**The unconventional signals** are the ones this project went looking for:
restaurant density from OpenStreetMap's edit history, attention by language
edition, eviction searches, business premises opening and closing. These have no
natural unit a reader can hold — "0.38 food share" is meaningless on its own — so
they are published as a diffusion index instead.

The index is the normal CDF of the metric's z-score against its own history,
scaled to 0-100. That makes it a percentile with a plain reading: 63 means the
current value is higher than 63% of everything this city has recorded for that
measure. 50 is exactly typical. It is the same underlying number as the z-score
shown elsewhere on the site, in a form somebody can act on.

Nothing here is a new measurement. It is a presentation of series that already
exist, which is why the desk can be rebuilt from scratch at any time and cannot
drift from the rest of the site.
"""

from __future__ import annotations

import math
from typing import Any

from ..framework.config import City, Config
from .store import HistoryStore
from .transforms import yoy, zscore

# Ticker codes. Purely a display convention, but a stable one: a reader who
# learns OSM-FOOD should find it in the same place next month.
TICKERS = {
    "osm_food_share": "OSM-FOOD",
    "osm_food_count": "OSM-CNT",
    "osm_tourist_retail": "TOURIST-RTL",
    "osm_cafe_bar_ratio": "CAFE-BAR",
    "horeca_openings": "HORECA-OPN",
    "horeca_closures": "HORECA-CLS",
    "locales_vacancy_rate": "SHUTTER",
    "search_eviction": "EVICT-SRCH",
    "search_hardship": "STRESS-SRCH",
    "search_room_share": "ROOM-SHARE",
    "search_tenure_switch": "BUY-RENT",
    "search_rental_pressure": "RENT-SRCH",
    "search_relocation": "RELOC-SRCH",
    "search_commercial_rent": "LOCAL-SRCH",
    "wiki_foreign_interest": "FOREIGN-EYE",
    "wiki_barrio_views": "BARRIO-EYE",
    "wiki_hardship_index": "WIKI-STRESS",
    "wiki_city_attention": "CITY-EYE",
    "vut_licensed": "STR-LIC",
    "str_listings": "STR-LST",
    "licences_granted": "PERMIT",
    "visados_new_build": "VISADO",
    "vehicle_registrations": "VEHICLE",
    "job_postings_index": "POSTINGS",
    "airport_flights": "FLIGHTS",
    "births": "BIRTHS",
    "marriages": "MARRIAGE",
    # Official tape.
    "euribor_12m": "EURIBOR",
    "ecb_mortgage_rate": "ECB-MTG",
    "mortgage_rate_new": "ES-MTG",
    "hicp_rents": "HICP-RENT",
    "hicp_maintenance": "HICP-MAINT",
    "rent_reference": "RENT-REF",
    "appraised_value": "APPRAISE",
    "property_transfers": "TRANSFER",
    "unemployment_registered": "UNEMP",
    "affiliations": "AFFIL",
    "house_price_index": "HPI",
    "hotel_nights": "HOTEL-NT",
    # World and euro area.
    "ecb_policy_rate": "ECB-RATE",
    "euro_inflation": "EA-CPI",
    "euro_unemployment": "EA-UNEMP",
    "eur_usd": "EURUSD",
    "eur_gbp": "EURGBP",
    "eur_jpy": "EURJPY",
    "eur_sek": "EURSEK",
    "eur_nok": "EURNOK",
    "eur_chf": "EURCHF",
}

# The official tape, in the order a reader wants it: money, then prices, then
# activity, then the labour market.
OFFICIAL_TAPE = [
    "euribor_12m",
    "ecb_mortgage_rate",
    "hicp_rents",
    "rent_reference",
    "appraised_value",
    "property_transfers",
    "unemployment_registered",
    "affiliations",
]

# Signals that earn their place by being unusual, not by being official.
DESK_SIGNALS = [
    "osm_food_share",
    "osm_tourist_retail",
    "osm_cafe_bar_ratio",
    "locales_vacancy_rate",
    "horeca_openings",
    "search_eviction",
    "search_tenure_switch",
    "search_room_share",
    "search_relocation",
    "wiki_foreign_interest",
    "vut_licensed",
    "visados_new_build",
    "vehicle_registrations",
    "job_postings_index",
]


def _phi(z: float) -> float:
    """Standard normal CDF, so a z-score becomes a percentile."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def diffusion(z: float | None) -> int | None:
    """z-score → 0-100, where 50 is exactly typical for this city.

    A percentile is the one summary a non-specialist reads correctly without
    being taught: 63 means higher than 63% of this city's own history. Compare
    with "+0.33 sigma", which requires knowing what a standard deviation is and
    still does not say how unusual that is.
    """
    if z is None:
        return None
    return round(_phi(max(-4.0, min(4.0, z))) * 100)


def _read(value: int | None, direction: int) -> str:
    """Whether a reading is expansionary, neutral or contractionary.

    Oriented by the metric's declared direction, so a rising vacancy rate reads
    as contractionary even though the number went up.
    """
    if value is None:
        return "unknown"
    oriented = value if direction >= 0 else 100 - value
    if oriented >= 60:
        return "expansionary"
    if oriented <= 40:
        return "contractionary"
    return "neutral"


class DeskBuilder:
    def __init__(self, config: Config, store: HistoryStore) -> None:
        self.config = config
        self.store = store

    # How a district-level metric becomes one city-level number. A rate is
    # averaged; a count is summed. Getting this backwards would either divide
    # Madrid's permits by 21 or multiply its vacancy rate by it.
    DISTRICT_AGGREGATION = {
        "osm_food_share": "mean",
        "osm_tourist_retail": "mean",
        "osm_cafe_bar_ratio": "mean",
        "locales_vacancy_rate": "mean",
        "rent_district_m2": "mean",
        "horeca_openings": "sum",
        "horeca_closures": "sum",
        "locales_stock": "sum",
        "horeca_stock": "sum",
        "vut_licensed": "sum",
        "licences_granted": "sum",
        "padron_district": "sum",
        "wiki_barrio_views": "sum",
        "search_barrio_attention": "mean",
    }

    def _series_for(self, city: City, metric_id: str):
        series = self.store.for_city(city, metric_id)
        if series is not None and len(series.values) >= 8:
            return series

        how = self.DISTRICT_AGGREGATION.get(metric_id)
        if how is None:
            return None
        districts = self.store.districts_of(city, metric_id)
        if not districts:
            return None

        pooled: dict[str, list[float]] = {}
        for district in districts:
            for period, value in district.values.items():
                pooled.setdefault(period, []).append(value)
        if len(pooled) < 8:
            return None

        combined = {
            period: (sum(vals) / len(vals) if how == "mean" else sum(vals))
            for period, vals in pooled.items()
        }
        first = districts[0]
        return type(first)(
            key=first.key,
            values=combined,
            source_id=first.source_id,
            unit=first.unit,
        )

    def _entry(self, city: City, metric_id: str, *, as_index: bool) -> dict[str, Any] | None:
        series = self._series_for(city, metric_id)
        if series is None:
            return None
        return self._entry_from(series, metric_id, as_index=as_index)

    def _entry_from(self, series, metric_id: str, *, as_index: bool) -> dict[str, Any] | None:
        meta = self.config.metrics.get(metric_id, {})
        periods = sorted(series.values)
        latest = periods[-1]

        scores = zscore(series.values)
        z = scores.get(latest)
        changes = yoy(series.values)

        entry: dict[str, Any] = {
            "metric_id": metric_id,
            "ticker": TICKERS.get(metric_id, metric_id[:9].upper()),
            "label": meta.get("label", metric_id),
            "plain": (meta.get("plain") or "").strip() or None,
            "note": (meta.get("note") or "").strip() or None,
            "unit": meta.get("unit"),
            "section": meta.get("section"),
            "geo_level": series.key.geo_level,
            "source_id": series.source_id,
            "period": latest,
            "value": series.values[latest],
            "yoy": changes.get(latest),
            "observations": len(series.values),
            "series": [{"period": p, "value": series.values[p]} for p in periods[-36:]],
        }

        if as_index:
            direction = int(meta.get("direction", 1))
            raw = diffusion(z)

            # The published index is ORIENTED, so the number and the word always
            # agree. A low room-vs-flat spread is good news — households are not
            # compressing — but showing the raw percentile of 15 next to the word
            # "expansionary" reads as a contradiction, and a reader is right to
            # distrust it. Orienting first means high always means "more
            # expansionary", whichever way the underlying number moved.
            #
            # The unoriented percentile is kept so the transformation stays
            # auditable rather than hidden.
            entry["percentile_raw"] = raw
            entry["inverted"] = direction < 0
            index = raw if raw is None or direction >= 0 else 100 - raw
            entry["index"] = index
            entry["read"] = _read(index, 1)
            if len(periods) > 1:
                prev = diffusion(scores.get(periods[-2]))
                entry["index_prev"] = prev if prev is None or direction >= 0 else 100 - prev
        else:
            entry["z"] = None if z is None else round(z, 2)
        return entry

    # ---- the national view ------------------------------------------------
    # Spain first, cities second. A reader arriving cold wants to know what the
    # market is doing before they want to know what Málaga is doing, and half of
    # what drives a Spanish city — rates, credit, national hiring — is not
    # city-level data at all.
    # Conditions Spain receives rather than sets. Rates and the euro's exchange
    # rate move every Spanish city at once, and for the coastal markets the FX
    # line is not background — foreign buyers are over a third of purchases in
    # Málaga and the Balearics, and their budget is a pure function of it.
    WORLD_TAPE = [
        "ecb_policy_rate",
        "euro_inflation",
        "euro_unemployment",
        "eur_usd",
        "eur_gbp",
        "eur_jpy",
    ]
    WORLD_FX = ["eur_gbp", "eur_usd", "eur_sek", "eur_nok", "eur_chf", "eur_jpy"]

    def build_world(self) -> dict[str, Any]:
        tape = [e for m in self.WORLD_TAPE if (e := self._national_entry(m, as_index=False))]
        fx = [e for m in self.WORLD_FX if (e := self._national_entry(m, as_index=False))]
        return {"scope": "World", "tape": tape, "fx": fx, "carry": self._carry_stress()}

    def _carry_stress(self) -> dict[str, Any] | None:
        """How hard the yen is moving, as a read on carry-trade unwinding.

        The yen funds a large share of the world's leveraged positions, so when it
        appreciates sharply those positions are closed and capital retreats from
        peripheral risk assets — southern European property among them. A falling
        EUR/JPY means a strengthening yen, so the three-month change is negated to
        make rising mean rising stress.

        The reference event is August 2024: EUR/JPY ran 171.17 in July, 161.06 in
        August and 159.08 in September, a 6.3% three-month move, and global
        equities went with it. That episode is what this measure is calibrated to
        recognise, and it is visible in the committed series.
        """
        series = self._national_series("eur_jpy")
        if series is None or len(series.values) < 6:
            return None
        periods = sorted(series.values)
        latest = periods[-1]
        back = periods[-4]
        change = (series.values[latest] - series.values[back]) / series.values[back] * 100
        stress = -change

        history = []
        for index in range(3, len(periods)):
            a, b = series.values[periods[index]], series.values[periods[index - 3]]
            history.append({"period": periods[index], "value": -(a - b) / b * 100})

        return {
            "metric_id": "carry_stress",
            "ticker": "CARRY",
            "label": "Yen carry stress",
            "plain": (
                "How sharply the yen is strengthening. The yen funds much of the world's "
                "borrowed money, so a fast rise forces those trades to close and pulls "
                "capital out of riskier markets."
            ),
            "unit": "percent",
            "geo_level": "world",
            "period": latest,
            "value": round(stress, 2),
            "read": "contractionary" if stress > 4 else "neutral" if stress > -4 else "expansionary",
            "series": history[-36:],
            "reference": "August 2024: a 6.3% three-month move unwound carry trades worldwide.",
        }

    NATIONAL_TAPE = [
        "euribor_12m",
        "ecb_mortgage_rate",
        "hicp_rents",
        "hicp_maintenance",
        "house_price_index",
        "mortgage_rate_new",
    ]
    NATIONAL_SIGNALS = [
        "search_eviction",
        "job_postings_index",
        "visados_new_build",
        "wiki_hardship_index",
        "wiki_relocation_index",
    ]

    def _national_series(self, metric_id: str):
        """A nation-, region- or world-level series, independent of any city."""
        meta = self.config.metrics.get(metric_id, {})
        level = meta.get("geo_level")
        wanted = {
            "nation": "es",
            "ccaa": "ccaa-13",
            "euro_area": "euro-area",
            "world": "world",
        }.get(level)
        for series in self.store:
            if series.key.metric_id != metric_id:
                continue
            if wanted is None or series.key.geo_id == wanted:
                return series if len(series.values) >= 8 else None
        return None

    def _national_entry(self, metric_id: str, *, as_index: bool) -> dict[str, Any] | None:
        series = self._national_series(metric_id)
        if series is None:
            return None
        return self._entry_from(series, metric_id, as_index=as_index)

    def build_national(self, cities: list[City]) -> dict[str, Any]:
        tape = [e for m in self.NATIONAL_TAPE if (e := self._national_entry(m, as_index=False))]
        signals = [e for m in self.NATIONAL_SIGNALS if (e := self._national_entry(m, as_index=True))]

        oriented = [s["index"] for s in signals if s.get("index") is not None]
        composite = round(sum(oriented) / len(oriented)) if oriented else None

        # Every city's own composite, so the national page is a way in rather
        # than a dead end.
        by_city = []
        for city in cities:
            desk = self.build(city)
            by_city.append({
                "slug": city.slug,
                "name": city.name,
                "composite": desk["composite"],
                "signals": desk["composite_n"],
                "top": sorted(
                    (s for s in desk["signals"] if s.get("index") is not None),
                    key=lambda s: abs(s["index"] - 50),
                    reverse=True,
                )[:2],
            })
        by_city.sort(key=lambda c: (c["composite"] is None, -(c["composite"] or 0)))

        return {
            "scope": "Spain",
            "world": self.build_world(),
            "composite": composite,
            "composite_n": len(oriented),
            "tape": tape,
            "signals": signals,
            "cities": by_city,
        }

    def build(self, city: City) -> dict[str, Any]:
        tape = [e for m in OFFICIAL_TAPE if (e := self._entry(city, m, as_index=False))]
        signals = [e for m in DESK_SIGNALS if (e := self._entry(city, m, as_index=True))]

        # Signals are already oriented, so the composite is a plain mean. Higher
        # means a busier city, not a better one — whether that is welcome depends
        # entirely on whether you are buying or selling.
        oriented = [s["index"] for s in signals if s.get("index") is not None]
        composite = round(sum(oriented) / len(oriented)) if oriented else None

        return {
            "city": city.slug,
            "name": city.name,
            "composite": composite,
            "composite_n": len(oriented),
            "tape": tape,
            "signals": signals,
        }
