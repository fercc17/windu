"""Intake (US3, FR-014): tickets created per period by area / sub-area / region."""

from __future__ import annotations

import streamlit as st

from isreq_dashboard.app.components.charts import area_drilldown, series_chart, stacked_bar
from isreq_dashboard.app.components.controls import (
    render_controls,
    render_drill_jql,
    render_freshness,
    render_scope_jql,
)
from isreq_dashboard.app.components.drilldown import render_tickets
from isreq_dashboard.app.data import get_metric_config, get_period_marks, get_session_factory
from isreq_dashboard.metrics import drilldown as dd
from isreq_dashboard.metrics.flow import flow_headline, flow_series
from isreq_dashboard.metrics.intake import GROUP_AREA, GROUP_REGION, GROUP_SUB_AREA, intake_series


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    days = seconds / 86400
    return f"{days:.1f} d" if days >= 1 else f"{seconds / 3600:.1f} h"

st.set_page_config(page_title="ISReq — Intake", layout="wide")
st.title("Intake — tickets created per period")

cfg = get_metric_config()
factory = get_session_factory()
sel = render_controls()
group = st.sidebar.selectbox(
    "Break down by", [GROUP_AREA, GROUP_SUB_AREA, GROUP_REGION],
    format_func=lambda g: {"area": "Area", "sub_area": "Sub-area",
                           "region_time_of_day": "Region (creation time-of-day)"}[g],
)
render_scope_jql(sel)

with factory() as session:
    render_freshness(session)
    marks = get_period_marks(sel.cadence)
    if group == GROUP_AREA:
        df_sub = intake_series(session, cfg, sel, group=GROUP_SUB_AREA)
        if df_sub.empty:
            st.info("No intake in the synced data yet.")
            st.stop()
        area_drilldown(df_sub, "count", marks=marks, key="intake_area")
        periods = sorted(df_sub["period"].unique())
    else:
        df = intake_series(session, cfg, sel, group=group)
        if df.empty:
            st.info("No intake in the synced data yet.")
            st.stop()
        stacked_bar(df, "count", marks=marks)
        st.dataframe(
            df.pivot_table(index="period", columns="group", values="count", fill_value=0),
            width="stretch",
        )
        periods = sorted(df["period"].unique())

    st.subheader("Drill down — created in period")
    period = st.selectbox("Period", periods)
    drill_rows = dd.created_in_period(session, cfg, sel, period)
    render_tickets(drill_rows, caption=f"Created in {period}")
    render_drill_jql(sel, cfg, period=period, cadence=sel.cadence, event="created", rows=drill_rows)

    st.divider()
    st.subheader("Creation vs close rate (flow)")
    st.caption(
        "How fast tickets arrive (created) vs how often they're closed, per period and "
        "overall since inception. Created = Intake; closed = Throughput close events. "
        "Respects the sidebar scope / PR-MP filters."
    )
    flow = flow_series(session, cfg, sel)
    head = flow_headline(session, cfg, sel)
    if flow.empty:
        st.info("No flow in the synced data yet.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Created / week", f"{head['created_per_week']:.1f}",
                  help=f"{head['total_created']} created over {head['weeks_span']:.1f} weeks")
        c2.metric("Closed / week", f"{head['closed_per_week']:.1f}",
                  help=f"{head['total_closed']} closed over {head['weeks_span']:.1f} weeks")
        c3.metric("Mean gap between creations", _fmt_duration(head["mean_interarrival_seconds"]))
        c4.metric("Net (created − closed)", f"{head['net']:+d}",
                  help="Positive = backlog grew over the whole window.")

        marks = get_period_marks(sel.cadence)
        st.markdown("**Per period — created vs closed**")
        series_chart(flow.set_index("period")[["created", "closed"]], kind="bar", marks=marks)
        st.markdown("**Cumulative since inception — created vs closed**")
        series_chart(flow.set_index("period")[["cum_created", "cum_closed"]], kind="line",
                     marks=marks)
        st.dataframe(
            flow.rename(
                columns={
                    "period": "Period", "created": "Created", "closed": "Closed", "net": "Net",
                    "cum_created": "Σ Created", "cum_closed": "Σ Closed", "cum_net": "Σ Net",
                }
            ),
            hide_index=True,
            width="stretch",
        )
