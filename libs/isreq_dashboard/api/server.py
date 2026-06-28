"""FastAPI backend (issue #40): serves the audited metrics as JSON for the Pragma SPA.

Read-only, Postgres-only. Like the Streamlit app, this layer **never imports
``isreq_dashboard.jira``** (render isolation, Art. X) — the ``/api/sync`` endpoint runs
the sync CLI as a subprocess, so Jira stays out of this process. The Python metric
definitions are reused verbatim.

Each page endpoint returns ``{"sections": [...]}``. A section is one of:
  - ``{"type":"kv",    "title", "values": {...}}``
  - ``{"type":"table", "title", "data": [...]}``
  - ``{"type":"chart", "title", "x", "data": [...], "series": [{key,label,color,mark}]}``
The frontend renders sections generically, so a page can show many charts + tables.
"""

from __future__ import annotations

import logging
import math
import re
import subprocess
import sys
from dataclasses import replace as dc_replace
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Literal

import numpy as np
import pandas as pd
from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from isreq_dashboard.config import Settings
from isreq_dashboard.db.engine import make_engine
from isreq_dashboard.db.session import make_session_factory
from isreq_dashboard.metrics.base import MetricConfig, Selector, last_sync_at

log = logging.getLogger("isreq.api")
ROOT = Path(__file__).resolve().parents[3]  # repo root — cwd for the sync subprocess

# Chart palette (Canonical-ish).
BLUE, GREEN, RED, ORANGE, PURPLE, TEAL = (
    "#0066cc", "#0e8420", "#c7162b", "#f99b11", "#7e5fb3", "#007aa6",
)
# Cycled when a chart has a dynamic, unbounded set of series (e.g. one per status).
PALETTE = [BLUE, GREEN, ORANGE, PURPLE, TEAL, RED,
           "#8a6d3b", "#16a085", "#9b59b6", "#34495e", "#e67e22", "#7f8c8d"]


@lru_cache(maxsize=1)
def _settings() -> Settings:
    return Settings.load()


@lru_cache(maxsize=1)
def _factory():
    return make_session_factory(make_engine(_settings()))


@lru_cache(maxsize=1)
def _cfg() -> MetricConfig:
    t = _settings().toml
    return MetricConfig(
        anchor=t.anchor_date,
        closed_statuses=t.closed_statuses,
        highest_priority_name=t.highest_priority_name,
        ps5_blocker_label=t.ps5_blocker_label,
        region_windows=t.region_windows_utc,
        low_n_threshold=t.low_n_threshold,
        untriaged_status=t.untriaged_status,
        in_review_status=t.in_review_status,
    )


# --- PagerDuty analysis (co-tenant, own `pd` schema, never touches isreq) -----
@lru_cache(maxsize=1)
def _pd_factory():
    s = _settings()
    return make_session_factory(make_engine(s, schema=s.pd_db_schema))


@lru_cache(maxsize=1)
def _pd_cfg():
    from isreq_dashboard.metrics.pd_base import PdMetricConfig

    pd_cfg = _settings().toml.pd
    return PdMetricConfig(
        anchor=pd_cfg.since,
        region_windows=pd_cfg.region_windows_utc,
        low_n_threshold=_settings().toml.low_n_threshold,
    )


