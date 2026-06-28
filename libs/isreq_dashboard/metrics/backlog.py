"""M4 — Backlog (FR-016): issues created on/before a point and not in a closed
status as of that point, computed at query time from status intervals (reopen-aware).

Weekly cadence evaluates backlog at the end boundary of each week (the natural
point-in-time). Per-pulse cadence is best-effort: it evaluates backlog at the latest
activity instant within each pulse (pulses are not strictly time-ordered).

``group=None`` -> ``[period, backlog]``; with a group (area / sub_area) ->
``[period, group, backlog]`` (open issues at each point, bucketed by group).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from isreq_dashboard.domain import status as st
from isreq_dashboard.domain import weeks
from isreq_dashboard.domain.intervals import value_at
from isreq_dashboard.metrics.base import (
    PER_PULSE,
    MetricConfig,
    Selector,
    event_period,
    group_value,
    learn_pulse_naming,
    load_scoped_issues,
    load_status_intervals,
)


def _period_points(issues, sti, cfg: MetricConfig, sel: Selector) -> list[tuple[str, datetime]]:
    """Ordered ``(period_label, point-in-time T)`` pairs at which to evaluate backlog."""
    # weeks spanned by any event (creation or close)
    max_week = 1
    for key, i in issues.items():
        max_week = max(max_week, weeks.week_of(i.created_at, cfg.anchor))
        for t in st.close_events(sti.get(key, []), cfg.closed_statuses):
            max_week = max(max_week, weeks.week_of(t, cfg.anchor))

    if sel.cadence == PER_PULSE:
        # Evaluate backlog at the end of each calendar pulse window present (issue #14):
        # buckets by the event-time pulse, not the sparse sprint field, so every pulse shows.
        naming = learn_pulse_naming(issues.values())
        last_week_by_pulse: dict[int, int] = {}
        for w in range(1, max_week + 1):
            n = weeks.pulse_number_for_week(w)
            if n is not None:
                last_week_by_pulse[n] = max(last_week_by_pulse.get(n, w), w)
        points = []
        for _n, last_w in sorted(last_week_by_pulse.items()):
            interior = weeks.week_end_utc(cfg.anchor, last_w - 1)  # an instant inside this pulse
            label = event_period(PER_PULSE, interior, cfg.anchor, naming)
            points.append((label, weeks.week_end_utc(cfg.anchor, last_w)))
        return points

    return [(f"W{w:02d}", weeks.week_end_utc(cfg.anchor, w)) for w in range(1, max_week + 1)]


def _open_issues_at(issues, sti, cfg: MetricConfig, t: datetime):
    return [
        i
        for key, i in issues.items()
        if st.is_open_at(sti.get(key, []), t, cfg.closed_statuses, i.created_at)
    ]


def backlog_series(
    session, cfg: MetricConfig, sel: Selector, group: str | None = None
) -> pd.DataFrame:
    """Per-period open count, optionally broken down by area / sub_area."""
    issues = load_scoped_issues(session, cfg, sel)
    sti = load_status_intervals(session, set(issues))
    cols = ["period", "backlog"] if group is None else ["period", "group", "backlog"]
    if not issues:
        return pd.DataFrame(columns=cols)

    points = _period_points(issues, sti, cfg, sel)

    if group is None:
        rows = [{"period": p, "backlog": len(_open_issues_at(issues, sti, cfg, t))} for p, t in points]
        return pd.DataFrame(rows)

    rows = []
    for p, t in points:
        counts: dict[str, int] = {}
        for i in _open_issues_at(issues, sti, cfg, t):
            g = group_value(group, i, cfg)
            counts[g] = counts.get(g, 0) + 1
        rows.extend({"period": p, "group": g, "backlog": n} for g, n in sorted(counts.items()))
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=cols)


def carryover_series(session, cfg: MetricConfig, sel: Selector) -> pd.DataFrame:
    """*Carryover cohort* (issue #9): tickets **created in a period** that were still open
    at the **end of that period**, i.e. not completed within it and so carried into the next.

    - Weekly: created in week W, still open at the end of W → carried into W+1.
    - Per-pulse: created in pulse P (event-time), still open at the end of P's 2-week
      window → carried into P+1.

    Distinct from :func:`backlog_series`, which is the point-in-time open *stock* (every
    unclosed ticket regardless of when it was created). Returns
    ``[period, created, carried_over, carried_pct]``.
    """
    issues = load_scoped_issues(session, cfg, sel)
    sti = load_status_intervals(session, set(issues))
    per_pulse = sel.cadence == PER_PULSE
    naming = learn_pulse_naming(issues.values()) if per_pulse else None
    agg: dict[str, dict[str, int]] = {}
    for key, i in issues.items():
        if per_pulse:
            n = weeks.pulse_number_at(i.created_at, cfg.anchor)
            if n is None:
                continue  # pre-inception
            period = event_period(PER_PULSE, i.created_at, cfg.anchor, naming)
            t_end = weeks.week_end_utc(cfg.anchor, weeks.pulse_window(n)[1])
        else:
            period = weeks.period_key(i.created_at, cfg.anchor)
            t_end = weeks.week_end_utc(cfg.anchor, weeks.week_of(i.created_at, cfg.anchor))
        rec = agg.setdefault(period, {"created": 0, "carried_over": 0})
        rec["created"] += 1
        if st.is_open_at(sti.get(key, []), t_end, cfg.closed_statuses, i.created_at):
            rec["carried_over"] += 1
    if not agg:
        return pd.DataFrame(columns=["period", "created", "carried_over", "carried_pct"])
    rows = [
        {
            "period": p,
            "created": v["created"],
            "carried_over": v["carried_over"],
            "carried_pct": round(100.0 * v["carried_over"] / v["created"], 1) if v["created"] else 0.0,
        }
        for p, v in agg.items()
    ]
    return pd.DataFrame(rows).sort_values("period").reset_index(drop=True)


def carryover_streaks(
    session, cfg: MetricConfig, sel: Selector, min_pulses: int = 2, now: datetime | None = None
) -> list[dict]:
    """Chronic spillover (issue #12): tickets that lived across ≥ ``min_pulses`` pulses —
    open from their creation pulse through that many later pulse boundaries.

    ``pulses_carried`` = (resolution pulse, or the current pulse if still open) − creation
    pulse. Returns key/created_pulse/pulses_carried/current_status/resolved, longest first.
    """
    now = now or datetime.now(timezone.utc)
    issues = load_scoped_issues(session, cfg, sel)
    sti = load_status_intervals(session, set(issues))
    naming = learn_pulse_naming(issues.values())
    rows = []
    for key, i in issues.items():
        created_pulse = weeks.pulse_number_at(i.created_at, cfg.anchor)
        if created_pulse is None:
            continue
        ce = st.close_events(sti.get(key, []), cfg.closed_statuses)
        end_pulse = weeks.pulse_number_at(max(ce) if ce else now, cfg.anchor)
        if end_pulse is None:
            continue
        streak = end_pulse - created_pulse
        if streak >= min_pulses:
            rows.append(
                {
                    "key": key,
                    "title": i.title,
                    "assignee_name": i.assignee_name,
                    "created_pulse": event_period(PER_PULSE, i.created_at, cfg.anchor, naming),
                    "pulses_carried": streak,
                    "current_status": i.current_status,
                    "resolved": bool(ce),
                }
            )
    rows.sort(key=lambda r: r["pulses_carried"], reverse=True)
    return rows


def wip_series(session, cfg: MetricConfig, sel: Selector) -> pd.DataFrame:
    """Work-in-progress over time (issue #29): tickets **past triage and not closed**
    (in active flight) at each week-end. ``[period, wip]``. Lead-time ≈ WIP ÷ throughput."""
    issues = load_scoped_issues(session, cfg, sel)
    sti = load_status_intervals(session, set(issues))
    closed = set(cfg.closed_statuses)
    triage = {cfg.untriaged_status, "Triaged"}
    max_week = 1
    for key, i in issues.items():
        max_week = max(max_week, weeks.week_of(i.created_at, cfg.anchor))
        for t in st.close_events(sti.get(key, []), cfg.closed_statuses):
            max_week = max(max_week, weeks.week_of(t, cfg.anchor))
    rows = []
    for w in range(1, max_week + 1):
        t_end = weeks.week_end_utc(cfg.anchor, w)
        wip = 0
        for key, i in issues.items():
            if i.created_at > t_end:
                continue
            s = value_at(sti.get(key, []), t_end)
            if s is not None and s not in closed and s not in triage:
                wip += 1
        rows.append({"period": f"W{w:02d}", "wip": wip})
    return pd.DataFrame(rows)
