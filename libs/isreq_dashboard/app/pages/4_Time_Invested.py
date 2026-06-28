"""Time invested (US6, FR-017/018): worklog seconds per period, bucketed by the
worklog's started date, attributed at issue/area level only — best-effort, no person."""

from __future__ import annotations

import streamlit as st

from isreq_dashboard.app.components.charts import area_drilldown, stacked_bar
from isreq_dashboard.app.components.controls import (
    render_controls, render_freshness, render_scope_jql)
from isreq_dashboard.app.data import get_metric_config, get_period_marks, get_session_factory
from isreq_dashboard.metrics.time_invested import (
    BEST_EFFORT_CAVEAT,
    GROUP_AREA,
    GROUP_ISSUE,
    GROUP_SUB_AREA,
    time_invested_series,
)

st.set_page_config(page_title="ISReq — Time Invested", layout="wide")
st.title("Time invested (best-effort)")
st.warning(BEST_EFFORT_CAVEAT)

cfg = get_metric_config()
factory = get_session_factory()
sel = render_controls()
group = st.sidebar.selectbox(
    "Attribute by", [GROUP_AREA, GROUP_SUB_AREA, GROUP_ISSUE],
    format_func=lambda g: {"area": "Area", "sub_area": "Sub-area", "issue": "Issue"}[g],
)
render_scope_jql(sel)

with factory() as session:
    render_freshness(session)
    marks = get_period_marks(sel.cadence)
    group_for_query = GROUP_SUB_AREA if group == GROUP_AREA else group
    df = time_invested_series(session, cfg, sel, group=group_for_query)
    if df.empty:
        st.info("No worklogs in the synced data yet.")
        st.stop()
    df = df.assign(hours=(df["seconds"] / 3600).round(2))
    if group == GROUP_AREA:
        area_drilldown(df, "hours", marks=marks, key="time_area")
    else:
        stacked_bar(df, "hours", marks=marks)
        st.dataframe(
            df.pivot_table(index="period", columns="group", values="hours", fill_value=0),
            width="stretch",
        )
    st.caption("Hours logged, bucketed by each worklog's *started* date. No per-person breakdown exists (FR-018).")
