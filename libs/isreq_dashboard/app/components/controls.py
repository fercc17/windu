"""Shared controls: freshness banner (FR-005/SC-008) + cadence/scope/PR-MP selectors
(FR-010/013/028). Rendered on every view so the metric definitions stay identical."""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import urllib.parse

import streamlit as st

from isreq_dashboard.app.components.theme import inject_pragma_theme
from isreq_dashboard.domain import weeks as _weeks
from isreq_dashboard.metrics.base import (
    MetricConfig,
    PER_PULSE,
    PR_MP_EXCLUDED,
    PR_MP_INCLUDED,
    PR_MP_ONLY,
    SCOPE_ALL,
    SCOPE_HIGHEST,
    SCOPE_HIGHEST_OR_PS5,
    SCOPE_PS5,
    WEEKLY,
    Selector,
    last_sync_at,
)


def _run_incremental_sync() -> None:
    """Trigger an **incremental** sync of BOTH sources (Jira + PagerDuty) out-of-process.

    Runs each sync CLI as a subprocess (never imports the source libraries into the app —
    keeps render-isolation / Art. X intact). On success, clears the cached metric frames
    and reruns so the fresh data shows immediately.
    """
    root = pathlib.Path(__file__).resolve().parents[4]  # repo root
    sources = (
        ("isreq_dashboard.cli.sync_main", "Jira", r"sync complete: (\d+) issues"),
        ("isreq_dashboard.cli.pd_sync_main", "PagerDuty", r"pd sync complete: (\d+) incidents"),
    )
    results = []
    with st.spinner("Fetching latest from Jira + PagerDuty (incremental since last fetch)…"):
        for module, label, pat in sources:
            proc = subprocess.run(
                [sys.executable, "-m", module],
                cwd=str(root), capture_output=True, text=True, timeout=1800,
            )
            m = re.search(pat, f"{proc.stdout or ''}\n{proc.stderr or ''}")
            results.append((label, proc.returncode == 0, int(m.group(1)) if m else None, proc))

    # persist a per-source summary so it survives the rerun (shown in render_freshness)
    st.session_state["last_fetch"] = " · ".join(
        f"{label} {'✓' if ok else '✗'}{f' {count}' if count is not None else ''}"
        for label, ok, count, _ in results
    )
    failures = [(label, proc) for label, ok, _, proc in results if not ok]
    if not failures:
        st.cache_data.clear()
        st.rerun()
    else:
        for label, proc in failures:
            st.error(f"{label} sync failed (exit {proc.returncode}).")
            st.code((proc.stderr or proc.stdout or "no output")[-2000:], language="text")


def render_freshness(session) -> None:
    """Surface the last successful sync time + a manual incremental fetch (I-5, SC-008)."""
    inject_pragma_theme()  # apply the Pragma/Canonical CSS on every page (issue #40)
    ts = last_sync_at(session, "issues")
    if ts is None:
        st.warning("No sync recorded yet — run the sync job to populate data.")
    else:
        st.caption(f"Data freshness — last successful sync: **{ts:%Y-%m-%d %H:%M UTC}**")
    # Prominent, always-visible fetch control in the sidebar (issue #26).
    st.sidebar.divider()
    if st.sidebar.button("🔄 Fetch data", key="fetch_latest",
                         use_container_width=True,
                         help="Incremental sync of Jira + PagerDuty — pulls only changes since the last fetch."):
        _run_incremental_sync()
    if ts is not None:
        st.sidebar.caption(f"Last sync: {ts:%Y-%m-%d %H:%M UTC}")
    if st.session_state.get("last_fetch"):
        st.sidebar.caption(f"Last fetch: {st.session_state['last_fetch']}")


