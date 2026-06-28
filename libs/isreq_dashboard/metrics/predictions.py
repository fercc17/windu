"""Predictions & forecasts (issue #11).

Lightweight, transparent forecasting on top of the existing, audited metric
definitions — never a black box. Every number is a simple historical average or a
ratio with explicitly stated assumptions, so a reader can reproduce it by hand:

1. :func:`pr_mp_forecast`       — expected PR/MP per period + time invested.
2. :func:`backlog_clear_scenarios` — weeks to clear the open backlog under
   staffing / automation scenarios.
3. :func:`ps5_blocker_stats`    — arrival rate, time-to-first-work, time-to-resolve.
4. :func:`priority_breakdown`   — intake / closes / time-to-close by current priority.
5. :func:`automation_targets`   — area/sub-area ranked by volume × effort.

All time spent is issue-level (worklog sums), never per person (Art. VI / FR-018).
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import func, select

from isreq_dashboard.db.models import Issue, Worklog
from isreq_dashboard.domain import status as status_domain
from isreq_dashboard.metrics.base import (
    PER_PULSE,
    PR_MP_INCLUDED,
    PR_MP_ONLY,
    SCOPE_ALL,
    SCOPE_PS5,
    MetricConfig,
    Selector,
    load_scoped_issues,
    load_status_intervals,
)
from isreq_dashboard.metrics.flow import flow_headline
from isreq_dashboard.metrics.intake import intake_series
from isreq_dashboard.metrics.throughput import _percentile, time_to_close_durations

PRIORITY_ORDER = ["Highest", "High", "Medium", "Low", "Lowest"]


def _per_period_mean(df: pd.DataFrame, value_col: str) -> float:
    if df is None or df.empty:
        return 0.0
    s = df.groupby("period")[value_col].sum()
    return float(s.mean()) if len(s) else 0.0


def _worklog_seconds(session, keys: set[str]) -> dict[str, int]:
    if not keys:
        return {}
    return dict(
        session.execute(
            select(Worklog.issue_key, func.sum(Worklog.time_spent_seconds))
            .where(Worklog.issue_key.in_(keys))
            .group_by(Worklog.issue_key)
        ).all()
    )


# 1) -------------------------------------------------------------------------
def pr_mp_forecast(session, cfg: MetricConfig, cadence: str, scope: str = SCOPE_ALL) -> dict:
    """Expected PR/MP tickets per period and the time they'll consume.

    Forecast = historical mean of PR/MP created per period. Effort/ticket = mean of the
    ticket's logged worklog (issue-level). Forecast hours = count × hours/ticket. ``scope``
    narrows the PR/MP set further (e.g. Highest PR/MP).
    """
    sel = Selector(cadence=cadence, scope=scope, pr_mp=PR_MP_ONLY)
    created = intake_series(session, cfg, sel)
    mean_count = _per_period_mean(created, "count")

    issues = load_scoped_issues(session, cfg, sel)
    spent = _worklog_seconds(session, set(issues))
    per_ticket_hours = [spent[k] / 3600 for k in issues if k in spent]
    mean_hours = statistics.fmean(per_ticket_hours) if per_ticket_hours else 0.0

    ttc = time_to_close_durations(session, cfg, sel)
    mean_ttc_days = (statistics.fmean(ttc) / 86400) if ttc else None

    return {
        "unit": "pulse" if cadence == PER_PULSE else "week",
        "n_periods": int(created["period"].nunique()) if not created.empty else 0,
        "total_pr_mp": len(issues),
        "forecast_count": mean_count,
        "mean_hours_per_ticket": mean_hours,
        "forecast_hours": mean_count * mean_hours,
        "n_with_worklog": len(per_ticket_hours),
        "mean_ttc_days": mean_ttc_days,
    }


# 2) -------------------------------------------------------------------------
def current_backlog(session, cfg: MetricConfig, sel: Selector | None = None) -> int:
    """Open tickets right now (created and not currently closed), within the selection."""
    sel = sel or Selector(scope=SCOPE_ALL, pr_mp=PR_MP_INCLUDED)
    issues = load_scoped_issues(session, cfg, sel)
    sti = load_status_intervals(session, set(issues))
    closed = set(cfg.closed_statuses)
    open_n = 0
    for key, i in issues.items():
        spans = sti.get(key, [])
        current = spans[-1].value if spans else i.current_status
        if current not in closed:
            open_n += 1
    return open_n


def backlog_burndown(session, cfg: MetricConfig, sel: Selector | None = None) -> dict:
    """Project the open backlog forward a **full year** at the current net rate (issue #12),
    within the selection (scope / PR-MP filters), in the cadence's unit.

    Weekly cadence → 52 weekly steps; per-pulse → 26 fortnightly steps (both = 1 year).
    net = closes − intake per step (from whole-history weekly averages). The ``projection``
    frame is ``[step, date, projected_backlog]`` with real **calendar dates** (from today),
    so the chart's x-axis reads "Jun 15" rather than "W5". ``steps_to_zero`` (in ``unit``s)
    is ``None`` when the queue is growing.
    """
    sel = sel or Selector(scope=SCOPE_ALL, pr_mp=PR_MP_INCLUDED)
    head = flow_headline(session, cfg, sel)
    backlog = current_backlog(session, cfg, sel)
    net_per_week = head["closed_per_week"] - head["created_per_week"]

    per_pulse = sel.cadence == PER_PULSE
    unit = "pulse" if per_pulse else "week"
    step_days = 14 if per_pulse else 7
    steps = 26 if per_pulse else 52
    net_per_step = net_per_week * (2 if per_pulse else 1)

    start = datetime.now(timezone.utc).date()
    rows = []
    for i in range(steps + 1):
        projected = backlog - net_per_step * i
        if net_per_step > 0:
            projected = max(projected, 0.0)
        rows.append(
            {"step": i, "date": start + timedelta(days=step_days * i), "projected_backlog": projected}
        )
        if net_per_step > 0 and projected <= 0:
            break

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    steps_to_zero = (backlog / net_per_step) if net_per_step > 0 else None
    return {
        "backlog": backlog,
        "net_per_week": net_per_week,
        "net_per_step": net_per_step,
        "unit": unit,
        "steps_to_zero": round(steps_to_zero, 1) if steps_to_zero is not None else None,
        "projection": df,
    }


def backlog_baseline(session, cfg: MetricConfig, sel: Selector | None = None) -> dict:
    """The DB-derived inputs for the interactive staffing model (issue #13).

    Returns the current open ``backlog`` and whole-history weekly ``intake_per_week`` /
    ``close_per_week`` for the selection. The page turns these into a live what-if with
    pure arithmetic (so every slider responds instantly): per-person weekly throughput =
    ``close_per_week / current head-count``; a scenario's close rate = per-person ×
    scenario head-count; automation removes a share of intake; weeks-to-clear =
    ``backlog / (close − intake)`` (``None`` when the queue still grows).
    """
    sel = sel or Selector(scope=SCOPE_ALL, pr_mp=PR_MP_INCLUDED)
    head = flow_headline(session, cfg, sel)
    return {
        "backlog": current_backlog(session, cfg, sel),
        "intake_per_week": head["created_per_week"],
        "close_per_week": head["closed_per_week"],
    }


# 2b) interactive staffing what-if + hiring recovery (issue #13) -------------
# Pure arithmetic on the DB-derived baseline (backlog + intake/close per week), so the
# SAME numbers drive the Streamlit sliders and the SPA's slider-driven endpoint.
def clear_time_scenario(
    baseline: dict,
    *,
    regions: int = 3,
    people_per_region_today: int = 2,
    scenario_people_per_region: int | None = None,
    automation: float = 0.4,
) -> dict:
    """Weeks to clear the open backlog under a staffing + automation scenario.

    per-person weekly throughput = ``close / (regions × people_today)``; scenario close =
    per-person × regions × scenario-people; automation removes a share of intake; weeks =
    ``backlog / (close − intake)`` (``None`` when the queue still grows)."""
    backlog = baseline["backlog"]
    intake = baseline["intake_per_week"]
    close = baseline["close_per_week"]
    current_staff = max(regions * int(people_per_region_today), 1)
    per_person = close / current_staff if current_staff else 0.0
    scn_ppr = people_per_region_today if scenario_people_per_region is None else int(scenario_people_per_region)
    scn_close = per_person * regions * scn_ppr
    scn_intake = intake * (1 - automation)
    net = scn_close - scn_intake
    weeks = (backlog / net) if net > 0 else None
    return {
        "backlog": backlog,
        "intake_per_week": round(intake, 2),
        "close_per_week": round(close, 2),
        "per_person_per_week": round(per_person, 3),
        "scenario_close_per_week": round(scn_close, 1),
        "scenario_intake_per_week": round(scn_intake, 1),
        "net_per_week": round(net, 1),
        "weeks_to_clear": round(weeks, 1) if weeks is not None else None,
        "regions": regions,
        "scenario_people_per_region": scn_ppr,
        "automation_pct": round(automation * 100),
    }


def clear_sensitivity(
    baseline: dict,
    *,
    regions: int = 3,
    people_per_region_today: int = 2,
    automation: float = 0.4,
    max_ppr: int = 12,
) -> pd.DataFrame:
    """Weeks-to-clear vs scenario people/region (1..``max_ppr``) at the given automation —
    the sensitivity curve. ``weeks_to_clear`` is ``None`` where the queue still grows."""
    rows = []
    for p in range(1, max_ppr + 1):
        sc = clear_time_scenario(baseline, regions=regions,
                                 people_per_region_today=people_per_region_today,
                                 scenario_people_per_region=p, automation=automation)
        rows.append({"people_per_region": p, "weeks_to_clear": sc["weeks_to_clear"]})
    return pd.DataFrame(rows)


def recovery_simulation(
    baseline: dict,
    *,
    n_hire: int,
    regions: int = 3,
    people_per_region_today: int = 2,
    hiring_months: float = 6,
    onboarding_months: float = 3,
    concurrent: int = 2,
    healthy_level: int = 100,
    horizon_weeks: int = 261,
    start: datetime | None = None,
) -> dict:
    """Simulate the open backlog over 5 years as hires arrive in waves and onboard.

    A hire produces nothing until ``(wave+1)·hiring + onboarding`` weeks out, then full
    per-person throughput. Returns the trajectory (vs do-nothing) as a dated ``projection``
    frame, the peak, the week it reaches ``healthy_level``, and the suggested minimum hires."""
    backlog = float(baseline["backlog"])
    intake = baseline["intake_per_week"]
    close = baseline["close_per_week"]
    current_staff = max(regions * int(people_per_region_today), 1)
    per_person = close / current_staff if current_staff else 0.0
    hire_w = max(hiring_months * 4.345, 1e-9)
    onboard_w = onboarding_months * 4.345
    concurrent = max(int(concurrent), 1)

    def productive_weeks(n: int) -> list[float]:
        return sorted(((i // concurrent) + 1) * hire_w + onboard_w for i in range(n))

    def simulate(n: int):
        prod_at = productive_weeks(n)
        b = backlog
        traj = [b]
        peak, peak_w, recovered = b, 0, None
        for w in range(1, horizon_weeks):
            n_prod = sum(1 for pw in prod_at if w >= pw)
            b = max(b + (intake - (close + n_prod * per_person)), 0.0)
            traj.append(b)
            if b > peak:
                peak, peak_w = b, w
            if recovered is None and b <= healthy_level:
                recovered = w
        return traj, peak, peak_w, recovered

    traj, peak, peak_w, recovered = simulate(int(n_hire))
    do_nothing = simulate(0)[0]
    suggested = next((n for n in range(0, 61) if simulate(n)[3] is not None), None)

    start = start or datetime.now(timezone.utc)
    proj = pd.DataFrame({
        "date": pd.to_datetime([start + timedelta(weeks=w) for w in range(horizon_weeks)]),
        f"hire_{int(n_hire)}": traj,
        "do_nothing": do_nothing,
    })
    return {
        "n_hire": int(n_hire),
        "per_person_per_week": round(per_person, 3),
        "peak_backlog": int(peak),
        "peak_week": peak_w,
        "recovered_week": recovered,
        "healthy_level": healthy_level,
        "suggested_hires": suggested,
        "concurrent": concurrent,
        "projection": proj,
    }


# 3) -------------------------------------------------------------------------
def ps5_blocker_stats(session, cfg: MetricConfig) -> dict:
    """ps5-blocker cadence: arrival/week, time-to-first-work, time-to-resolve (days)."""
    sel = Selector(scope=SCOPE_PS5, pr_mp=PR_MP_INCLUDED)
    head = flow_headline(session, cfg, sel)
    issues = load_scoped_issues(session, cfg, sel)
    sti = load_status_intervals(session, set(issues))

    first_work_days: list[float] = []
    for key, i in issues.items():
        exit_at = status_domain.triage_exit(sti.get(key, []), cfg.untriaged_status)
        if exit_at is not None:
            first_work_days.append((exit_at - i.created_at).total_seconds() / 86400)

    ttc = time_to_close_durations(session, cfg, sel)
    resolve_days = [s / 86400 for s in ttc]

    return {
        "total": len(issues),
        "arrival_per_week": head["created_per_week"],
        "mean_first_work_days": statistics.fmean(first_work_days) if first_work_days else None,
        "n_first_work": len(first_work_days),
        "mean_resolve_days": statistics.fmean(resolve_days) if resolve_days else None,
        "n_resolved": len(resolve_days),
    }


# 4) -------------------------------------------------------------------------
def priority_breakdown(session, cfg: MetricConfig, sel: Selector | None = None) -> pd.DataFrame:
    """By **current** priority (display, Art. VII): created, closed, mean time-to-close (days)."""
    sel = sel or Selector(scope=SCOPE_ALL, pr_mp=PR_MP_INCLUDED)
    issues = load_scoped_issues(session, cfg, sel)
    sti = load_status_intervals(session, set(issues))

    # current_priority lives on the ORM Issue (display-only, Art. VII), which
    # load_scoped_issues doesn't carry, so read it back from the session objects.
    rows: dict[str, dict] = {}
    for i in session.scalars(select(Issue)):
        if i.key not in issues:
            continue
        p = i.current_priority or "Backlog"
        r = rows.setdefault(p, {"created": 0, "closed": 0, "ttc": []})
        r["created"] += 1
        closes = status_domain.close_events(sti.get(i.key, []), cfg.closed_statuses)
        r["closed"] += len(closes)
        for t in closes:
            r["ttc"].append((t - i.created_at).total_seconds() / 86400)

    out = []
    for p, r in rows.items():
        out.append(
            {
                "priority": p,
                "created": r["created"],
                "closed": r["closed"],
                "mean_ttc_days": round(statistics.fmean(r["ttc"]), 1) if r["ttc"] else None,
                "n_closed": len(r["ttc"]),
            }
        )
    df = pd.DataFrame(out)
    if df.empty:
        return df
    df["_ord"] = df["priority"].map(lambda p: PRIORITY_ORDER.index(p) if p in PRIORITY_ORDER else 99)
    return df.sort_values("_ord").drop(columns="_ord").reset_index(drop=True)


def time_to_close_percentiles_by_priority(
    session, cfg: MetricConfig, sel: Selector | None = None
) -> pd.DataFrame:
    """Time-to-close **p50/p85/p95** (days) per **current** priority (display, Art. VII).

    The mean (see :func:`priority_breakdown`) hides the right-skewed tail; read 'p85 = N'
    as '85% of that priority's tickets close within N days'. Each close event is measured
    from the ticket's creation. ``[priority, n_closed, p50_days, p85_days, p95_days]``,
    ordered Highest→Lowest.
    """
    sel = sel or Selector(scope=SCOPE_ALL, pr_mp=PR_MP_INCLUDED)
    issues = load_scoped_issues(session, cfg, sel)
    sti = load_status_intervals(session, set(issues))

    durs: dict[str, list[float]] = {}
    for i in session.scalars(select(Issue)):
        if i.key not in issues:
            continue
        p = i.current_priority or "Backlog"
        for t in status_domain.close_events(sti.get(i.key, []), cfg.closed_statuses):
            durs.setdefault(p, []).append((t - i.created_at).total_seconds() / 86400)

    out = []
    for p, ds in durs.items():
        s = sorted(ds)
        out.append({
            "priority": p,
            "n_closed": len(s),
            "p50_days": round(_percentile(s, 50), 1),
            "p85_days": round(_percentile(s, 85), 1),
            "p95_days": round(_percentile(s, 95), 1),
        })
    df = pd.DataFrame(out)
    if df.empty:
        return pd.DataFrame(columns=["priority", "n_closed", "p50_days", "p85_days", "p95_days"])
    df["_ord"] = df["priority"].map(lambda p: PRIORITY_ORDER.index(p) if p in PRIORITY_ORDER else 99)
    return df.sort_values("_ord").drop(columns="_ord").reset_index(drop=True)


# 5) -------------------------------------------------------------------------
def automation_targets(session, cfg: MetricConfig, sel: Selector | None = None) -> pd.DataFrame:
    """Rank sub-areas by volume × effort to flag the best automation candidates.

    score = 100 × (½·normalised(tickets) + ½·normalised(total hours)). High score =
    lots of tickets **and** lots of logged effort → the most leverage from automating.
    """
    sel = sel or Selector(scope=SCOPE_ALL, pr_mp=PR_MP_INCLUDED)
    intake = intake_series(session, cfg, sel, group="sub_area")
    tickets = (
        intake.groupby("group", as_index=False)["count"].sum().rename(columns={"count": "tickets"})
        if not intake.empty
        else pd.DataFrame(columns=["group", "tickets"])
    )

    issues = load_scoped_issues(session, cfg, sel)
    spent = _worklog_seconds(session, set(issues))
    sub_hours: dict[str, float] = {}
    for key, i in issues.items():
        sub = f"{i.area or 'Backlog'} ▸ {i.sub_area or 'Backlog'}"
        sub_hours[sub] = sub_hours.get(sub, 0.0) + spent.get(key, 0) / 3600

    hours = pd.DataFrame({"group": list(sub_hours), "hours": list(sub_hours.values())})
    df = pd.merge(tickets, hours, on="group", how="outer").fillna(0)
    if df.empty:
        return df

    def _norm(col: pd.Series) -> pd.Series:
        rng = col.max() - col.min()
        return (col - col.min()) / rng if rng else col * 0.0

    df["avg_hours_per_ticket"] = (df["hours"] / df["tickets"]).replace([float("inf")], 0).fillna(0)
    df["score"] = (100 * (0.5 * _norm(df["tickets"]) + 0.5 * _norm(df["hours"]))).round(1)
    df["tickets"] = df["tickets"].astype(int)
    df["hours"] = df["hours"].round(1)
    df["avg_hours_per_ticket"] = df["avg_hours_per_ticket"].round(2)
    return df.sort_values("score", ascending=False).reset_index(drop=True)
