"""Insights — health metrics from the issue #12 idea-set, plus deprioritized Highest.

Every function excludes pre-inception tickets (via ``load_scoped_issues`` / an anchor
filter) and never attributes effort per person (Art. VI). Point-in-time priority comes
from reconstructed intervals (Art. VII).
"""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import func, select

from isreq_dashboard.db.models import Changelog, Issue, Worklog
from isreq_dashboard.domain import priority as prio
from isreq_dashboard.domain import status as stmod
from isreq_dashboard.domain.intervals import value_at
from isreq_dashboard.domain.regions import region_from_timestamp
from isreq_dashboard.metrics.base import (
    PR_MP_INCLUDED,
    SCOPE_ALL,
    MetricConfig,
    Selector,
    _ever_highest_keys,
    anchor_datetime,
    event_period,
    learn_pulse_naming,
    load_priority_intervals,
    load_scoped_issues,
    load_status_intervals,
)

PRIORITY_ORDER = ["Highest", "High", "Medium", "Low", "Lowest"]
AGE_BUCKETS = [("<1w", 0, 7), ("1–2w", 7, 14), ("2–4w", 14, 28), (">4w", 28, 10**9)]


def _all() -> Selector:
    return Selector(scope=SCOPE_ALL, pr_mp=PR_MP_INCLUDED)


def _current_status(spans, fallback):
    return spans[-1].value if spans else fallback


# 1) Aging of open tickets ---------------------------------------------------
def aging_buckets(session, cfg: MetricConfig, now: datetime | None = None) -> pd.DataFrame:
    """Open tickets bucketed by age since creation × current priority — ``[bucket, priority, count]``."""
    now = now or datetime.now(timezone.utc)
    closed = set(cfg.closed_statuses)
    rows = []
    for i in session.scalars(
        select(Issue).where(Issue.created_at >= anchor_datetime(cfg.anchor))
    ):
        if (i.current_status or "") in closed:
            continue
        age = (now - i.created_at).total_seconds() / 86400
        bucket = next(b for b, lo, hi in AGE_BUCKETS if lo <= age < hi)
        rows.append({"bucket": bucket, "priority": i.current_priority or "Backlog"})
    if not rows:
        return pd.DataFrame(columns=["bucket", "priority", "count"])
    return pd.DataFrame(rows).groupby(["bucket", "priority"]).size().reset_index(name="count")


# 2) Reopen rate -------------------------------------------------------------
def _reopened(intervals, closed: set[str]) -> bool:
    return any(
        iv.value in closed and nxt.value not in closed
        for iv, nxt in zip(intervals, intervals[1:])
    )


def reopen_stats(session, cfg: MetricConfig) -> dict:
    """How often a closed ticket is reopened: closed-then-back-to-open transitions."""
    issues = load_scoped_issues(session, cfg, _all())
    sti = load_status_intervals(session, set(issues))
    closed = set(cfg.closed_statuses)
    closed_n = reopened_n = 0
    for key in issues:
        ivs = sti.get(key, [])
        if stmod.close_events(ivs, cfg.closed_statuses):
            closed_n += 1
            if _reopened(ivs, closed):
                reopened_n += 1
    return {
        "closed_tickets": closed_n,
        "reopened_tickets": reopened_n,
        "reopen_pct": round(100 * reopened_n / closed_n, 1) if closed_n else 0.0,
    }


# 3) Worklog coverage --------------------------------------------------------
def worklog_coverage(session, cfg: MetricConfig, sel: Selector) -> pd.DataFrame:
    """Per period: share of resolved tickets that have *any* worklog — the honesty gauge
    for every effort/forecast number. ``[period, closed, with_worklog, coverage_pct]``."""
    issues = load_scoped_issues(session, cfg, sel)
    sti = load_status_intervals(session, set(issues))
    have_wl = set(
        session.scalars(
            select(Worklog.issue_key).where(Worklog.issue_key.in_(set(issues))).distinct()
        )
    )
    naming = learn_pulse_naming(issues.values())
    agg: dict[str, dict[str, int]] = {}
    for key, i in issues.items():
        ce = stmod.close_events(sti.get(key, []), cfg.closed_statuses)
        if not ce:
            continue
        period = event_period(sel.cadence, max(ce), cfg.anchor, naming)
        rec = agg.setdefault(period, {"closed": 0, "with_worklog": 0})
        rec["closed"] += 1
        if key in have_wl:
            rec["with_worklog"] += 1
    if not agg:
        return pd.DataFrame(columns=["period", "closed", "with_worklog", "coverage_pct"])
    rows = [
        {
            "period": p,
            "closed": v["closed"],
            "with_worklog": v["with_worklog"],
            "coverage_pct": round(100 * v["with_worklog"] / v["closed"], 1) if v["closed"] else 0.0,
        }
        for p, v in agg.items()
    ]
    return pd.DataFrame(rows).sort_values("period").reset_index(drop=True)