# --- JSON cleaning ----------------------------------------------------------
def _clean(v: Any) -> Any:
    if isinstance(v, pd.DataFrame):
        return _records(v)
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        f = float(v)
        return None if math.isnan(f) else f
    if isinstance(v, float):
        return None if math.isnan(v) else v
    if isinstance(v, np.bool_):
        return bool(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _records(df: pd.DataFrame | None) -> list[dict[str, Any]]:
    if df is None or getattr(df, "empty", True):
        return []
    return [{k: _clean(val) for k, val in row.items()} for row in df.to_dict(orient="records")]


# --- section builders -------------------------------------------------------
def _series(*specs: tuple) -> list[dict]:
    """Each spec: (key, label, color, mark) where mark is bar|line|area."""
    return [{"key": k, "label": ll, "color": c, "mark": m} for k, ll, c, m in specs]


def _chart(title: str, x: str, data, series: list[dict], height: int = 300,
           stacked: bool = False) -> dict:
    out = {
        "type": "chart", "title": title, "x": x,
        "data": _records(data) if isinstance(data, pd.DataFrame) else _clean(data),
        "series": series, "height": height,
    }
    if stacked:
        out["stacked"] = True
    return out


def _pivot_chart(title: str, df: pd.DataFrame | None, value_col: str, *,
                 x: str = "period", mark: str = "bar", stacked: bool = False,
                 height: int = 300) -> dict:
    """Long ``[x, group, value_col]`` -> wide chart with one series per group.

    Colours are cycled from ``PALETTE`` since the group set (statuses, areas) is
    dynamic. Use ``stacked=True`` for a stacked bar (e.g. status mix)."""
    if df is None or getattr(df, "empty", True):
        return _chart(title, x, [], [], height=height, stacked=stacked)
    groups = list(dict.fromkeys(df["group"]))
    wide = df.pivot_table(index=x, columns="group", values=value_col,
                          aggfunc="sum", fill_value=0).reset_index()
    series = [{"key": g, "label": g, "color": PALETTE[i % len(PALETTE)], "mark": mark}
              for i, g in enumerate(groups)]
    return _chart(title, x, wide, series, height=height, stacked=stacked)


def _table(title: str, data) -> dict:
    return {"type": "table", "title": title,
            "data": _records(data) if isinstance(data, pd.DataFrame) else _clean(data)}


def _kv(title: str, d: dict) -> dict:
    values = {k: _clean(v) for k, v in d.items()
              if not isinstance(v, (list, dict, pd.DataFrame))}
    return {"type": "kv", "title": title, "values": values}


def _histogram(seconds: list[float], bins: int = 20) -> pd.DataFrame:
    days = [s / 86400 for s in seconds]
    if not days:
        return pd.DataFrame(columns=["days", "count"])
    counts, edges = np.histogram(days, bins=bins)
    mids = [round((edges[i] + edges[i + 1]) / 2, 1) for i in range(len(counts))]
    return pd.DataFrame({"days": mids, "count": counts})


def _period_marks(cadence: str) -> dict[str, str]:
    """Sprint marks (period label -> caption) for the active cadence — the dashed
    vertical rules Streamlit overlays on period charts (e.g. the Pulse 9 sprint)."""
    marks = _settings().toml.period_marks or {}
    return marks.get("per_pulse" if cadence == "per_pulse" else "weekly", {})


def _attach_marks(sections: list[dict], cadence: str) -> None:
    """Add ``marks`` (the in-view sprint rules) to each period chart, in place.
    Only marks whose period is actually present in the chart's data are kept."""
    marks = _period_marks(cadence)
    if not marks:
        return
    for s in sections:
        if s.get("type") != "chart" or s.get("x") != "period":
            continue
        present = {str(r.get("period")) for r in s.get("data", [])}
        in_view = [{"x": p, "label": lbl} for p, lbl in marks.items() if p in present]
        if in_view:
            s["marks"] = in_view


def _build(builders: list[Callable[[Any], dict | None]], session,
           *, cadence: str | None = None) -> list[dict]:
    """Run each section builder defensively — a failing metric is skipped, not fatal.
    When ``cadence`` is given, overlay the period-chart sprint marks for it."""
    out: list[dict] = []
    for b in builders:
        try:
            s = b(session)
            if s:
                out.append(s)
        except Exception as exc:  # noqa: BLE001 — one bad section must not 500 the page
            log.warning("section builder failed: %s", exc)
    if cadence:
        _attach_marks(out, cadence)
    return out


app = FastAPI(title="ISReq Analytics API", version="0.2.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# --- request selectors (the SPA "View controls") ----------------------------
# cadence/scope/PR-MP come in as query params and build a per-request Selector,
# replacing the old module-global default so every page can be resliced. Defaults
# match the historical hardcoded ``Selector()`` so param-less calls are unchanged;
# the Literal types make FastAPI reject anything else with a 422.
Cadence = Literal["weekly", "per_pulse"]
Scope = Literal["all", "highest", "ps5_blocker", "highest_or_ps5"]
PrMp = Literal["included", "excluded", "only"]


def selector(
    cadence: Cadence = Query("weekly", description="Period cadence: IS Pulse (sprint) vs Week"),
    scope: Scope = Query("all", description="Ticket scope filter (Highest / ps5-blocker)"),
    pr_mp: PrMp = Query("included", description="PR/MP-review tickets: included/excluded/only"),
) -> Selector:
    return Selector(cadence=cadence, scope=scope, pr_mp=pr_mp)


def _cap_groups(df: pd.DataFrame | None, value_col: str, *, top: int = 12,
                other: str = "Other") -> pd.DataFrame | None:
    """Keep the top-N groups by total ``value_col`` and fold the rest into one
    ``Other`` bucket, so a stacked breakdown stays legible when the group set
    (areas / sub-areas / issues) is large."""
    if df is None or getattr(df, "empty", True) or "group" not in df.columns:
        return df
    totals = df.groupby("group")[value_col].sum().sort_values(ascending=False)
    if len(totals) <= top:
        return df
    keep = set(totals.head(top).index)
    out = df.copy()
    out["group"] = out["group"].where(out["group"].isin(keep), other)
    return out


# --- health / sync ----------------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    from sqlalchemy import text

    with _factory()() as s:
        issues = s.execute(text("SELECT count(*) FROM isreq.issues")).scalar_one()
        ts = last_sync_at(s)
    return {"status": "ok", "issues": int(issues), "last_sync": ts.isoformat() if ts else None}


@app.post("/api/sync")
def sync() -> dict:
    """Read-only incremental sync of BOTH sources (Jira + PagerDuty), each run as a
    subprocess so neither source library enters this process. Incremental: pulls only
    each source's changes since its last watermark."""
    def _run(module: str, source: str, label: str, count_re: str) -> dict:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", module],
                cwd=str(ROOT), capture_output=True, text=True, timeout=1800,
            )
            out = f"{proc.stdout or ''}\n{proc.stderr or ''}"
            m = re.search(count_re, out)
            count = int(m.group(1)) if m else None
            tail = proc.stderr.strip().splitlines()[-3:] if proc.stderr else []
            return {"source": source, "label": label, "ok": proc.returncode == 0,
                    "count": count, "log": tail}
        except Exception as exc:  # noqa: BLE001 — one source failing must not 500 the other
            return {"source": source, "label": label, "ok": False, "count": None, "log": [str(exc)]}

    results = [
        _run("isreq_dashboard.cli.sync_main", "jira", "Jira", r"sync complete: (\d+) issues"),
        _run("isreq_dashboard.cli.pd_sync_main", "pagerduty", "PagerDuty",
             r"pd sync complete: (\d+) incidents"),
    ]
    return {"ok": all(r["ok"] for r in results), "results": results}


# --- pages ------------------------------------------------------------------
@app.get("/api/north-star")
def north_star(sel: Selector = Depends(selector)) -> dict:
    from isreq_dashboard.metrics.highest import highest_series

    def headline(s):
        df = highest_series(s, _cfg(), sel)
        recs = _records(df)
        became = sum(r.get("became_highest", 0) or 0 for r in recs)
        closed = sum(r.get("highest_closed", 0) or 0 for r in recs)
        backlog = recs[-1].get("highest_backlog") if recs else 0
        return _kv("Headline", {
            "became_highest": became, "highest_closed": closed,
            "current_highest_backlog": backlog,
            "verdict": "intake outpacing closure" if became > closed else "closure keeping up",
        })

    def chart(s):
        return _chart("Became Highest vs Highest closed, per period", "period",
                      highest_series(s, _cfg(), sel),
                      _series(("became_highest", "Became Highest", BLUE, "bar"),
                              ("highest_closed", "Highest closed", GREEN, "bar"),
                              ("highest_backlog", "Highest backlog", RED, "line")))

    def table(s):
        return _table("Per-period detail", highest_series(s, _cfg(), sel))

    with _factory()() as s:
        return {"sections": _build([headline, chart, table], s, cadence=sel.cadence)}


@app.get("/api/intake")
def intake(sel: Selector = Depends(selector),
           group: Literal["area", "sub_area", "region_time_of_day"] = "area") -> dict:
    from isreq_dashboard.metrics.intake import intake_series

    label = {"area": "area", "sub_area": "sub-area", "region_time_of_day": "region"}[group]

    def per_period(s):
        df = _cap_groups(intake_series(s, _cfg(), sel, group=group), "count")
        return _pivot_chart(f"Tickets created per period by {label}", df, "count",
                            mark="bar", stacked=True, height=340)

    def by_group_chart(s):
        df = intake_series(s, _cfg(), sel, group=group)
        agg = (df.groupby("group", as_index=False)["count"].sum()
               .sort_values("count", ascending=False).head(15)) if not df.empty else df
        return _chart(f"Created by {label} (total)", "group", agg,
                      _series(("count", "Created", TEAL, "bar")), height=360)

    def by_group_table(s):
        df = intake_series(s, _cfg(), sel, group=group)
        agg = (df.groupby("group", as_index=False)["count"].sum()
               .sort_values("count", ascending=False)) if not df.empty else df
        return _table(f"Intake by {label}", agg)

    with _factory()() as s:
        return {"sections": _build([per_period, by_group_chart, by_group_table], s,
                                   cadence=sel.cadence)}


@app.get("/api/throughput")
def throughput(sel: Selector = Depends(selector),
               group: Literal["total", "area", "sub_area"] = "total") -> dict:
    from isreq_dashboard.metrics.throughput import (
        throughput_series,
        time_to_close_by_area,
        time_to_close_percentiles,
        time_to_close_stats,
    )

    def per_period(s):
        if group == "total":
            return _chart("Close events per period", "period", throughput_series(s, _cfg(), sel),
                          _series(("throughput", "Closed", GREEN, "bar")))
        label = "area" if group == "area" else "sub-area"
        df = _cap_groups(throughput_series(s, _cfg(), sel, group=group), "throughput")
        return _pivot_chart(f"Close events per period by {label}", df, "throughput",
                            mark="bar", stacked=True, height=340)

    def ttc(s):
        st = time_to_close_stats(s, _cfg(), sel)
        return _kv("Time to close", {
            "mean_days": round(st.mean / 86400, 1) if st.mean else None, "n": st.n})

    def percentiles(s):
        pc = time_to_close_percentiles(s, _cfg(), sel)
        return _kv("Time-to-close percentiles (days)", {
            "p50": round(pc["p50"] / 86400, 1) if pc["p50"] else None,
            "p85": round(pc["p85"] / 86400, 1) if pc["p85"] else None,
            "p95": round(pc["p95"] / 86400, 1) if pc["p95"] else None})

    def hist(s):
        pc = time_to_close_percentiles(s, _cfg(), sel)
        return _chart("Time-to-close distribution", "days", _histogram(pc["durations"]),
                      _series(("count", "Tickets", BLUE, "bar")))

    def by_area(s):
        return _table("Slowest areas (time-to-close)", time_to_close_by_area(s, _cfg(), sel))

    with _factory()() as s:
        return {"sections": _build([per_period, ttc, percentiles, hist, by_area], s,
                                   cadence=sel.cadence)}


@app.get("/api/backlog")
def backlog(sel: Selector = Depends(selector),
            group: Literal["total", "area", "sub_area"] = "total") -> dict:
    from isreq_dashboard.metrics.backlog import (
        backlog_series,
        carryover_series,
        carryover_streaks,
        wip_series,
    )
    from isreq_dashboard.metrics.flow import flow_series

    def stock(s):
        if group == "total":
            return _chart("Open backlog over time", "period", backlog_series(s, _cfg(), sel),
                          _series(("backlog", "Open", BLUE, "area")))
        label = "area" if group == "area" else "sub-area"
        df = _cap_groups(backlog_series(s, _cfg(), sel, group=group), "backlog")
        return _pivot_chart(f"Open backlog over time by {label}", df, "backlog",
                            mark="bar", stacked=True, height=340)

    def carryover(s):
        return _chart("Carryover: created vs carried over", "period",
                      carryover_series(s, _cfg(), sel),
                      _series(("created", "Created", BLUE, "bar"),
                              ("carried_over", "Carried over", ORANGE, "bar")))

    def cfd(s):
        return _chart("Cumulative flow (created vs resolved)", "period",
                      flow_series(s, _cfg(), sel),
                      _series(("cum_created", "Created", BLUE, "area"),
                              ("cum_closed", "Resolved", GREEN, "area")))

    def wip(s):
        return _chart("Work in progress over time", "period", wip_series(s, _cfg(), sel),
                      _series(("wip", "WIP", PURPLE, "line")))

    def spillover(s):
        return _table("Chronic spillover (≥2 pulses)", carryover_streaks(s, _cfg(), sel))

    with _factory()() as s:
        return {"sections": _build([stock, carryover, cfd, wip, spillover], s,
                                   cadence=sel.cadence)}


@app.get("/api/cycle-times")
def cycle_times(sel: Selector = Depends(selector)) -> dict:
    from isreq_dashboard.metrics.cycle_times import (
        KIND_CLOSE,
        KIND_IN_REVIEW,
        KIND_TRIAGE,
        cycle_time_series,
    )

    def _merged(s):
        by_period: dict[str, dict] = {}
        for k in (KIND_TRIAGE, KIND_CLOSE, KIND_IN_REVIEW):
            for r in _records(cycle_time_series(s, _cfg(), sel, k)):
                p = str(r["period"])
                row = by_period.setdefault(p, {"period": p})
                mean = r.get("mean")
                row[k] = round(mean / 86400, 2) if mean is not None else None
        return list(by_period.values())

    def chart(s):
        return _chart("Mean cycle time (days), per period", "period", _merged(s),
                      _series(("triage", "Time to triage", BLUE, "line"),
                              ("close", "Time to close", GREEN, "line"),
                              ("in_review", "Time in review", ORANGE, "line")))

    def table(s):
        return _table("Mean days per period", _merged(s))

    with _factory()() as s:
        return {"sections": _build([chart, table], s, cadence=sel.cadence)}


@app.get("/api/insights")
def insights(sel: Selector = Depends(selector)) -> dict:
    from isreq_dashboard.metrics.insights import (
        aging_buckets,
        blocked_analysis,
        deprioritized_highest,
        effort_pareto,
        escalation_breakdown,
        hr_automation_summary,
        region_load,
        rejection_rate,
        reopen_stats,
        stale_tickets,
        status_churn,
        worklog_coverage,
    )

    def reopen(s):
        return _kv("Reopen rate", reopen_stats(s, _cfg()))

    def aging(s):
        df = aging_buckets(s, _cfg())
        agg = df.groupby("bucket", as_index=False, sort=False)["count"].sum() if not df.empty else df
        return _chart("Open tickets by age bucket", "bucket", agg,
                      _series(("count", "Open", PURPLE, "bar")))

    def coverage(s):
        return _chart("Worklog coverage %, per period", "period",
                      worklog_coverage(s, _cfg(), sel),
                      _series(("coverage_pct", "Coverage %", GREEN, "line")))

    def escalation(s):
        return _kv("Escalation to Highest", escalation_breakdown(s, _cfg()))

    def pareto_chart(s):
        df = effort_pareto(s, _cfg())
        top = df.drop(columns=["keys"], errors="ignore").head(15) if not df.empty else df
        return _chart("Effort Pareto — top sub-areas (hours)", "group", top,
                      _series(("hours", "Hours", TEAL, "bar")), height=420)

    def pareto_table(s):
        df = effort_pareto(s, _cfg())
        return _table("Effort Pareto", df.drop(columns=["keys"], errors="ignore") if not df.empty else df)

    def stale(s):
        return _table("Stale tickets (idle ≥14d)", stale_tickets(s, _cfg()))

    def blocked(s):
        b = blocked_analysis(s, _cfg())
        return _kv("Blocked", {k: v for k, v in b.items() if k != "tickets"})

    def churn(s):
        return _table("Status churn (frequent re-transitions)", status_churn(s, _cfg()))

    def rejection(s):
        rr = rejection_rate(s, _cfg())
        return _kv("Rejection rate", {k: v for k, v in rr.items() if k != "by_area"})

    def deprioritized(s):
        return _table("Deprioritized Highest", deprioritized_highest(s, _cfg()))

    def region(s):
        return _chart("Open load by region", "region", region_load(s, _cfg()),
                      _series(("open_tickets", "Open", BLUE, "bar")))

    def hr(s):
        return _kv("HR automation", hr_automation_summary(s, _cfg()))

    with _factory()() as s:
        return {"sections": _build(
            [reopen, aging, coverage, escalation, pareto_chart, pareto_table, stale,
             blocked, churn, rejection, deprioritized, region, hr], s, cadence=sel.cadence)}


@app.get("/api/predictions")
def predictions(
    sel: Selector = Depends(selector),
    automate_pct: int = Query(40, ge=0, le=100, description="Automate % of intake (clear-backlog scenario)"),
    regions: int = Query(3, ge=1, le=6, description="Regions"),
    people_today: int = Query(2, ge=1, le=20, description="People/region today (throughput calibration)"),
    scenario_ppr: int = Query(2, ge=1, le=12, description="People/region working tickets (scenario)"),
    n_hire: int = Query(8, ge=0, le=60, description="Engineers to hire (recovery scenario)"),
    hiring_months: int = Query(6, ge=1, le=36, description="Hiring lead time (months/wave)"),
    onboarding_months: int = Query(3, ge=0, le=24, description="Onboarding before full output (months)"),
    concurrent: int = Query(2, ge=1, le=20, description="Concurrent hires per wave"),
    healthy_level: int = Query(100, ge=0, le=10000, description="Healthy backlog (open tickets)"),
) -> dict:
    from isreq_dashboard.metrics.predictions import (
        automation_targets,
        backlog_baseline,
        backlog_burndown,
        clear_sensitivity,
        clear_time_scenario,
        priority_breakdown,
        pr_mp_forecast,
        ps5_blocker_stats,
        recovery_simulation,
        time_to_close_percentiles_by_priority,
    )

    def by_priority(s):
        return _chart("Created vs closed, by priority", "priority",
                      priority_breakdown(s, _cfg(), sel),
                      _series(("created", "Created", BLUE, "bar"),
                              ("closed", "Closed", GREEN, "bar")))

    def percentiles(s):
        return _chart("Time-to-close percentiles (days), by priority", "priority",
                      time_to_close_percentiles_by_priority(s, _cfg(), sel),
                      _series(("p50_days", "p50", GREEN, "bar"),
                              ("p85_days", "p85", ORANGE, "bar"),
                              ("p95_days", "p95", RED, "bar")))

    def prmp(s):
        return _kv("PR/MP forecast", pr_mp_forecast(s, _cfg(), sel.cadence))

    def ps5(s):
        return _kv("ps5-blocker stats", ps5_blocker_stats(s, _cfg()))

    def burndown(s):
        bd = backlog_burndown(s, _cfg(), sel)
        return _chart("Backlog burndown projection (current rate)", "date", bd.get("projection"),
                      _series(("projected_backlog", "Projected backlog", RED, "line")))

    def automation(s):
        return _table("Automation targets (area ▸ sub-area)", automation_targets(s, _cfg(), sel))

    with _factory()() as s:
        # scenario inputs computed once from the DB-derived baseline (same math as Streamlit)
        base = backlog_baseline(s, _cfg(), sel)
        clear = clear_time_scenario(base, regions=regions, people_per_region_today=people_today,
                                    scenario_people_per_region=scenario_ppr, automation=automate_pct / 100)
        sens = clear_sensitivity(base, regions=regions, people_per_region_today=people_today,
                                 automation=automate_pct / 100)
        rec = recovery_simulation(base, n_hire=n_hire, regions=regions, people_per_region_today=people_today,
                                  hiring_months=hiring_months, onboarding_months=onboarding_months,
                                  concurrent=concurrent, healthy_level=healthy_level)

        def baseline_kv(_s):
            return _kv("Backlog baseline (per week)", base)

        def clear_kv(_s):
            weeks = clear["weeks_to_clear"]
            return _kv(f"Clear the backlog — {automate_pct}% automation, {scenario_ppr} ppl/region", {
                "open_backlog": clear["backlog"],
                "per_person_per_week": clear["per_person_per_week"],
                "scenario_closes_per_week": clear["scenario_close_per_week"],
                "scenario_intake_per_week": clear["scenario_intake_per_week"],
                "net_per_week": clear["net_per_week"],
                "weeks_to_clear": weeks if weeks is not None else "never — queue grows",
            })

        def sensitivity_chart(_s):
            return _chart("Weeks to clear vs people/region (at the chosen automation)",
                          "people_per_region", sens,
                          _series(("weeks_to_clear", "Weeks to clear", PURPLE, "line")))

        def recovery_kv(_s):
            rw = rec["recovered_week"]
            sug = rec["suggested_hires"]
            return _kv(f"Recovery plan — hire {n_hire} ({concurrent}/wave, {hiring_months}mo + {onboarding_months}mo)", {
                "engineers_to_hire": rec["n_hire"],
                "peak_backlog": rec["peak_backlog"],
                "weeks_to_healthy": rw if rw is not None else "not within 5y",
                "healthy_level": rec["healthy_level"],
                "suggested_min_hires": sug if sug is not None else ">60",
            })

        def recovery_chart(_s):
            return _chart("Backlog recovery projection (5 years)", "date", rec["projection"],
                          _series((f"hire_{n_hire}", f"Hire {n_hire}", BLUE, "line"),
                                  ("do_nothing", "Do nothing", RED, "line")))

        # order mirrors the Streamlit page's §1..§5 so the two pages read the same top-down:
        # 1) PR/MP forecast, 2) staffing/recovery what-if + burndown, 3) ps5, 4) by priority, 5) automation
        return {"sections": _build(
            [prmp, baseline_kv, clear_kv, sensitivity_chart, recovery_kv, recovery_chart, burndown,
             ps5, by_priority, percentiles, automation], s, cadence=sel.cadence)}


@app.get("/api/time-invested")
def time_invested(sel: Selector = Depends(selector),
                  group: Literal["area", "sub_area", "issue"] = "area") -> dict:
    from isreq_dashboard.metrics.time_invested import (
        BEST_EFFORT_CAVEAT,
        time_invested_series,
    )

    label = {"area": "area", "sub_area": "sub-area", "issue": "issue"}[group]

    def _hours(s):
        df = time_invested_series(s, _cfg(), sel, group=group)
        return df.assign(hours=(df["seconds"] / 3600).round(2)) if not df.empty else df

    def caveat(s):
        return _kv("Note", {"attribution": BEST_EFFORT_CAVEAT})

    def per_period(s):
        df = _cap_groups(_hours(s), "hours")
        return _pivot_chart(f"Hours logged per period by {label}", df, "hours",
                            mark="bar", stacked=True, height=340)

    def by_group_chart(s):
        df = _hours(s)
        agg = (df.groupby("group", as_index=False)["hours"].sum()
               .sort_values("hours", ascending=False).head(15)) if not df.empty else df
        return _chart(f"Hours by {label} (total)", "group", agg,
                      _series(("hours", "Hours", BLUE, "bar")), height=360)

    def by_group_table(s):
        df = _hours(s)
        agg = (df.groupby("group", as_index=False)["hours"].sum()
               .sort_values("hours", ascending=False)) if not df.empty else df
        return _table(f"Hours by {label}", agg)

    with _factory()() as s:
        return {"sections": _build([caveat, per_period, by_group_chart, by_group_table], s,
                                   cadence=sel.cadence)}


@app.get("/api/status-mix")
def status_mix(sel: Selector = Depends(selector)) -> dict:
    from isreq_dashboard.metrics.status_mix import status_mix_series

    def stacked(s):
        return _pivot_chart("Tickets per period by current status",
                            status_mix_series(s, _cfg(), sel), "count",
                            mark="bar", stacked=True, height=360)

    def distribution(s):
        df = status_mix_series(s, _cfg(), sel)
        agg = (df.groupby("group", as_index=False)["count"].sum()
               .sort_values("count", ascending=False)) if not df.empty else df
        return _chart("Current status distribution", "group", agg,
                      _series(("count", "Tickets", PURPLE, "bar")))

    def table(s):
        df = status_mix_series(s, _cfg(), sel)
        if df.empty:
            return _table("Period x status", df)
        wide = df.pivot_table(index="period", columns="group", values="count",
                              aggfunc="sum", fill_value=0).reset_index()
        return _table("Period x status", wide)

    with _factory()() as s:
        return {"sections": _build([stacked, distribution, table], s, cadence=sel.cadence)}


@app.get("/api/region")
def region(sel: Selector = Depends(selector)) -> dict:
    from sqlalchemy import select

    from isreq_dashboard.db.models import Issue, User
    from isreq_dashboard.domain.regions import region_from_user_map
    from isreq_dashboard.metrics.base import _ever_highest_keys, load_scoped_issues
    from isreq_dashboard.metrics.intake import GROUP_REGION, intake_series

    def by_creation(s):
        df = intake_series(s, _cfg(), sel, group=GROUP_REGION)
        agg = (df.groupby("group", as_index=False)["count"].sum()
               .sort_values("count", ascending=False)) if not df.empty else df
        return _chart("Created by region (creation time-of-day)", "group", agg,
                      _series(("count", "Created", BLUE, "bar")))

    def by_assignee(s):
        users = {u.account_id: u for u in s.scalars(select(User))}
        user_region = {aid: u.region for aid, u in users.items()}
        external = {aid for aid, u in users.items() if u.is_external}
        counts: dict[str, int] = {}
        for i in load_scoped_issues(s, _cfg(), sel).values():
            aid = i.assignee_account_id
            if aid is None or aid in external:
                continue
            r = region_from_user_map(aid, user_region)
            r = "Backlog" if r == "Unknown" else r
            counts[r] = counts.get(r, 0) + 1
        data = [{"region": k, "tickets": v}
                for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
        return _chart("Per-user region (assignee map, excl. external)", "region", data,
                      _series(("tickets", "Tickets", TEAL, "bar")))

    def highest_by_reporter(s):
        users = {u.account_id: u for u in s.scalars(select(User))}
        keys = _ever_highest_keys(s, _cfg())
        if not keys:
            return _kv("Highest tickets by reporter", {"highest_tickets": 0})
        c = {"internal_is_team": 0, "external": 0, "no_reporter": 0}
        for i in s.scalars(select(Issue).where(Issue.key.in_(keys))):
            aid = i.reporter_account_id
            if aid is None:
                c["no_reporter"] += 1
            elif aid in users and not users[aid].is_external:
                c["internal_is_team"] += 1
            else:
                c["external"] += 1
        return _kv("Highest tickets by reporter (internal vs external)", c)

    with _factory()() as s:
        return {"sections": _build([by_creation, by_assignee, highest_by_reporter], s)}


@app.get("/api/data-quality")
def data_quality() -> dict:
    from isreq_dashboard.metrics.anomalies import (
        ordinary_worked_after_cutoff,
        pr_mp_ever_highest,
        unassigned_past_triage,
    )

    def prmp(s):
        rows = pr_mp_ever_highest(s, _cfg())
        still = sum(1 for r in rows if r["current_priority"] == _cfg().highest_priority_name)
        return _kv("PR/MP-review tickets ever Highest (should be 0)",
                   {"count": len(rows), "still_highest": still})

    def prmp_table(s):
        rows = pr_mp_ever_highest(s, _cfg())
        data = [{"key": r["key"], "title": r["title"], "assignee": r["assignee_name"],
                 "current_priority": r["current_priority"],
                 "current_status": r["current_status"],
                 "first_highest_at": r["first_highest_at"]} for r in rows]
        return _table("PR/MP ever Highest", data)

    def cutoff(s):
        off = ordinary_worked_after_cutoff(s, _cfg())
        hours = round(sum(r["time_after_seconds"] for r in off) / 3600, 1)
        return _kv("Ordinary tickets worked after Pulse 9 (policy violation)",
                   {"count": len(off), "hours_after_cutoff": hours})

    def cutoff_table(s):
        off = ordinary_worked_after_cutoff(s, _cfg())
        data = [{"key": r["key"], "assignee": r["assignee_name"],
                 "time_after_h": round(r["time_after_seconds"] / 3600, 1),
                 "total_h": round(r["time_spent_seconds"] / 3600, 1),
                 "last_activity": r["last_activity_at"],
                 "status": r["current_status"], "priority": r["current_priority"],
                 "title": r["title"]} for r in off]
        return _table("Worked after Pulse 9", data)

    def unassigned(s):
        return _kv("Past triage but unassigned", {"count": len(unassigned_past_triage(s, _cfg()))})

    def unassigned_table(s):
        data = [{"key": r["key"], "status": r["current_status"], "area": r["area"],
                 "created": r["created_at"], "title": r["title"]}
                for r in unassigned_past_triage(s, _cfg())]
        return _table("Unassigned past triage", data)

    with _factory()() as s:
        return {"sections": _build(
            [prmp, prmp_table, cutoff, cutoff_table, unassigned, unassigned_table], s)}


@app.get("/api/reports")
def reports(top_n: int = Query(15, ge=5, le=40, description="Show top N reporters")) -> dict:
    from isreq_dashboard.metrics.reports import (
        top_reporters_all,
        top_reporters_highest,
        top_reporters_ps5,
    )

    def all_filed(s):
        return _chart("All tickets filed - top reporters", "reporter",
                      top_reporters_all(s, _cfg(), top_n),
                      _series(("count", "Tickets filed", BLUE, "bar")), height=420)

    def highest(s):
        return _chart("Highest tickets filed - top reporters", "reporter",
                      top_reporters_highest(s, _cfg(), top_n),
                      _series(("count", "Tickets filed", RED, "bar")), height=420)

    def ps5(s):
        return _chart("ps5-blocker tickets filed - top reporters", "reporter",
                      top_reporters_ps5(s, _cfg(), top_n),
                      _series(("count", "Tickets filed", ORANGE, "bar")), height=420)

    with _factory()() as s:
        return {"sections": _build([all_filed, highest, ps5], s)}


# --- PagerDuty pages (analysis #2; reads pd schema only) --------------------
PdGroup = Literal["cloud", "juju_model", "charm", "alertname", "region"]
PdDim = Literal["alertname", "service", "juju_model"]
# Default to "handled": "received" is empty for resolved incidents (PagerDuty clears
# the assignee on resolve), so a received-ranked chart looks blank over a backfill.
# *_pct are each SRE's share of the team total (% of alerts handled / % of time spent);
# "disproportion" = % time − % alerts (the outliers carrying the long incidents).
PdMeasure = Literal["handled", "handled_pct", "time_spent_hours", "time_spent_pct",
                    "disproportion", "received"]


def _parse_pd_date(v: str | None, *, is_end: bool) -> datetime | None:
    """Parse a YYYY-MM-DD (or ISO datetime) bound to tz-aware UTC. A bare end date is
    treated as inclusive-of-that-day (advanced to the next midnight, exclusive)."""
    if not v:
        return None
    d = datetime.fromisoformat(v)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    if is_end and (d.hour, d.minute, d.second) == (0, 0, 0):
        d = d + timedelta(days=1)
    return d


def _pd_window_cfg(start: str | None, end: str | None):
    """A per-request PdMetricConfig with the optional [start, end) trigger-time window."""
    return dc_replace(_pd_cfg(),
                      start=_parse_pd_date(start, is_end=False),
                      end=_parse_pd_date(end, is_end=True))


def _pd_window_section(session, cfg) -> dict:
    """A kv section showing the full data extent and the currently-shown window."""
    from isreq_dashboard.metrics.pd_base import data_window

    lo, hi = data_window(session)
    vals: dict[str, Any] = {
        "data_from": lo.date().isoformat() if lo else None,
        "data_to": hi.date().isoformat() if hi else None,
    }
    if cfg.start is not None:
        vals["showing_from"] = cfg.start.date().isoformat()
    if cfg.end is not None:
        vals["showing_to"] = (cfg.end - timedelta(days=1)).date().isoformat()
    return _kv("Data window (alert trigger time, UTC)", vals)


# Result cache for the PD pages. The pd data is static between syncs (sync-then-read),
# so a page's sections are fully determined by its params + the sync watermark. Repeat
# views (revisiting, other devices, re-running a control) then return instantly.
_PD_RESULT_CACHE: dict = {}


def _pd_watermark_token() -> str:
    from isreq_dashboard.db.pd_models import PdSyncState

    with _pd_factory()() as s:
        row = s.get(PdSyncState, "pd_incidents")
    return row.last_sync_at.isoformat() if (row and row.last_sync_at) else "none"


def _pd_cached(cache_key: tuple, compute) -> dict:
    key = (_pd_watermark_token(), cache_key)
    hit = _PD_RESULT_CACHE.get(key)
    if hit is not None:
        return hit
    val = compute()
    if len(_PD_RESULT_CACHE) > 256:  # bound growth (a new watermark invalidates anyway)
        _PD_RESULT_CACHE.clear()
    _PD_RESULT_CACHE[key] = val
    return val


@app.get("/api/pd/overview")
def pd_overview(cadence: Cadence = "weekly", group: PdGroup = "cloud",
                start: str | None = None, end: str | None = None) -> dict:
    from isreq_dashboard.metrics import pd_common
    from isreq_dashboard.metrics.pd_base import alert_select

    cfg = _pd_window_cfg(start, end)
    label = {"cloud": "cloud", "juju_model": "model", "charm": "charm",
             "alertname": "alert type", "region": "region"}[group]

    def compute():
        with _pd_factory()() as s:
            alerts = list(s.scalars(alert_select(cfg)))  # one scan, reused by every section

            def window(_s):
                return _pd_window_section(_s, cfg)

            def headline(_s):
                freq = pd_common.alert_frequency(_s, cfg, alerts=alerts)
                if freq.empty:
                    return _kv("Headline", {"alerts": 0, "note": "no PagerDuty data in this range"})
                top = freq.iloc[0]
                cov = pd_common.classification_coverage(_s, cfg, alerts=alerts)
                return _kv("Headline", {
                    "alerts": int(freq["count"].sum()),
                    "distinct_alert_types": int(freq["alertname"].nunique()),
                    "top_alert": str(top["alertname"]),
                    "top_alert_per_day": round(float(top["per_day"]), 2),
                    "top_alert_share_pct": round(float(top["share"]) * 100, 1),
                    "coverage_cloud_pct": round(cov["cloud"] * 100),
                    "coverage_charm_pct": round(cov["charm"] * 100),
                })

            def most_common(_s):
                df = pd_common.alert_frequency(_s, cfg, alerts=alerts)
                if df.empty:
                    return _table("Most common alerts", df)
                out = df.assign(per_day=df["per_day"].round(2), share_pct=(df["share"] * 100).round(1))
                return _table("Most common alerts", out.drop(columns=["share"]))

            def volume(_s):
                df = pd_common.alert_volume(_s, cfg, cadence, group=group, alerts=alerts)
                return _pivot_chart(f"Alert volume per period by {label}", df, "count",
                                    mark="bar", stacked=True, height=340)

            def totals(_s):
                df = pd_common.dimension_totals(_s, cfg, dimension=group, alerts=alerts)
                return _chart(f"Alerts by {label} (total)", "label", df.head(15),
                              _series(("count", "Alerts", TEAL, "bar")), height=340)

            return {"sections": _build([window, headline, most_common, volume, totals], s, cadence=cadence)}

    return _pd_cached(("overview", cadence, group, start, end), compute)


@app.get("/api/pd/response-times")
def pd_response_times(cadence: Cadence = "weekly",
                      start: str | None = None, end: str | None = None) -> dict:
    from isreq_dashboard.metrics import pd_durations

    cfg = _pd_window_cfg(start, end)

    def window(s):
        return _pd_window_section(s, cfg)

    def per_period(s):
        df = pd_durations.mtta_mttr_by_period(s, cfg, cadence)
        return _chart("MTTA & MTTR per period (mean days)", "period", df,
                      _series(("mtta_days", "MTTA (d)", BLUE, "line"),
                              ("mttr_days", "MTTR (d)", RED, "line")))

    def slowest(s):
        df = pd_durations.slowest_by_alertname(s, cfg)
        if df.empty:
            return _table("Slowest alert types (resolve time)", df)
        out = df.assign(
            median_days=df["median_days"].round(2),
            mean_days=df["mean_days"].round(2),
            sd_days=df["sd_days"].round(2),
            cv=df["cv"].round(2),
        )
        return _table("Slowest alert types (median + mean + SD + CV + count)", out)

    def compute():
        with _pd_factory()() as s:
            return {"sections": _build([window, per_period, slowest], s, cadence=cadence)}

    return _pd_cached(("response-times", cadence, start, end), compute)


@app.get("/api/pd/pareto")
def pd_pareto(dimension: PdDim = "alertname",
              start: str | None = None, end: str | None = None) -> dict:
    from isreq_dashboard.metrics import pd_pareto as par

    cfg = _pd_window_cfg(start, end)

    def window(s):
        return _pd_window_section(s, cfg)

    def chart(s):
        df = par.pareto(s, cfg, dimension=dimension)
        return _chart(f"Pareto by {dimension} (top 30)", "label", df.head(30),
                      _series(("count", "Alerts", BLUE, "bar"),
                              ("cum_share", "Cumulative share", ORANGE, "line")), height=380)

    def table(s):
        df = par.pareto(s, cfg, dimension=dimension)
        if df.empty:
            return _table("Pareto detail", df)
        return _table("Pareto detail", df.assign(cum_share_pct=(df["cum_share"] * 100).round(1)))

    def compute():
        with _pd_factory()() as s:
            return {"sections": _build([window, chart, table], s)}

    return _pd_cached(("pareto", dimension, start, end), compute)


@app.get("/api/pd/on-call")
def pd_on_call(measure: PdMeasure = "handled",
               start: str | None = None, end: str | None = None) -> dict:
    from isreq_dashboard.metrics import pd_sre

    cfg = _pd_window_cfg(start, end)
    label = {"received": "received (paged)", "handled": "handled (actions)",
             "time_spent_hours": "time spent (h)", "handled_pct": "% of alerts handled",
             "time_spent_pct": "% of time spent",
             "disproportion": "disproportion (% time − % alerts)"}[measure]

    def compute():
        with _pd_factory()() as s:
            df = pd_sre.sre_load(s, cfg)  # computed once, shared by chart + table

            def window(_s):
                return _pd_window_section(_s, cfg)

            def chart(_s):
                ranked = df.sort_values(measure, ascending=False).head(20) if not df.empty else df
                return _chart(f"On-call load by {label}", "name", ranked,
                              _series((measure, label.title(), PURPLE, "bar")), height=380)

            def compare(_s):
                # side-by-side % of alerts vs % of time, top 15 by volume — where the
                # time bar towers over the alerts bar, that SRE carries the long incidents.
                top = df.sort_values("handled", ascending=False).head(15) if not df.empty else df
                return _chart("% of alerts vs % of time, per SRE (top 15 by volume)", "name", top,
                              _series(("handled_pct", "% of alerts", BLUE, "bar"),
                                      ("time_spent_pct", "% of time", ORANGE, "bar")), height=400)

            def table(_s):
                # sort the table by the selected measure so outliers (e.g. by
                # disproportion) float to the top
                out = df.sort_values(measure, ascending=False) if not df.empty else df
                return _table("SRE load — counts + each SRE's share of the team (%)", out)

            def time_stats(_s):
                return _table(
                    "Time on alert per engineer (hours) — AVG / SD / CV / p50 / p75 / p95",
                    pd_sre.sre_time_stats(_s, cfg),
                )

            return {"sections": _build([window, chart, compare, table, time_stats], s)}

    return _pd_cached(("on-call", measure, start, end), compute)


# --- React SPA --------------------------------------------------------------
# Serve the built dashboard (frontend/dist) at the root. Mounted LAST so every
# /api/* route and /docs match first; the SPA's relative "/api" calls then hit
# this same origin (no CORS, no rebuild). html=True serves index.html at "/".
# If the build is absent (frontend not yet built), fall back to a JSON index so
# the API is still usable.
_SPA_DIST = ROOT / "frontend" / "dist"
if (_SPA_DIST / "index.html").is_file():
    app.mount("/", StaticFiles(directory=_SPA_DIST, html=True), name="spa")
else:
    @app.get("/")
    def root() -> dict:
        return {
            "service": app.title,
            "version": app.version,
            "endpoints": sorted({r.path for r in app.routes if r.path.startswith("/api/")}),
            "docs": "/docs",
            "note": "frontend/dist not built — run `npm run build` in frontend/ to serve the SPA here",
        }
