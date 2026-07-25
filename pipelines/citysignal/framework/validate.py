"""Record-level validation applied before anything touches history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .record import CanonicalRecord, RecordError, validate_geo, validate_period


class ValidationError(ValueError):
    pass


@dataclass(slots=True)
class ValidationReport:
    checked: int = 0
    rejected: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.rejected is None:
            self.rejected = []

    @property
    def ok(self) -> bool:
        return not self.rejected


def validate_records(
    records: Sequence[CanonicalRecord],
    *,
    source_id: str,
    metrics: dict[str, dict],
    value_ranges: dict[str, tuple[float, float]] | None = None,
    max_reject_ratio: float = 0.02,
) -> ValidationReport:
    """Check every record against the metric registry and the manifest's ranges.

    A handful of bad rows in a big file is a data problem worth flagging; a large
    fraction of bad rows means the file format changed under us, which is an
    adapter failure and must stop the merge.
    """
    report = ValidationReport(checked=len(records))
    ranges = value_ranges or {}

    for record in records:
        try:
            meta = metrics.get(record.metric_id)
            if meta is None:
                raise ValidationError(f"unknown metric_id {record.metric_id!r}")
            if meta.get("source_id") not in (None, source_id):
                raise ValidationError(
                    f"metric {record.metric_id!r} belongs to source {meta['source_id']!r}, "
                    f"not {source_id!r}"
                )
            validate_period(record.period, meta["cadence"])
            validate_geo(record.geo_id, record.geo_level or "")
            # The registry states the level a metric is genuinely published at.
            # An adapter emitting a different level means either the source
            # changed or someone promoted a provincial figure to a city — the
            # exact substitution this project exists to prevent. Fix the registry
            # or fix the adapter; never let the two disagree silently.
            if record.geo_level != meta["geo_level"]:
                raise ValidationError(
                    f"{record.metric_id}: registry declares {meta['geo_level']!r} but "
                    f"{record.geo_id} is {record.geo_level!r}. If the publisher really "
                    "reports at this level, update config/metrics.yml deliberately."
                )
            if record.unit != meta["unit"]:
                raise ValidationError(
                    f"{record.metric_id}: unit {record.unit!r} != registry unit {meta['unit']!r}"
                )
            if record.value is not None:
                lo, hi = ranges.get(record.metric_id, meta.get("range", (float("-inf"), float("inf"))))
                if not (lo <= record.value <= hi):
                    raise ValidationError(
                        f"{record.metric_id} {record.geo_id} {record.period}: "
                        f"value {record.value} outside [{lo}, {hi}]"
                    )
        except (ValidationError, RecordError, KeyError) as exc:
            report.rejected.append(str(exc))

    if records and len(report.rejected) / len(records) > max_reject_ratio:
        raise ValidationError(
            f"{source_id}: {len(report.rejected)}/{len(records)} records rejected "
            f"(over {max_reject_ratio:.0%}) — treating as a format change. "
            f"First: {report.rejected[0]}"
        )
    return report


def keep_valid(
    records: Iterable[CanonicalRecord],
    *,
    metrics: dict[str, dict],
    value_ranges: dict[str, tuple[float, float]] | None = None,
) -> list[CanonicalRecord]:
    """Filter to records that pass validation, without raising on the strays."""
    ranges = value_ranges or {}
    kept: list[CanonicalRecord] = []
    for record in records:
        meta = metrics.get(record.metric_id)
        if meta is None:
            continue
        try:
            validate_period(record.period, meta["cadence"])
        except RecordError:
            continue
        if record.value is not None:
            lo, hi = ranges.get(record.metric_id, meta.get("range", (float("-inf"), float("inf"))))
            if not (lo <= record.value <= hi):
                continue
        kept.append(record)
    return kept