# 4) Priority escalation -----------------------------------------------------
def escalation_breakdown(session, cfg: MetricConfig) -> dict:
    """Among ever-Highest tickets: born-Highest vs escalated-later, + mean days to escalate."""
    issues = load_scoped_issues(session, cfg, _all())
    keys = {k for k in _ever_highest_keys(session, cfg) if k in issues}
    pri = load_priority_intervals(session, keys)
    target = cfg.highest_priority_name
    born = escalated = 0
    ttl: list[float] = []
    for key in keys:
        ivs = pri.get(key, [])
        entries = prio.entries_into(ivs, target)
        if not entries:
            continue
        if ivs and ivs[0].value == target:
            born += 1
        else:
            escalated += 1
            ttl.append((entries[0] - issues[key].created_at).total_seconds() / 86400)
    return {
        "born_highest": born,
        "escalated_to_highest": escalated,
        "mean_days_to_escalation": round(sum(ttl) / len(ttl), 2) if ttl else None,
        "n_escalated_timed": len(ttl),
    }


# 5) Deprioritized Highest ---------------------------------------------------
def deprioritized_highest(session, cfg: MetricConfig) -> list[dict]:
    """Tickets whose Highest priority was **dropped to a lower priority** (not by closing).

    Uses reconstructed priority intervals (Art. VII): each ``drop_below_exits`` from
    Highest is a deprioritization. Returns key/title/assignee, when it was last
    deprioritized, and the priority it dropped to — newest first.
    """
    issues = load_scoped_issues(session, cfg, _all())
    pri = load_priority_intervals(session, set(issues))
    target = cfg.highest_priority_name
    cur_pri = dict(
        session.execute(
            select(Issue.key, Issue.current_priority).where(Issue.key.in_(set(issues)))
        ).all()
    )
    rows = []
    for key, i in issues.items():
        ivs = pri.get(key, [])
        drops = prio.drop_below_exits(ivs, target)
        if not drops:
            continue
        last = max(drops)
        rows.append(
            {
                "key": key,
                "title": i.title,
                "assignee_name": i.assignee_name,
                "deprioritized_at": last,
                "dropped_to": value_at(ivs, last) or "—",
                "current_priority": cur_pri.get(key) or "—",
                "current_status": i.current_status,
            }
        )
    rows.sort(key=lambda r: r["deprioritized_at"], reverse=True)
    return rows


