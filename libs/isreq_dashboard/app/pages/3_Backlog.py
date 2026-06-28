"""Backlog (US3, FR-016): open tickets at each period end (reopen-aware)."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from isreq_dashboard.app.components.charts import area_drilldown, series_chart, stacked_bar
from isreq_dashboard.app.components.controls import (
    render_controls,
    render_drill_jql,
    render_freshness,
    render_scope_jql,
)
from isreq_dashboard.app.components.drilldown import render_jql, render_tickets
from isreq_dashboard.app.data import (
    backlog_frame,
    get_metric_config,
    get_period_marks,
    get_session_factory,
)
from isreq_dashboard.domain import weeks
from isreq_dashboard.metrics import drilldown as dd
from isreq_dashboard.metrics.backlog import carryover_series, carryover_streaks, wip_series
from isreq_dashboard.metrics.base import GROUP_AREA, GROUP_SUB_AREA, PER_PULSE
from isreq_dashboard.metrics.flow import flow_series

st.set_page_config(page_title="ISReq — Backlog", layout="wide")
st.title("Backlog — open tickets over time")
st.caption(
    "This is the **open stock** at each period end: every ticket created on/before that "
    "point and not yet in a closed status (reopen-aware) — not a per-week cohort. "
    "For 'created this week and carried into next week', see **Weekly carryover** below."
)

cfg = get_metric_config()
factory = get_session_factory()
sel = render_controls()

BREAKDOWN = {"Total": None, "Area": GROUP_AREA, "Sub-area": GROUP_SUB_AREA}
group = BREAKDOWN[st.sidebar.selectbox("Break down by", list(BREAKDOWN), index=0)]
render_scope_jql(sel)

with factory() as session:
    render_freshness(session)
    total = backlog_frame(sel)  # memoized per data version (issue #5)
    if total.empty:
        st.info("No backlog in the synced data yet.")
        st.stop()

    marks = get_period_marks(sel.cadence)
    if group is None:
        series_chart(total.set_index("period")[["backlog"]], kind="line", marks=marks)
        st.dataframe(total, hide_index=True, width="stretch")
    elif group == GROUP_AREA:
        g_sub = backlog_frame(sel, group=GROUP_SUB_AREA)
        area_drilldown(g_sub, "backlog", marks=marks, key="backlog_area")
    else:
        g = backlog_frame(sel, group=group)
        pivot = g.pivot_table(index="period", columns="group", values="backlog", fill_value=0)
        stacked_bar(g, "backlog", marks=marks)
        st.dataframe(pivot, width="stretch")

    st.subheader("Drill down — open at end of period")
    period = st.selectbox("Period", list(total["period"]))
    try:
        t_end = weeks.week_end_utc(cfg.anchor, int(period.lstrip("W")))
        drill_rows = dd.open_at(session, cfg, sel, t_end)
        render_tickets(drill_rows, caption=f"Open at end of {period}")
        render_drill_jql(sel, cfg, period=period, cadence=sel.cadence, event="open_at",
                         rows=drill_rows)
    except ValueError:
        st.info("Drill-down by point-in-time is available on the weekly cadence.")

    st.divider()
    unit = "pulse" if sel.cadence == PER_PULSE else "week"
    st.subheader(f"Carryover — created this {unit}, still open at {unit}-end")
    st.caption(
        f"The creation cohort: tickets **created in a {unit}** that were **not completed "
        f"within it** and so carried into the next {unit} (with the % of that {unit}'s intake). "
        "Per-pulse uses the event-time 2-week window. Distinct from the open-stock chart above "
        "(which counts every unclosed ticket regardless of when it was created)."
    )
    co = carryover_series(session, cfg, sel)
    if co.empty:
        st.info("No intake to evaluate carryover.")
    else:
        series_chart(co.set_index("period")[["created", "carried_over"]], kind="bar", marks=marks)
        st.dataframe(
            co.rename(columns={
                "period": unit.capitalize(), "created": "Created",
                "carried_over": "Carried over", "carried_pct": "Carried %",
            }),
            hide_index=True, width="stretch",
        )

    st.divider()
    st.subheader("Chronic spillover — tickets living across many pulses")
    st.caption(
        "Tickets open from their creation pulse through **2 or more** later pulse boundaries "
        "(resolved or still open). The chronic offenders worth a process fix."
    )
    streaks = carryover_streaks(session, cfg, sel, min_pulses=2)
    if not streaks:
        st.success("None — no ticket spilled across 2+ pulses. ✅")
    else:
        still_open = sum(1 for r in streaks if not r["resolved"])
        st.warning(f"⚠️ {len(streaks)} ticket(s) spanned ≥2 pulses ({still_open} still open).")
        st.dataframe(
            pd.DataFrame([{
                "Ticket": r["key"],
                "Assignee": r["assignee_name"] or "—",
                "Created pulse": r["created_pulse"],
                "Pulses carried": r["pulses_carried"],
                "Status": r["current_status"] or "—",
                "Resolved": "yes" if r["resolved"] else "open",
                "Title": r["title"] or "",
            } for r in streaks]),
            hide_index=True, width="stretch",
        )
        render_jql([r["key"] for r in streaks])

    st.divider()
    st.subheader("Cumulative Flow Diagram — created vs resolved")
    st.caption(
        "Cumulative tickets **created** vs **resolved** over time. The **vertical gap** between "
        "the lines = the open backlog (WIP); the **horizontal gap** ≈ lead time."
    )
    cf = flow_series(session, cfg, sel)
    if cf.empty:
        st.info("No data.")
    else:
        cfd = cf.melt("period", value_vars=["cum_created", "cum_closed"],
                      var_name="series", value_name="count")
        cfd["series"] = cfd["series"].map({"cum_created": "Created", "cum_closed": "Resolved"})
        chart = (
            alt.Chart(cfd).mark_area(opacity=0.55).encode(
                x=alt.X("period:N", sort=list(cf["period"]), title=None),
                y=alt.Y("count:Q", title="Cumulative tickets", stack=None),
                color=alt.Color("series:N", title=None,
                                scale=alt.Scale(domain=["Created", "Resolved"],
                                                range=["#4c78a8", "#54a24b"])),
                tooltip=[alt.Tooltip("period:N", title="Period"),
                         alt.Tooltip("series:N", title="Series"),
                         alt.Tooltip("count:Q", title="Cumulative", format=".0f")],
            ).properties(height=340)
        )
        st.altair_chart(chart, width="stretch")

    st.divider()
    st.subheader("Work in progress (WIP) over time")
    st.caption(
        "Tickets **past triage and not closed** (in active flight) at each week-end — weekly "
        "point-in-time. By Little's Law, lead-time ≈ WIP ÷ throughput."
    )
    wip = wip_series(session, cfg, sel)
    if wip.empty:
        st.info("No data.")
    else:
        series_chart(wip.set_index("period")[["wip"]], kind="line", marks=marks)
