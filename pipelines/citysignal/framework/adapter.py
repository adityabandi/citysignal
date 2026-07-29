"""The adapter contract.

``BaseAdapter.run`` is framework-owned and adapters never override it. An adapter
supplies three things — where to look (``discover``), how to read the bytes
(``parse``), and how to turn a table into canonical observations (``normalize``).
Everything else — retries, hash skipping, sniffing, validation, revision handling,
quarantine, health reporting — happens once, here, identically for every source.

Failure of one adapter is a data-level event: its history is left untouched, the
run report records it, and the site keeps serving the last-good value with a stale
badge. Only framework-level breakage stops the pipeline.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Iterable, Literal, Sequence

import pandas as pd

from .config import Config
from .fetch import FetchPlan, Fetcher, PayloadError, RawPayload, StateStore, sniff
from .history import append_quarantine, history_path, merge_records
from .quality import AdapterResult, HealthStore, now_iso
from .record import CanonicalRecord
from .validate import validate_records

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SourceManifest:
    source_id: str
    publisher: str
    license: str
    attribution: str
    docs_url: str
    cadence: Literal["daily", "weekly", "monthly", "quarterly", "annual"]
    geo_level: str
    max_age_days: int
    formats: tuple[str, ...] = ("csv",)
    redistribute: bool = True
    revisions_allowed: bool = False
    read_timeout: float | None = None
    """Seconds to wait on one response, when the default is too short.

    Set only where a service does real computation per request rather than
    serving a file. ohsome replays OSM's edit history for the bounding box
    and takes tens of seconds on a large district."""
    kind: Literal["official", "research", "commercial"] = "official"
    expected_columns: tuple[str, ...] = ()
    value_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)
    min_rows: int = 1
    notes: str = ""
    aggregates_across_plans: bool = False
    """Set when finalize() combines several plans into one value.

    Content-hash skipping must then be disabled: a plan whose bytes are unchanged
    still has to be parsed, because leaving it out silently changes the level of a
    sum or the denominator of a ratio. Skipping is a bandwidth optimisation; a
    wrong published number is not worth it.
    """

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "publisher": self.publisher,
            "license": self.license,
            "attribution": self.attribution,
            "docs_url": self.docs_url,
            "cadence": self.cadence,
            "geo_level": self.geo_level,
            "max_age_days": self.max_age_days,
            "redistribute": self.redistribute,
            "kind": self.kind,
            "notes": self.notes,
        }


@dataclass(slots=True)
class RunContext:
    config: Config
    fetcher: Fetcher
    state: StateStore
    health: HealthStore
    force: bool = False
    accept_revisions: set[str] = field(default_factory=set)
    dry_run: bool = False

    @property
    def data_dir(self) -> Path:
        return self.config.data_dir


class AdapterFailure(RuntimeError):
    """Raised inside an adapter to fail cleanly with a human-readable reason."""


class BaseAdapter(ABC):
    manifest: ClassVar[SourceManifest]

    # ---- adapter-supplied ------------------------------------------------
    @abstractmethod
    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        """Resolve the concrete URLs to fetch this run.

        Prefer parsing a listing page over hard-coding a deep link: deep links to
        month-stamped files on Spanish portals rot within a release cycle.
        """

    @abstractmethod
    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        """Bytes to a tidy DataFrame. No canonicalisation here."""

    @abstractmethod
    def normalize(self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext) -> Iterable[CanonicalRecord]:
        """DataFrame to canonical observations, filtered to the eight cities."""

    def finalize(
        self, records: list[CanonicalRecord], ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        """Last pass over every record from every plan, before validation.

        Override when a metric can only be computed once all plans are in — summing
        a basket across articles, rolling daily observations up to months, or
        dividing one fetched series by another. The default is identity.
        """
        return records

    # ---- framework-owned -------------------------------------------------
    def run(self, ctx: RunContext) -> AdapterResult:
        started = time.perf_counter()
        result = AdapterResult(source_id=self.manifest.source_id)
        collected: list[CanonicalRecord] = []

        try:
            plans = self.discover(ctx)
            result.plans = len(plans)
            if not plans:
                raise AdapterFailure("discover() returned no fetch plans")

            failed_plans: list[str] = []
            for plan in plans:
                try:
                    self._run_plan(plan, ctx, result, collected)
                except Exception as exc:  # noqa: BLE001
                    if not plan.optional:
                        raise
                    # Interchangeable fetch: note the gap and keep the rest of the
                    # source. A city missing one language edition is not an outage.
                    failed_plans.append(f"{plan.label or plan.url}: {type(exc).__name__}")

            if failed_plans:
                if len(failed_plans) == len(plans):
                    raise AdapterFailure(
                        f"all {len(plans)} optional fetches failed; first: {failed_plans[0]}"
                    )
                result.notes.append(
                    f"{len(failed_plans)}/{len(plans)} optional fetches skipped: "
                    + "; ".join(failed_plans[:5])
                )

            collected = list(self.finalize(collected, ctx))
            result.records = len(collected)

            if collected:
                validate_records(
                    collected,
                    source_id=self.manifest.source_id,
                    metrics=ctx.config.metrics,
                    value_ranges=self.manifest.value_ranges,
                )
                self._merge(collected, ctx, result)
                result.latest_observation = max(r.period for r in collected)
                result.metrics = sorted({r.metric_id for r in collected})
                result.status = "ok"
            elif result.skipped_unchanged and result.skipped_unchanged == result.plans:
                result.status = "skipped"
                result.notes.append("all sources unchanged since last run")
            else:
                result.status = "partial"
                result.notes.append("fetched successfully but produced no records")

        except Exception as exc:  # noqa: BLE001 — isolation is the point
            result.status = "failed"
            result.error = str(exc)[:900]
            result.error_type = type(exc).__name__
            log.warning("adapter %s failed: %s", self.manifest.source_id, exc)

        result.duration_s = round(time.perf_counter() - started, 2)
        ctx.health.record(result, manifest=self.manifest.to_dict())
        return result

    def _run_plan(
        self,
        plan: FetchPlan,
        ctx: RunContext,
        result: AdapterResult,
        collected: list[CanonicalRecord],
    ) -> None:
        key = plan.cache_key
        may_skip = not ctx.force and not self.manifest.aggregates_across_plans
        headers = ctx.state.validators(key) if may_skip else {}
        payload = ctx.fetcher.get(
            plan, headers=headers, timeout=self.manifest.read_timeout
        )

        if payload is None:  # 304 Not Modified
            ctx.state.touch(key, checked=now_iso())
            result.skipped_unchanged += 1
            return

        if may_skip and payload.sha256 == ctx.state.hash_for(key):
            ctx.state.touch(key, checked=now_iso())
            result.skipped_unchanged += 1
            return

        sniff(
            payload,
            min_rows=self.manifest.min_rows,
            expected_columns=self.manifest.expected_columns if plan.fmt == "csv" else (),
        )
        frame = self.parse(payload, ctx)
        collected.extend(self.normalize(frame, plan, ctx))
        ctx.state.put(key, payload, checked=now_iso())

    def _merge(self, records: Sequence[CanonicalRecord], ctx: RunContext, result: AdapterResult) -> None:
        if ctx.dry_run:
            result.notes.append("dry run: history not written")
            return

        revisions_allowed = (
            self.manifest.revisions_allowed or self.manifest.source_id in ctx.accept_revisions
        )
        by_metric: dict[str, list[CanonicalRecord]] = {}
        for record in records:
            by_metric.setdefault(record.metric_id, []).append(record)

        quarantined: list[dict[str, object]] = []
        for metric_id, metric_records in by_metric.items():
            path = history_path(ctx.data_dir, self.manifest.source_id, metric_id)
            outcome = merge_records(path, metric_records, revisions_allowed=revisions_allowed)
            result.added += outcome.added
            result.revised += outcome.revised
            result.unchanged += outcome.unchanged
            quarantined.extend(outcome.quarantined)

        if quarantined:
            append_quarantine(ctx.data_dir, self.manifest.source_id, quarantined)
            result.quarantined = len(quarantined)
            result.notes.append(
                f"{len(quarantined)} unexpected revisions quarantined — "
                f"re-run with --accept-revision {self.manifest.source_id} if the restatement is genuine"
            )