# 6) Intake seasonality ------------------------------------------------------
def intake_seasonality(session, cfg: MetricConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Creations by UTC hour-of-day and by day-of-week — ``(hours_df, dow_df)``."""
    issues = load_scoped_issues(session, cfg, _all())
    by_hour: Counter = Counter()
    by_dow: Counter = Counter()
    for i in issues.values():
        t = i.created_at.astimezone(timezone.utc)
        by_hour[t.hour] += 1
        by_dow[t.weekday()] += 1
    hours = pd.DataFrame({"hour": list(range(24)), "count": [by_hour.get(h, 0) for h in range(24)]})
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow = pd.DataFrame({"day": days, "count": [by_dow.get(d, 0) for d in range(7)]})
    return hours, dow


# 7) Open-ticket load by creation-time-of-day region (counts only) -----------
def region_load(session, cfg: MetricConfig) -> pd.DataFrame:
    """Open tickets by the **region of the hour they were created** (creation time-of-day,
    same derivation as Intake's region breakdown) — ``[region, open_tickets]``. Counts only."""
    issues = load_scoped_issues(session, cfg, _all())
    sti = load_status_intervals(session, set(issues))
    closed = set(cfg.closed_statuses)
    counts: Counter = Counter()
    for key, i in issues.items():
        if _current_status(sti.get(key, []), i.current_status) in closed:
            continue
        region = (
            region_from_timestamp(i.created_at, cfg.region_windows)
            if cfg.region_windows
            else "Backlog"
        )
        counts["Backlog" if region == "Unknown" else region] += 1
    if not counts:
        return pd.DataFrame(columns=["region", "open_tickets"])
    return (
        pd.DataFrame({"region": list(counts), "open_tickets": list(counts.values())})
        .sort_values("open_tickets", ascending=False)
        .reset_index(drop=True)
    )


# 8) Stale / zombie tickets --------------------------------------------------
def stale_tickets(session, cfg: MetricConfig, *, days: int = 14, now: datetime | None = None) -> list[dict]:
    """Open tickets with **no activity** (no status/priority change, no worklog) for ≥
    ``days`` (issue #27). Last activity = max(created, last changelog, last worklog)."""
    now = now or datetime.now(timezone.utc)
    issues = load_scoped_issues(session, cfg, _all())
    sti = load_status_intervals(session, set(issues))
    closed = set(cfg.closed_statuses)
    keys = set(issues)
    last_cl = dict(session.execute(
        select(Changelog.issue_key, func.max(Changelog.changed_at))
        .where(Changelog.issue_key.in_(keys)).group_by(Changelog.issue_key)).all()) if keys else {}
    last_wl = dict(session.execute(
        select(Worklog.issue_key, func.max(Worklog.started_at))
        .where(Worklog.issue_key.in_(keys)).group_by(Worklog.issue_key)).all()) if keys else {}
    rows = []
    for key, i in issues.items():
        if _current_status(sti.get(key, []), i.current_status) in closed:
            continue
        last = max(t for t in (i.created_at, last_cl.get(key), last_wl.get(key)) if t is not None)
        idle = (now - last).total_seconds() / 86400
        if idle >= days:
            rows.append({"key": key, "title": i.title, "assignee_name": i.assignee_name,
                         "current_status": i.current_status, "area": i.area,
                         "last_activity": last, "days_idle": round(idle, 1)})
    rows.sort(key=lambda r: r["days_idle"], reverse=True)
    return rows


# 9) Blocked analysis --------------------------------------------------------
def blocked_analysis(session, cfg: MetricConfig, status: str = "BLOCKED") -> dict:
    """Time tickets spend BLOCKED (issue #31): currently-blocked count, mean spell length,
    and the longest-blocked tickets."""
    issues = load_scoped_issues(session, cfg, _all())
    sti = load_status_intervals(session, set(issues))
    currently = 0
    spell_days: list[float] = []
    tickets = []
    for key, i in issues.items():
        spans = sti.get(key, [])
        cur = _current_status(spans, i.current_status)
        spells = stmod.completed_spells(spans, status)
        total = sum((iv.valid_to - iv.valid_from).total_seconds() for iv in spells)
        spell_days.extend((iv.valid_to - iv.valid_from).total_seconds() / 86400 for iv in spells)
        if cur == status:
            currently += 1
        if total > 0 or cur == status:
            tickets.append({"key": key, "title": i.title, "assignee_name": i.assignee_name,
                            "blocked_days": round(total / 86400, 1),
                            "currently_blocked": cur == status, "area": i.area})
    tickets.sort(key=lambda r: r["blocked_days"], reverse=True)
    return {"currently_blocked": currently, "n_spells": len(spell_days),
            "mean_blocked_days": round(statistics.fmean(spell_days), 1) if spell_days else None,
            "tickets": tickets}


# 10) Rejection rate ---------------------------------------------------------
def rejection_rate(session, cfg: MetricConfig, rejected_status: str = "Rejected") -> dict:
    """Share of resolved tickets ending **Rejected** vs Done/Closed (issue #32), overall and
    by area — a signal of misrouted intake / triage quality."""
    closed = set(cfg.closed_statuses)
    overall_rej = overall_tot = 0
    by_area: dict[str, dict[str, int]] = {}
    for i in session.scalars(select(Issue).where(Issue.created_at >= anchor_datetime(cfg.anchor))):
        if (i.current_status or "") not in closed:
            continue
        overall_tot += 1
        rej = i.current_status == rejected_status
        overall_rej += rej
        d = by_area.setdefault(i.area or "Backlog", {"rejected": 0, "resolved": 0})
        d["resolved"] += 1
        d["rejected"] += rej
    df = pd.DataFrame([
        {"area": a, "rejected": d["rejected"], "resolved": d["resolved"],
         "rejection_pct": round(100 * d["rejected"] / d["resolved"], 1) if d["resolved"] else 0.0}
        for a, d in by_area.items()
    ])
    if not df.empty:
        df = df.sort_values("resolved", ascending=False).reset_index(drop=True)
    return {"rejected": overall_rej, "resolved": overall_tot,
            "rejection_pct": round(100 * overall_rej / overall_tot, 1) if overall_tot else 0.0,
            "by_area": df}


# 11) Status churn (ping-pong) ----------------------------------------------
def status_churn(session, cfg: MetricConfig, *, min_transitions: int = 6) -> list[dict]:
    """Tickets with many **status transitions** (issue #33) — process friction / rework."""
    issues = load_scoped_issues(session, cfg, _all())
    counts = dict(session.execute(
        select(Changelog.issue_key, func.count())
        .where(Changelog.issue_key.in_(set(issues)), Changelog.field == "status")
        .group_by(Changelog.issue_key)).all())
    rows = []
    for key, n in counts.items():
        if int(n) >= min_transitions and key in issues:
            i = issues[key]
            rows.append({"key": key, "title": i.title, "assignee_name": i.assignee_name,
                         "transitions": int(n), "current_status": i.current_status, "area": i.area})
    rows.sort(key=lambda r: r["transitions"], reverse=True)
    return rows


# 12) Effort Pareto (80/20) --------------------------------------------------
def effort_pareto(session, cfg: MetricConfig, group: str = "sub_area") -> pd.DataFrame:
    """Worklog hours by group, sorted desc with cumulative % (issue #36) — the 80/20 of
    where effort goes. ``[group, hours, cum_pct, keys]`` (``keys`` = the contributing
    tickets, so a bar can open its exact tickets in Jira)."""
    issues = load_scoped_issues(session, cfg, _all())
    spent = dict(session.execute(
        select(Worklog.issue_key, func.sum(Worklog.time_spent_seconds))
        .where(Worklog.issue_key.in_(set(issues))).group_by(Worklog.issue_key)).all())
    g_hours: dict[str, float] = {}
    g_keys: dict[str, list[str]] = {}
    for key, i in issues.items():
        h = (spent.get(key, 0) or 0) / 3600
        if h <= 0:
            continue
        if group == "sub_area":
            g = f"{i.area or 'Backlog'} ▸ {i.sub_area or 'Backlog'}"
        elif group == "area":
            g = i.area or "Backlog"
        else:
            g = key
        g_hours[g] = g_hours.get(g, 0.0) + h
        g_keys.setdefault(g, []).append(key)
    if not g_hours:
        return pd.DataFrame(columns=["group", "hours", "cum_pct", "keys"])
    df = (pd.DataFrame([{"group": g, "hours": round(h, 1), "keys": g_keys[g]}
                        for g, h in g_hours.items()])
          .sort_values("hours", ascending=False).reset_index(drop=True))
    total = df["hours"].sum()
    df["cum_pct"] = (df["hours"].cumsum() / total * 100).round(1) if total else 0.0
    return df


# 13) HR-automation workload -------------------------------------------------
def hr_automation_summary(session, cfg: MetricConfig, marker: str = "HR Automation") -> dict:
    """Volume/effort of the auto-generated HR onboarding/offboarding tickets (issue #35) —
    an automation-ROI candidate (these are excluded from the Data-Quality policy table)."""
    mk = marker.lower()
    issues = [i for i in session.scalars(
        select(Issue).where(Issue.created_at >= anchor_datetime(cfg.anchor)))
        if i.title and mk in i.title.lower()]
    keys = {i.key for i in issues}
    spent = dict(session.execute(
        select(Worklog.issue_key, func.sum(Worklog.time_spent_seconds))
        .where(Worklog.issue_key.in_(keys)).group_by(Worklog.issue_key)).all()) if keys else {}
    closed = set(cfg.closed_statuses)
    open_n = sum(1 for i in issues if (i.current_status or "") not in closed)
    anchor_dt = anchor_datetime(cfg.anchor)
    last = max([i.created_at for i in issues], default=anchor_dt)
    span_weeks = max((last - anchor_dt).total_seconds() / (7 * 86400), 1e-9)
    return {"total": len(issues), "open": open_n, "resolved": len(issues) - open_n,
            "hours": round(sum(spent.values()) / 3600, 1),
            "created_per_week": round(len(issues) / span_weeks, 1)}
