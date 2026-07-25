"""EUROCONTROL daily airport traffic — movements, not passengers, and never a city measure.

EUROCONTROL's Aviation Intelligence Portal publishes one CSV per calendar year
covering every IFR and non-IFR airport movement it tracks across the pan-European
network, refreshed daily. ``FLT_TOT_1`` is departures plus arrivals for that airport
on that day — a count of *flights*, not people. It moves before passenger counts do:
airlines file a new route in the schedule weeks before the first ticket is sold, and
cut frequencies at the first sign of soft demand, well before an occupancy report
would show it. That is why this metric is tagged ``leading`` while Aena's passenger
count is ``coincident``.

The catchment is the airport, not the city that shares its name. Málaga-Costa del Sol
airport (AGP) serves the whole Costa del Sol, not the municipality of Málaga; a spike
in AGP traffic is metropolitan-or-wider evidence, and the site must never present it
as if it were a city-level count. ``geo_level: airport`` exists specifically so this
distinction survives into the data model — it is deliberately impossible to construct
an ``airport_flights`` record with a ``mun-*`` geo_id.

Only the eight airports serving our cities are kept; the daily file also carries
Heathrow, Frankfurt and everywhere else in EUROCONTROL's coverage, which is discarded
immediately in ``parse`` to keep the working set small. Daily rows are staged and
rolled up to monthly totals in ``finalize``, and the current (still accumulating)
month is dropped every run — a partial month next to eleven complete ones would read
as a collapse.
"""

from __future__ import annotations

import io
from collections import defaultdict
from datetime import date
from typing import Iterable

import pandas as pd

from ..framework.adapter import BaseAdapter, RunContext, SourceManifest
from ..framework.fetch import FetchPlan, RawPayload
from ..framework.record import CanonicalRecord, airport

BASE_URL = "https://www.eurocontrol.int/performance/data/download/csv/airport_traffic_{year}.csv"

# ICAO -> IATA for the eight airports serving our cities.
AIRPORTS: dict[str, str] = {
    "LEMD": "MAD",
    "LEBL": "BCN",
    "LEVC": "VLC",
    "LEMG": "AGP",
    "LEZL": "SVQ",
    "LEPA": "PMI",
    "LEBB": "BIO",
    "LEZG": "ZAZ",
}

# EUROCONTROL's file is one per calendar year; fetch just enough history to be
# useful without pulling a decade of pan-European daily rows every run.
YEARS_OF_HISTORY = 4


class EurocontrolAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="eurocontrol",
        publisher="EUROCONTROL",
        license="EUROCONTROL open data terms (attribution, non-commercial research)",
        attribution="Source: EUROCONTROL Aviation Intelligence Portal",
        docs_url="https://ansperformance.eu/data/",
        cadence="monthly",
        geo_level="airport",
        max_age_days=45,
        formats=("csv",),
        redistribute=False,
        revisions_allowed=False,
        kind="official",
        expected_columns=("APT_ICAO", "FLT_DATE", "FLT_TOT_1"),
        min_rows=1000,
        # Aggregation happens within a single plan (one year = whole months), never
        # across plans, so unchanged past years are free to skip via content hash.
        aggregates_across_plans=False,
        notes=(
            "Movements (departures + arrivals), not passengers. Metropolitan/FUA "
            "evidence via the airport, never presented as a municipal measure."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        this_year = date.today().year
        plans: list[FetchPlan] = []
        for year in range(this_year - YEARS_OF_HISTORY + 1, this_year + 1):
            plans.append(
                FetchPlan(
                    url=BASE_URL.format(year=year),
                    fmt="csv",
                    label=f"eurocontrol:{year}",
                    # An early year 404ing (portal only keeps N years live) costs
                    # us history depth, not the source itself.
                    optional=True,
                    meta={"year": year},
                )
            )
        return plans

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        frame = pd.read_csv(
            io.BytesIO(payload.content),
            usecols=["FLT_DATE", "APT_ICAO", "FLT_TOT_1"],
            dtype={"APT_ICAO": "string"},
        )
        # Drop the other ~2000 European airports immediately; only ours matter.
        return frame[frame["APT_ICAO"].isin(AIRPORTS)]

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        # Daily rows are staged, not emitted — the canonical observation is monthly,
        # and only exists once every day in the month has been summed in finalize().
        for row in frame.itertuples(index=False):
            iata = AIRPORTS.get(row.APT_ICAO)
            total = row.FLT_TOT_1
            if iata is None or pd.isna(total):
                continue
            period = str(row.FLT_DATE)[:7]
            self._monthly[(iata, period)] += float(total)
        return ()

    def finalize(
        self, records: list[CanonicalRecord], ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        current_month = date.today().strftime("%Y-%m")
        out: list[CanonicalRecord] = list(records)
        for (iata, period), total in sorted(self._monthly.items()):
            if period >= current_month:
                continue  # still accumulating; would read as a collapse
            out.append(
                CanonicalRecord(
                    metric_id="airport_flights",
                    geo_id=airport(iata),
                    period=period,
                    value=total,
                    unit="flights",
                    source_id=self.manifest.source_id,
                )
            )
        return out

    # Staging state, reset per run by __init__.
    _monthly: defaultdict[tuple[str, str], float]

    def __init__(self) -> None:
        self._monthly = defaultdict(float)