def render_controls(default_pr_mp: str = PR_MP_INCLUDED) -> Selector:
    """Render the cadence/scope/PR-MP toggles in the sidebar; return a Selector."""
    st.sidebar.header("View controls")
    # Base presentation is per-pulse (Jira sprint / "IS Pulse"); Week is the toggle.
    cadence = st.sidebar.radio(
        "Cadence",
        [PER_PULSE, WEEKLY],
        format_func=lambda c: "IS Pulse (sprint)" if c == PER_PULSE else "Week",
    )
    st.sidebar.markdown("**Scope**")
    cb_highest = st.sidebar.checkbox("ISReq Highest", value=False)
    cb_ps5 = st.sidebar.checkbox("ps5-blocker", value=False)
    # Each box is a filter; unchecking both = no filter = every ISReq ticket. Both = union.
    if cb_highest and cb_ps5:
        scope = SCOPE_HIGHEST_OR_PS5
    elif cb_highest:
        scope = SCOPE_HIGHEST
    elif cb_ps5:
        scope = SCOPE_PS5
    else:
        scope = SCOPE_ALL
    st.sidebar.caption(
        {
            SCOPE_ALL: "Showing **all ISReq tickets**",
            SCOPE_HIGHEST: "Showing **ISReq Highest** tickets",
            SCOPE_PS5: "Showing **ps5-blocker** tickets",
            SCOPE_HIGHEST_OR_PS5: "Showing **Highest ∪ ps5-blocker**",
        }[scope]
    )
    pr_mp = st.sidebar.radio(
        "PR/MP-review tickets",
        [PR_MP_INCLUDED, PR_MP_EXCLUDED, PR_MP_ONLY],
        index=[PR_MP_INCLUDED, PR_MP_EXCLUDED, PR_MP_ONLY].index(default_pr_mp),
        format_func=lambda v: {"included": "Included", "excluded": "Excluded",
                               "only": "Only PR/MP"}[v],
    )
    # Display: stacked bars as absolute counts or 100%-normalised share (issue #2).
    # Stored under the `stack_as_pct` key, read by charts.stacked_bar across every page.
    st.sidebar.markdown("**Display**")
    st.sidebar.toggle("Stacked bars as %", value=False, key="stack_as_pct",
                      help="Show each stacked bar as 100%-normalised shares instead of counts.")
    return Selector(cadence=cadence, scope=scope, pr_mp=pr_mp)


def scope_jql(sel: Selector, toml, *, extra_clauses: tuple[str, ...] = ()) -> str:
    """Best-effort Jira JQL for the ticket population behind a view (issue #25).

    Highest uses ``priority WAS`` (ever-Highest, matching the dashboard's history logic);
    PR/MP uses a summary match. ``extra_clauses`` add page-intrinsic constraints (e.g. the
    Home north-star is always Highest). ISREQ-1 (pre-inception) is excluded from the
    dashboard but not encodable in JQL — noted in the UI."""
    hp = toml.highest_priority_name
    clauses = [f"project = {toml.project_key}"]
    if sel.scope == SCOPE_HIGHEST:
        clauses.append(f'priority WAS "{hp}"')
    elif sel.scope == SCOPE_PS5:
        clauses.append(f'labels = "{toml.ps5_blocker_label}"')
    elif sel.scope == SCOPE_HIGHEST_OR_PS5:
        clauses.append(f'(priority WAS "{hp}" OR labels = "{toml.ps5_blocker_label}")')
    if sel.pr_mp == PR_MP_ONLY:
        clauses.append(f'summary ~ "{toml.pr_mp_title_substring}"')
    elif sel.pr_mp == PR_MP_EXCLUDED:
        clauses.append(f'summary !~ "{toml.pr_mp_title_substring}"')
    for c in extra_clauses:
        if c not in clauses:
            clauses.append(c)
    # The dashboard excludes pre-inception tickets — encode it as a created lower bound,
    # unless a drill already constrains `created` to a tighter window.
    if not any("created" in c for c in clauses):
        clauses.append(f'created >= "{toml.anchor_date:%Y-%m-%d}"')
    return " AND ".join(clauses) + " ORDER BY created ASC"


