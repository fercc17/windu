"""Insights — health metrics (issue #12 idea-set) + deprioritized Highest.

Aging, reopen rate, worklog coverage, time-to-triage trend, priority escalation,
deprioritized Highest, intake seasonality, and open-ticket load by region.
"""

from __future__ import annotations

import urllib.parse

import altair as alt
import pandas as pd
import streamlit as st

from isreq_dashboard.app.components.charts import series_chart
from isreq_dashboard.app.components.controls import (
    render_controls, render_freshness, render_scope_jql)
from isreq_dashboard.app.components.drilldown import render_jql
from isreq_dashboard.metrics.base import PR_MP_INCLUDED, SCOPE_ALL, Selector
from isreq_dashboard.app.data import (
    get_metric_config,
    get_period_marks,
    get_session_factory,
    get_settings,
)
from isreq_dashboard.metrics.cycle_times import KIND_TRIAGE, cycle_time_series
from isreq_dashboard.metrics.insights import (
    AGE_BUCKETS,
    PRIORITY_ORDER,
    aging_buckets,
    blocked_analysis,
    deprioritized_highest,
    effort_pareto,
    escalation_breakdown,
    hr_automation_summary,
    intake_seasonality,
    region_load,
    rejection_rate,
    reopen_stats,
    stale_tickets,
    status_churn,
    worklog_coverage,
)

st.set_page_config(page_title="ISReq — Insights", layout="wide")
st.title("Insights — queue health")

cfg = get_metric_config()
factory = get_session_factory()
sel = render_controls()
render_scope_jql(Selector(scope=SCOPE_ALL, pr_mp=PR_MP_INCLUDED),
                 note="Most insights cover **all** tickets; only Worklog-coverage & "
                      "Triage-trend follow the sidebar filter.")
marks = get_period_marks(sel.cadence)
DAY = 86400.0
_BASE = get_settings().jira_base_url.rstrip("/")
_PROJ = get_settings().toml.project_key

