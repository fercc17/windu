"""Drill-down queries (US2; FR-019/020/021).

Every aggregate has a paired drill-down returning the underlying issues with
``key``, ``title``, ``assignee_name``. The two period predicates are physically
distinct and never conflated:
  - created-in-period  -> ``period_for(created_at)``
  - closed-in-period   -> close events' ``period_for(closed_at)`` (independent of created_at)

Row counts equal their aggregates (I-2): one row per matching *event*.
"""

from __future__ import annotations

from datetime import datetime

from isreq_dashboard.domain import priority as prio
from isreq_dashboard.domain import status as st
from isreq_dashboard.domain.intervals import value_at
from isreq_dashboard.metrics.base import (
    MetricConfig,
    Selector,
    load_priority_intervals,
    load_scoped_issues,
    load_status_intervals,
    period_for,
)


def _row(issue) -> dict:
    return {"key": issue.key, "title": issue.title, "assignee_name": issue.assignee_name}


def created_in_period(session, cfg: MetricConfig, sel: Selector, period: str) -> list[dict]:
    issues = load_scoped_issues(session, cfg, sel)
    return [
        _row(i)
        for i in issues.values()
        if period_for(sel.cadence, i, i.created_at, cfg.anchor) == period
    ]


def closed_in_period(session, cfg: MetricConfig, sel: Selector, period: str) -> list[dict]:
    issues = load_scoped_issues(session, cfg, sel)
    sti = load_status_intervals(session, set(issues))
    out: list[dict] = []
    for key, issue in issues.items():
        for t in st.close_events(sti.get(key, []), cfg.closed_statuses):
            if period_for(sel.cadence, issue, t, cfg.anchor) == period:
                out.append(_row(issue))  # one row per close event (matches throughput count)
    return out


def became_highest_in_period(session, cfg: MetricConfig, sel: Selector, period: str) -> list[dict]:
    issues = load_scoped_issues(session, cfg, sel)
    pri = load_priority_intervals(session, set(issues))
    out: list[dict] = []
    for key, issue in issues.items():
        for t in prio.entries_into(pri.get(key, []), cfg.highest_priority_name):
            if period_for(sel.cadence, issue, t, cfg.anchor) == period:
                out.append(_row(issue))
    return out


def open_at(session, cfg: MetricConfig, sel: Selector, t: datetime) -> list[dict]:
    issues = load_scoped_issues(session, cfg, sel)
    sti = load_status_intervals(session, set(issues))
    return [
        _row(i)
        for key, i in issues.items()
        if st.is_open_at(sti.get(key, []), t, cfg.closed_statuses, i.created_at)
    ]


def highest_open_at(session, cfg: MetricConfig, sel: Selector, t: datetime) -> list[dict]:
    """Issues that are Highest (interval covering t) AND open at t."""
    issues = load_scoped_issues(session, cfg, sel)
    pri = load_priority_intervals(session, set(issues))
    sti = load_status_intervals(session, set(issues))
    out: list[dict] = []
    for key, i in issues.items():
        if (
            value_at(pri.get(key, []), t) == cfg.highest_priority_name
            and st.is_open_at(sti.get(key, []), t, cfg.closed_statuses, i.created_at)
        ):
            out.append(_row(i))
    return out
