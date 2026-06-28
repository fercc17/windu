"""Shared interval primitive for point-in-time reconstruction (Art. VII).

A field's value over time is a sequence of non-overlapping, contiguous spans
rebuilt from the creation value plus the ordered changelog of that field. Both
priority and status intervals share this builder.

Pure logic: no database, no Jira, no third-party deps.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Interval:
    """A span during which a field held a single ``value``.

    ``valid_to is None`` means the interval is still in effect.
    """

    value: str | None
    valid_from: datetime
    valid_to: datetime | None

    def covers(self, t: datetime) -> bool:
        if t < self.valid_from:
            return False
        return self.valid_to is None or t < self.valid_to


@dataclass(frozen=True)
class Change:
    """One ordered field transition from the changelog."""

    changed_at: datetime
    to_value: str | None


def build_intervals(
    created_at: datetime,
    created_value: str | None,
    changes: list[Change],
) -> list[Interval]:
    """Reconstruct contiguous intervals from a creation value + ordered changes.

    Seeds ``(created_at, created_value)``; for each change in chronological order,
    closes the open interval at ``changed_at`` and opens a new one with ``to_value``.
    Changes at or before ``created_at`` are clamped to ``created_at`` so the result
    stays monotonic. Zero-length spans (two changes at the same instant) are dropped.

    Guarantees (validated by :func:`validate`): contiguous, no gaps/overlaps,
    exactly one open interval.
    """
    ordered = sorted(changes, key=lambda c: c.changed_at)
    intervals: list[Interval] = []
    cur_value = created_value
    cur_from = created_at

    for change in ordered:
        at = max(change.changed_at, created_at)
        if at > cur_from:
            intervals.append(Interval(cur_value, cur_from, at))
            cur_from = at
        # at == cur_from: zero-length span — just adopt the newer value.
        cur_value = change.to_value

    intervals.append(Interval(cur_value, cur_from, None))
    return intervals


def value_at(intervals: list[Interval], t: datetime) -> str | None:
    for iv in intervals:
        if iv.covers(t):
            return iv.value
    return None


def validate(intervals: list[Interval]) -> None:
    """Raise ``ValueError`` if intervals are not contiguous with one open span."""
    if not intervals:
        raise ValueError("no intervals")
    open_count = sum(1 for iv in intervals if iv.valid_to is None)
    if open_count != 1 or intervals[-1].valid_to is not None:
        raise ValueError("expected exactly one open interval, at the end")
    for a, b in zip(intervals, intervals[1:]):
        if a.valid_to != b.valid_from:
            raise ValueError(f"non-contiguous intervals: {a.valid_to} != {b.valid_from}")
