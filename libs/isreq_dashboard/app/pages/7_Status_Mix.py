"""Status mix: tickets per pulse/week broken down by current status (stacked bar)."""

from __future__ import annotations

import streamlit as st

from isreq_dashboard.app.components.charts import stacked_bar
from isreq_dashboard.app.components.controls import (
    render_controls, render_freshness, render_scope_jql)
from isreq_dashboard.app.data import get_metric_config, get_period_marks, get_session_factory
from isreq_dashboard.metrics.base import PER_PULSE
from isreq_dashboard.metrics.status_mix import status_mix_series

st.set_page_config(page_title="ISReq — Status Mix", layout="wide")
st.title("Status mix — tickets per period by status")

cfg = get_metric_config()
factory = get_session_factory()
sel = render_controls()
render_scope_jql(sel)
st.caption(
    "Each bar is one period; segments are the tickets' **current** status. "
    + ("Per-pulse: bars are pulses; unsprinted tickets are placed in the pulse where "
       "they **entered their current status**."
       if sel.cadence == PER_PULSE
       else "Weekly: bars are the week each ticket was created.")
)

with factory() as session:
    render_freshness(session)
    df = status_mix_series(session, cfg, sel)
    if df.empty:
        st.info("No tickets in the synced data for this selection.")
        st.stop()

    marks = get_period_marks(sel.cadence)
    stacked_bar(df, "count", marks=marks)
    st.dataframe(
        df.pivot_table(index="period", columns="group", values="count", fill_value=0),
        width="stretch",
    )