with factory() as session:
    render_freshness(session)

    # 1) Aging --------------------------------------------------------------
    st.header("Aging of open tickets")
    st.caption("Open tickets by age since creation, split by current priority. Stale **Highest** "
               "in the older buckets is the thing to watch.")
    aging = aging_buckets(session, cfg)
    if aging.empty:
        st.info("No open tickets.")
    else:
        order = [b for b, _, _ in AGE_BUCKETS]
        chart = (
            alt.Chart(aging).mark_bar().encode(
                x=alt.X("bucket:N", sort=order, title="Age"),
                y=alt.Y("count:Q", title="Open tickets", stack="zero",
                        axis=alt.Axis(tickMinStep=1, format="d")),
                color=alt.Color("priority:N", sort=PRIORITY_ORDER, title="Priority"),
                order=alt.Order("count:Q", sort="descending"),
                tooltip=[alt.Tooltip("bucket:N", title="Age"),
                         alt.Tooltip("priority:N", title="Priority"),
                         alt.Tooltip("count:Q", title="Open")],
            ).properties(height=340)
        )
        st.altair_chart(chart, width="stretch")

    # 2) Reopen rate + 4) escalation + deprioritized headline ---------------
    st.divider()
    st.header("Reopen rate · Highest escalation · Deprioritized Highest")
    rs = reopen_stats(session, cfg)
    esc = escalation_breakdown(session, cfg)
    depr = deprioritized_highest(session, cfg)
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Closed tickets", rs["closed_tickets"])
    c2.metric("Reopened", rs["reopened_tickets"], help="Closed then moved back to an open status.")
    c3.metric("Reopen rate", f"{rs['reopen_pct']}%")
    c4.metric("Born-Highest", esc["born_highest"])
    c5.metric("Escalated → Highest", esc["escalated_to_highest"],
              help=f"Mean {esc['mean_days_to_escalation']} d to escalate (n={esc['n_escalated_timed']})")
    c6.metric("Deprioritized Highest", len(depr), help="Highest later dropped to a lower priority.")

    # 3) Worklog coverage ---------------------------------------------------
    st.divider()
    st.header("Worklog coverage")
    st.caption("Share of resolved tickets with **any** logged work, per period — the honesty "
               "gauge for every effort/forecast metric. Low coverage ⇒ effort numbers understate.")
    cov = worklog_coverage(session, cfg, sel)
    if cov.empty:
        st.info("No resolved tickets.")
    else:
        series_chart(cov.set_index("period")[["coverage_pct"]], kind="line", marks=marks)
        st.dataframe(cov.rename(columns={
            "period": "Period", "closed": "Resolved", "with_worklog": "With worklog",
            "coverage_pct": "Coverage %"}), hide_index=True, width="stretch")

    # 4) Time-to-triage trend ----------------------------------------------
    st.divider()
    st.header("Time-to-triage trend")
    st.caption(f"Mean days from creation to first exit from **{cfg.untriaged_status}**, per period.")
    tr = cycle_time_series(session, cfg, sel, KIND_TRIAGE)
    if tr.empty or int(tr["n"].sum()) == 0:
        st.info("No triage events.")
    else:
        series_chart((tr.assign(days=(tr["mean"] / DAY).round(2)).set_index("period")[["days"]]),
                     kind="bar", marks=marks)

    # 5) Deprioritized Highest detail --------------------------------------
    st.divider()
    st.header("Deprioritized Highest — dropped to a lower priority")
    st.caption("Tickets that held **Highest** and were later moved **down** (from reconstructed "
               "priority history, Art. VII). Click a header to sort.")
    if not depr:
        st.success("None — no Highest ticket was ever deprioritized. ✅")
    else:
        st.warning(f"⚠️ {len(depr)} Highest ticket(s) were deprioritized.")
        ddf = pd.DataFrame([{
            "Ticket": f"{_BASE}/browse/{r['key']}",
            "Assignee": r["assignee_name"] or "—",
            "Dropped to": r["dropped_to"],
            "Current priority": r["current_priority"],
            "Deprioritized on": r["deprioritized_at"].date(),
            "Status": r["current_status"] or "—",
            "Title": r["title"] or "",
        } for r in depr])
        st.dataframe(ddf, hide_index=True, width="stretch",
                     column_config={"Ticket": st.column_config.LinkColumn(
                         "Ticket", display_text=r".*/browse/(.+)$")})
        render_jql([r["key"] for r in depr])

    # 6) Intake seasonality -------------------------------------------------
    st.divider()
    st.header("Intake seasonality")
    st.caption("When tickets are created (UTC) — useful for coverage/region-window planning.")
    hours, dow = intake_seasonality(session, cfg)
    cc1, cc2 = st.columns(2)
    with cc1:
        st.markdown("**By hour of day (UTC)**")
        st.altair_chart(
            alt.Chart(hours).mark_bar().encode(
                x=alt.X("hour:O", title="Hour"),
                y=alt.Y("count:Q", title="Created", axis=alt.Axis(tickMinStep=1, format="d")),
                tooltip=["hour", "count"]).properties(height=280),
            width="stretch")
    with cc2:
        st.markdown("**By day of week**")
        st.altair_chart(
            alt.Chart(dow).mark_bar().encode(
                x=alt.X("day:N", sort=list(dow["day"]), title=None),
                y=alt.Y("count:Q", title="Created", axis=alt.Axis(tickMinStep=1, format="d")),
                tooltip=["day", "count"]).properties(height=280),
            width="stretch")

    # 7) Region load --------------------------------------------------------
    st.divider()
    st.header("Open-ticket load by region (creation time-of-day)")
    st.caption("Open tickets bucketed by the **region of the hour each was created** (same "
               "time-of-day derivation as Intake), i.e. which region's working hours the "
               "still-open work arrived in. Counts only.")
    rl = region_load(session, cfg)
    if rl.empty:
        st.info("No open tickets.")
    else:
        st.bar_chart(rl.set_index("region")["open_tickets"])

    _link = st.column_config.LinkColumn("Ticket", display_text=r".*/browse/(.+)$")

    # 8) Stale / zombie tickets --------------------------------------------
    st.divider()
    st.header("Stale / zombie tickets")
    days = st.slider("Idle threshold (days)", 7, 90, 14)
    st.caption(f"Open tickets with **no** status/priority change and **no** worklog for ≥ {days} days "
               "— the forgotten ones (idle since last touched, not since creation).")
    stale = stale_tickets(session, cfg, days=days)
    if not stale:
        st.success("None — every open ticket has recent activity. ✅")
    else:
        st.warning(f"⚠️ {len(stale)} stale open ticket(s).")
        st.dataframe(pd.DataFrame([{
            "Ticket": f"{_BASE}/browse/{r['key']}", "Days idle": r["days_idle"],
            "Assignee": r["assignee_name"] or "—", "Status": r["current_status"] or "—",
            "Area": r["area"] or "—", "Last activity": r["last_activity"].date(),
            "Title": r["title"] or "",
        } for r in stale]), hide_index=True, width="stretch", column_config={"Ticket": _link})
        render_jql([r["key"] for r in stale])

    # 9) Blocked analysis --------------------------------------------------
    st.divider()
    st.header("Blocked analysis")
    bl = blocked_analysis(session, cfg)
    b1, b2, b3 = st.columns(3)
    b1.metric("Currently blocked", bl["currently_blocked"])
    b2.metric("Mean time blocked",
              "—" if bl["mean_blocked_days"] is None else f"{bl['mean_blocked_days']} d")
    b3.metric("Blocked spells (total)", bl["n_spells"])
    st.caption("Time tickets spend in **BLOCKED**. Longest-blocked first.")
    if bl["tickets"]:
        st.dataframe(pd.DataFrame([{
            "Ticket": f"{_BASE}/browse/{r['key']}", "Blocked (d)": r["blocked_days"],
            "Now blocked": "yes" if r["currently_blocked"] else "—",
            "Assignee": r["assignee_name"] or "—", "Area": r["area"] or "—", "Title": r["title"] or "",
        } for r in bl["tickets"][:60]]), hide_index=True, width="stretch",
            column_config={"Ticket": _link})

    # 10) Rejection rate ---------------------------------------------------
    st.divider()
    st.header("Rejection rate")
    rr = rejection_rate(session, cfg)
    r1, r2, r3 = st.columns(3)
    r1.metric("Resolved", rr["resolved"])
    r2.metric("Rejected", rr["rejected"])
    r3.metric("Rejection rate", f"{rr['rejection_pct']}%")
    st.caption("**Rejected** = triaged away without doing the work. A high rate by area points at "
               "misrouted intake or triage that could be filtered upstream.")
    if not rr["by_area"].empty:
        st.dataframe(rr["by_area"].rename(columns={
            "area": "Area", "rejected": "Rejected", "resolved": "Resolved",
            "rejection_pct": "Rejection %"}), hide_index=True, width="stretch")

    # 11) Status churn -----------------------------------------------------
    st.divider()
    st.header("Status churn (ping-pong)")
    st.caption("Tickets that bounced through **≥6 status transitions** — process friction / rework.")
    churn = status_churn(session, cfg)
    if not churn:
        st.success("None — no excessive status churn. ✅")
    else:
        st.warning(f"⚠️ {len(churn)} ticket(s) with ≥6 status transitions.")
        st.dataframe(pd.DataFrame([{
            "Ticket": f"{_BASE}/browse/{r['key']}", "Transitions": r["transitions"],
            "Status": r["current_status"] or "—", "Area": r["area"] or "—", "Title": r["title"] or "",
        } for r in churn]), hide_index=True, width="stretch", column_config={"Ticket": _link})
        render_jql([r["key"] for r in churn])

    # 12) Effort Pareto ----------------------------------------------------
    st.divider()
    st.header("Effort Pareto (80/20)")
    st.caption("Logged worklog hours by **sub-area**, largest first, with cumulative %. Where effort "
               "concentrates = the best automation / process targets.")
    pareto = effort_pareto(session, cfg)
    if pareto.empty:
        st.info("No worklog data.")
    else:
        top = pareto.head(20).reset_index(drop=True)
        st.altair_chart(
            alt.Chart(top).mark_bar().encode(
                x=alt.X("hours:Q", title="Hours logged"),
                y=alt.Y("group:N", sort="-x", title=None, axis=alt.Axis(labelLimit=400)),
                tooltip=[alt.Tooltip("group:N", title="Area ▸ Sub-area"),
                         alt.Tooltip("hours:Q", format=".1f"),
                         alt.Tooltip("cum_pct:Q", title="Cumulative %", format=".1f")],
            ).properties(height=min(60 + 24 * len(top), 620)),
            width="stretch")
        # Pick a bar's sub-area, then a one-click button opens its effort tickets in a NEW Jira tab.
        labels = [f"{r['group']}  —  {r['hours']}h · {len(r['keys'])} tickets"
                  for _, r in top.iterrows()]
        idx = st.selectbox("Open a sub-area's effort tickets in Jira",
                           range(len(labels)), format_func=lambda i: labels[i], key="pareto_pick")
        chosen = top.iloc[idx]
        ckeys = list(chosen["keys"])
        cjql = f"project = {_PROJ} AND issuekey in ({', '.join(ckeys)}) ORDER BY key ASC"
        st.link_button(f"↗ Open {len(ckeys)} effort tickets in Jira",
                       f"{_BASE}/issues/?jql={urllib.parse.quote(cjql)}", type="primary")
        st.caption(f":orange[**⚠️ experimental**] — opens a new tab. JQL for **{chosen['group']}**:")
        st.code(cjql, language="text")
        st.dataframe(
            top[["group", "hours", "cum_pct"]].assign(tickets=top["keys"].apply(len)).rename(
                columns={"group": "Area ▸ Sub-area", "hours": "Hours",
                         "cum_pct": "Cumulative %", "tickets": "Tickets"}),
            hide_index=True, width="stretch")

    # 13) HR-automation workload -------------------------------------------
    st.divider()
    st.header("HR-automation workload")
    hr = hr_automation_summary(session, cfg)
    h1, h2, h3, h4, h5 = st.columns(5)
    h1.metric("HR-automation tickets", hr["total"])
    h2.metric("Open", hr["open"])
    h3.metric("Resolved", hr["resolved"])
    h4.metric("Hours logged", f"{hr['hours']}")
    h5.metric("Created / week", hr["created_per_week"])
    st.caption("Auto-generated onboarding/offboarding bot tickets (`[private ticket] HR Automation: …`), "
               "excluded from the Data-Quality policy table. High, routine volume → a strong "
               "automation-ROI candidate.")