def render_scope_jql(sel: Selector, *, extra_clauses: tuple[str, ...] = (),
                     note: str | None = None) -> None:
    """A 'cross-check in Jira' expander reflecting what THIS page actually charts (issue #25).

    Pages pass the scope they really use: most use the sidebar ``sel``; Home adds
    ``priority WAS Highest`` (north-star); Insights forces all-scope; etc."""
    from isreq_dashboard.app.data import get_settings

    settings = get_settings()
    jql = scope_jql(sel, settings.toml, extra_clauses=extra_clauses)
    url = f"{settings.jira_base_url.rstrip('/')}/issues/?jql={urllib.parse.quote(jql)}"
    with st.expander("🔎 Cross-check this view in Jira (JQL) — ⚠️ experimental", expanded=True):
        st.caption(
            ":orange[**⚠️ Experimental — verify before relying on it.**] "
            "The ticket **population** behind this page's charts. JQL only *filters* tickets — it "
            "has **no GROUP BY**; the per-period / per-area breakdown is aggregation the dashboard "
            "does on this set. "
            + (f"{note} " if note else "")
            + f"[Open in Jira]({url}). It **updates live** with the sidebar **scope** and **PR/MP** "
            "filters (e.g. tick *ISReq Highest* → `priority WAS \"Highest\"`); cadence and *break "
            "down by* are aggregation, not filters, so they don't appear. `created >= …` encodes "
            "the pre-inception exclusion. To break it down in Jira, group the navigator by ‘Request "
            "area’ or use a **Two-Dimensional Filter Statistics** gadget. Drill-downs give a "
            "period-specific JQL."
        )
        st.code(jql, language="text")


def period_window(period: str | None, cadence: str, anchor):
    """``(start, end)`` UTC datetimes for a week/pulse period label, or ``None`` if undatable
    (e.g. the unsprinted 'Backlog' bucket). Pulses map to their 2-week calendar window."""
    try:
        if cadence == PER_PULSE:
            m = re.search(r"(\d+)\s*$", period or "")
            if not m:
                return None
            first, last = _weeks.pulse_window(int(m.group(1)))
            return _weeks.week_end_utc(anchor, first - 1), _weeks.week_end_utc(anchor, last)
        if period and period.startswith("W") and period[1:].isdigit():
            w = int(period[1:])
            return _weeks.week_end_utc(anchor, w - 1), _weeks.week_end_utc(anchor, w)
    except Exception:
        return None
    return None


def render_drill_jql(sel: Selector, cfg: MetricConfig, *, period: str, cadence: str,
                     event: str, rows: list[dict]) -> None:
    """Semantic JQL for a per-period drill-down (issue #25): the **period as a date window**
    + scope/PR-MP + the event clause. Falls back to the exact key list if the period isn't
    datable (e.g. unsprinted). Jira can't query pulses, so pulses use their calendar dates."""
    win = period_window(period, cadence, cfg.anchor)
    if win is None:
        from isreq_dashboard.app.components.drilldown import render_jql
        render_jql([r["key"] for r in rows], label=f"{period}: exact tickets (undatable period)")
        return
    s, e = win
    sd, ed = f"{s:%Y-%m-%d %H:%M}", f"{e:%Y-%m-%d %H:%M}"
    closed = ", ".join(f'"{c}"' for c in cfg.closed_statuses)
    hp = cfg.highest_priority_name
    by_event = {
        "created": (f'created >= "{sd}" AND created < "{ed}"',),
        "closed": (f'status CHANGED TO ({closed}) DURING ("{sd}", "{ed}")',),
        "became_highest": (f'priority CHANGED TO "{hp}" DURING ("{sd}", "{ed}")',),
        "highest_closed": (f'priority WAS "{hp}"',
                           f'status CHANGED TO ({closed}) DURING ("{sd}", "{ed}")'),
        "open_at": (f'created <= "{ed}"', f'status WAS NOT ({closed}) ON "{ed}"'),
        "highest_open_at": (f'created <= "{ed}"', f'priority WAS "{hp}" ON "{ed}"',
                            f'status WAS NOT ({closed}) ON "{ed}"'),
    }
    render_scope_jql(sel, extra_clauses=by_event.get(event, ()),
                     note=f"{event.replace('_', ' ').title()} in {period} ({sd[:10]} → {ed[:10]}).")
