"""Status intervals, close events, and open-at-T (R-005, FR-015/016).

A *close event* is each entry into the configured closed-status set, keyed by the
moment of closing. A closed -> reopened -> reclosed ticket yields >= 2 close events
(counted at each close, FR-015). Backlog "open at T" is created-on/before-T and not
in a closed status as of T, with reopen support.

Pure logic: no database, no Jira.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from isreq_dashboard.domain.intervals import Change, Interval, build_intervals, value_at


def build_status_intervals(
    created_at: datetime,
    created_status: str | None,
    changes: list[Change],
) -> list[Interval]:
    """Rebuild a ticket's status spans from creation status + status changes."""
    return build_intervals(created_at, created_status, changes)


def _closed_set(closed_statuses: Iterable[str]) -> set[str]:
    return {s for s in closed_statuses}


def close_events(intervals: list[Interval], closed_statuses: Iterable[str]) -> list[datetime]:
    """One timestamp per *entry* into a closed status (counts each close).

    A status interval whose value is in the closed set contributes its ``valid_from``.
    Consecutive closed intervals (rare) each count, matching "count each close".
    """
    closed = _closed_set(closed_statuses)
    return [iv.valid_from for iv in intervals if iv.value in closed]


def first_close_at(intervals: list[Interval], closed_statuses: Iterable[str]) -> datetime | None:
    events = close_events(intervals, closed_statuses)
    return min(events) if events else None


def triage_exit(intervals: list[Interval], untriaged_status: str) -> datetime | None:
    """When the ticket first *left* ``untriaged_status`` after creation.

    Returns the ``valid_to`` of the initial status spell when the ticket was
    created in ``untriaged_status``; ``None`` if it was created in another status or
    has never left ``untriaged_status`` (still open). "Time to triage" pairs this
    with the creation time.
    """
    if not intervals or intervals[0].value != untriaged_status:
        return None
    return intervals[0].valid_to


def completed_spells(intervals: list[Interval], status: str) -> list[Interval]:
    """Every *ended* span (``valid_to`` set) during which the ticket held ``status``.

    Each entry-then-exit of the status is one spell; an open (current) spell is
    excluded because its duration is not yet known.
    """
    return [iv for iv in intervals if iv.value == status and iv.valid_to is not None]


def is_open_at(
    intervals: list[Interval],
    t: datetime,
    closed_statuses: Iterable[str],
    created_at: datetime,
) -> bool:
    """True if the ticket exists at ``t`` and is not in a closed status as of ``t``."""
    if t < created_at:
        return False
    closed = _closed_set(closed_statuses)
    status = value_at(intervals, t)
    return status not in closed
