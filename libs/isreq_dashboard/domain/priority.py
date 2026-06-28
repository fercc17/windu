"""Priority intervals and Highest entry/exit events (Art. VII, FR-007/008/009).

All "was this ticket Highest during period W" questions are answered from these
reconstructed intervals — never from the current priority. Each transition *into*
Highest is a distinct entry event; a Highest interval that ends by a priority change
is a *drop-below* exit. (Close-while-Highest exits are added by the metric layer,
which joins priority intervals to status close events.)

Pure logic: no database, no Jira.
"""

from __future__ import annotations

from datetime import datetime

from isreq_dashboard.domain.intervals import Change, Interval, build_intervals, value_at


def build_priority_intervals(
    created_at: datetime,
    created_priority: str | None,
    changes: list[Change],
) -> list[Interval]:
    """Rebuild a ticket's priority spans from creation priority + priority changes."""
    return build_intervals(created_at, created_priority, changes)


def entries_into(intervals: list[Interval], target: str) -> list[datetime]:
    """Timestamps of each entry *into* ``target`` priority (creation-at-target included).

    An entry is an interval whose value is ``target`` and whose immediately preceding
    interval was not ``target`` (the first interval always counts if it is ``target``).
    Each entry is returned separately, so raised -> dropped -> raised yields two.
    """
    out: list[datetime] = []
    prev_value: str | None = None
    for iv in intervals:
        if iv.value == target and prev_value != target:
            out.append(iv.valid_from)
        prev_value = iv.value
    return out


def drop_below_exits(intervals: list[Interval], target: str) -> list[datetime]:
    """Timestamps where a ``target`` interval ended by changing to a non-target value.

    A close that happens while still ``target`` is NOT here (priority stays target);
    the metric layer adds those from status close events.
    """
    out: list[datetime] = []
    for iv, nxt in zip(intervals, intervals[1:]):
        if iv.value == target and nxt.value != target:
            out.append(iv.valid_to)  # == nxt.valid_from
    return out


def was_target_at(intervals: list[Interval], t: datetime, target: str) -> bool:
    return value_at(intervals, t) == target
