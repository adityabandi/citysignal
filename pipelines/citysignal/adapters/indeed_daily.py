"""Preserve daily hiring intent; never silently select unadjusted columns."""
import io
import math
import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, SourceManifest
from ..framework.fetch import FetchPlan
from ..framework.record import CanonicalRecord, utc_today

HIRING_COUNTRIES = ("ES", "FR", "DE", "IT", "NL", "US", "GB", "CA", "AU", "IE")


class IndeedDailyAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="indeed_daily", publisher="Indeed Hiring Lab",
        license="CC BY 4.0 (Hiring Lab repository); verify redistribution before commercial delivery",
        attribution="Source: Indeed Hiring Lab", docs_url="https://github.com/hiring-lab/job_postings_tracker",
        cadence="daily", geo_level="nation", max_age_days=21, formats=("csv",),
        kind="research", revisions_allowed=True,
        notes="Daily seasonally adjusted total and new job posting indices. 1 February 2020=100; publisher uses a seven-day moving average. Platform coverage is not the whole labour market.",
    )

    def discover(self, ctx):
        return [FetchPlan(
            url=f"https://raw.githubusercontent.com/hiring-lab/job_postings_tracker/master/{code}/aggregate_job_postings_{code}.csv",
            fmt="csv", label=f"hiring-{code}", optional=True, meta={"geo": code.lower()},
        ) for code in HIRING_COUNTRIES]

    def parse(self, payload, ctx):
        frame = pd.read_csv(io.StringIO(payload.text()))
        required = {"date", "jobcountry", "indeed_job_postings_index_SA", "variable"}
        if not required.issubset(frame.columns):
            raise AdapterFailure("Hiring Lab schema changed; refusing to infer columns")
        return frame.sort_values("date")

    def normalize(self, frame, plan, ctx):
        mapping = {"total postings": "hiring_total", "new postings": "hiring_new"}
        seen = set()
        for row in frame.itertuples(index=False):
            metric = mapping.get(str(row.variable).lower())
            if not metric:
                continue
            geo = str(row.jobcountry).lower()
            if geo != plan.meta["geo"]:
                raise AdapterFailure("Hiring country does not match requested country")
            period = str(row.date)
            if period >= utc_today().isoformat():
                continue
            value = float(row.indeed_job_postings_index_SA)
            if not math.isfinite(value) or value < 0 or (metric, period) in seen:
                raise AdapterFailure("Invalid or duplicate hiring observation")
            seen.add((metric, period))
            yield CanonicalRecord(metric_id=metric, geo_id=geo, period=period,
                value=value, unit="index", source_id=self.manifest.source_id)
        if not seen:
            raise AdapterFailure("No hiring observations")
