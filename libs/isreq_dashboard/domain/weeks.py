"""Inception-relative week numbering (FR-011, research R-006).

week(t) = floor((t - anchor) / 7 days) + 1, with week 1 being the anchor week.
Timestamps before the anchor land in a labelled pre-inception bucket (week <= 0)
rather than being silently merged into week 1.

Pure logic: no database, no Jira, no third-party deps.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

PRE_INCEPTION_LABEL = "Pre-inception"


def _as_utc(t: datetime) -> datetime:
    """Treat naive datetimes as UTC; normalise aware ones to UTC."""
    if t.tzinfo is None:
        return t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def _anchor_dt(anchor: date) -> datetime:
    """Anchor as midnight UTC of the configured week-1 date."""
    return datetime(anchor.year, anchor.month, anchor.day, tzinfo=timezone.utc)


def week_of(t: datetime, anchor: date) -> int:
    """Sequential week number relative to ``anchor``.

    The anchor week (days 0-6 from the anchor) is week 1. Anything strictly
    before the anchor week yields week <= 0 (pre-inception).
    """
    delta_days = (_as_utc(t) - _anchor_dt(anchor)).total_seconds() / 86400.0
    return math.floor(delta_days / 7.0) + 1


def is_pre_inception(week: int) -> bool:
    return week <= 0


def week_label(week: int) -> str:
    return PRE_INCEPTION_LABEL if is_pre_inception(week) else f"W{week:02d}"


def week_end_utc(anchor: date, week: int) -> datetime:
    """End boundary (exclusive) of ``week`` = start of the following week, in UTC.

    Used as the point-in-time for end-of-week backlog (FR-016).
    """
    return _anchor_dt(anchor) + timedelta(weeks=week)


def period_key(t: datetime, anchor: date) -> str:
    """Stable string key for grouping by week (pre-inception collapses to one bucket)."""
    w = week_of(t, anchor)
    return PRE_INCEPTION_LABEL if is_pre_inception(w) else f"W{w:02d}"


# --- Pulse calendar ---------------------------------------------------------
# Pulses are uniform 2-week blocks aligned to the week grid. Reference point
# (config.toml, verified from the data): "IS Pulse 2026#09" spans W12-W13. This
# lets an event's pulse be derived from its week without storing sprint dates.
PULSE_LEN_WEEKS = 2
_PULSE_REF_NUMBER = 9
_PULSE_REF_START_WEEK = 12


def pulse_number_for_week(week: int) -> int | None:
    """Pulse number whose 2-week window contains ``week`` (``None`` pre-inception).

    Anchored on pulse 9 = weeks 12-13, two weeks per pulse::

        P(w) = 9 + floor((w - 12) / 2)
    """
    if is_pre_inception(week):
        return None
    return _PULSE_REF_NUMBER + math.floor((week - _PULSE_REF_START_WEEK) / PULSE_LEN_WEEKS)


def pulse_number_at(t: datetime, anchor: date) -> int | None:
    """Pulse number active at instant ``t`` (``None`` if pre-inception)."""
    return pulse_number_for_week(week_of(t, anchor))


def pulse_window(n: int) -> tuple[int, int]:
    """``(first_week, last_week)`` of pulse ``n`` on the 2-week grid.

    Pulse n spans weeks ``[12 + 2(n-9), … +1]`` — e.g. pulse 9 = W12–W13.
    """
    start = _PULSE_REF_START_WEEK + (n - _PULSE_REF_NUMBER) * PULSE_LEN_WEEKS
    return start, start + PULSE_LEN_WEEKS - 1
