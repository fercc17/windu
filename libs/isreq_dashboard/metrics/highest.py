"""M1 — Highest create-vs-close, the north star (FR-006/007/009, Art. I).

- ``became_highest``  : each entry event into Highest (created-at-Highest OR raised),
                        counted in the period of the transition. Multiple entries for
                        one issue each count (clarified 2026-06-12).
- ``highest_closed``  : close events where the issue was Highest at the moment of close.
- ``highest_exits``   : a Highest interval that ended by dropping below Highest OR by
                        closing while Highest.
- ``highest_backlog`` : depends on cadence —
    * weekly  : point-in-time count of Highest-and-open tickets at each week end (from
                the intervals — correct under reopens; a running became−exits tally would
                undercount tickets closed-while-Highest then reopened).
    * per-pulse: a SNAPSHOT — the count of currently Highest-and-open tickets grouped by
                their (latest) sprint. Sprints are not a temporal sequence, so a running
                cumulative would be meaningless (it can go negative); the snapshot is the
                honest per-sprint view and sums to the live open-Highest total.

All point-in-time questions read reconstructed intervals, never current priority (Art. VII).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from isreq_dashboard.domain import priority as prio
from isreq_dashboard.domain import status as st
from isreq_dashboard.domain import weeks
from isreq_dashboard.domain.intervals import value_at
from isreq_dashboard.metrics.base import (
    PER_PULSE,
    MetricConfig,
    Selector,
    event_period,
    learn_pulse_naming,
    load_priority_intervals,
    load_scoped_issues,
    load_status_intervals,
)


@dataclass(frozen=True)
class HighestEvent:
    period: str
    issue_key: str
    kind: str  # "became" | "closed" | "exit"


def _load(session, cfg: MetricConfig, sel: Selector):
    issues = load_scoped_issues(session, cfg, sel)
    keys = set(issues)
    return issues, load_priority_intervals(session, keys), load_status_intervals(session, keys)


def _events(issues, pri, sti, cfg: MetricConfig, sel: Selector) -> list[HighestEvent]:
    target = cfg.highest_priority_name
    naming = learn_pulse_naming(issues.values())
    events: list[HighestEvent] = []
    for key, issue in issues.items():
        p_ivs = pri.get(key, [])
        s_ivs = sti.get(key, [])

        # per-pulse buckets by the event-time pulse window of each event (issue #14)
        for t in prio.entries_into(p_ivs, target):
            events.append(HighestEvent(event_period(sel.cadence, t, cfg.anchor, naming), key, "became"))

        # close-while-Highest: a close event whose priority at that instant is Highest
        for t in st.close_events(s_ivs, cfg.closed_statuses):
            if value_at(p_ivs, t) == target:
                period = event_period(sel.cadence, t, cfg.anchor, naming)
                events.append(HighestEvent(period, key, "closed"))
                events.append(HighestEvent(period, key, "exit"))

        # drop-below exits (priority left Highest by a priority change, not a close)
        for t in prio.drop_below_exits(p_ivs, target):
            events.append(HighestEvent(event_period(sel.cadence, t, cfg.anchor, naming), key, "exit"))

    return events


def highest_events(session, cfg: MetricConfig, sel: Selector) -> list[HighestEvent]:
    """Flat list of Highest entry/close/exit events tagged with their period."""
    issues, pri, sti = _load(session, cfg, sel)
    return _events(issues, pri, sti, cfg, sel)


def _live_highest_by_pulse(issues, pri, sti, cfg: MetricConfig) -> dict[str, int]:
    """Currently Highest-and-open tickets, grouped by the pulse they were created in.

    Grouped by the event-time pulse window of the creation date (issue #14), consistent
    with the became/closed bars, rather than the sparsely-populated sprint field.
    """
    closed = set(cfg.closed_statuses)
    target = cfg.highest_priority_name
    naming = learn_pulse_naming(issues.values())
    out: dict[str, int] = {}
    for key, issue in issues.items():
        p_ivs = pri.get(key, [])
        s_ivs = sti.get(key, [])
        cur_priority = p_ivs[-1].value if p_ivs else None
        cur_status = s_ivs[-1].value if s_ivs else None
        if cur_priority == target and cur_status not in closed:
            pulse = event_period(PER_PULSE, issue.created_at, cfg.anchor, naming)
            out[pulse] = out.get(pulse, 0) + 1
    return out


def _count_highest_open_at(issues, pri, sti, cfg: MetricConfig, t) -> int:
    """Point-in-time count of tickets that are Highest AND open as of ``t``."""
    target = cfg.highest_priority_name
    n = 0
    for key, issue in issues.items():
        if value_at(pri.get(key, []), t) == target and st.is_open_at(
            sti.get(key, []), t, cfg.closed_statuses, issue.created_at
        ):
            n += 1
    return n


def _week_backlog(period: str, issues, pri, sti, cfg: MetricConfig) -> int:
    w = int(period[1:]) if period.startswith("W") and period[1:].isdigit() else 0
    return _count_highest_open_at(issues, pri, sti, cfg, weeks.week_end_utc(cfg.anchor, w))


def highest_series(session, cfg: MetricConfig, sel: Selector) -> pd.DataFrame:
    """Per-period north-star frame: became_highest, highest_closed, highest_exits, backlog."""
    issues, pri, sti = _load(session, cfg, sel)
    events = _events(issues, pri, sti, cfg, sel)
    periods = sorted({e.period for e in events})
    frame = pd.DataFrame(
        {
            "period": periods,
            "became_highest": [sum(e.period == p and e.kind == "became" for e in events) for p in periods],
            "highest_closed": [sum(e.period == p and e.kind == "closed" for e in events) for p in periods],
            "highest_exits": [sum(e.period == p and e.kind == "exit" for e in events) for p in periods],
        }
    )

    if sel.cadence == PER_PULSE:
        # snapshot by sprint (non-cumulative; cumulative is meaningless across sprints)
        live = _live_highest_by_pulse(issues, pri, sti, cfg)
        frame["highest_backlog"] = frame["period"].map(lambda p: live.get(p, 0)).astype(int)
    else:
        # weekly: true point-in-time open-Highest at each week end (reopen-correct)
        frame["highest_backlog"] = frame["period"].map(
            lambda p: _week_backlog(p, issues, pri, sti, cfg)
        ).astype(int)

    return frame
