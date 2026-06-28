"""North-star landing view (US1, FR-006/009, Art. I): Highest create-rate vs
close-rate per period + the cumulative Highest backlog, with drill-down (US2)."""

from __future__ import annotations

import streamlit as st

from isreq_dashboard.app.components.charts import series_chart
from isreq_dashboard.app.components.controls import (
    render_controls,
    render_drill_jql,
    render_freshness,
    render_scope_jql,
)
from isreq_dashboard.app.components.drilldown import render_tickets
from isreq_dashboard.app.data import get_metric_config, get_period_marks, get_session_factory
from isreq_dashboard.metrics import drilldown as dd
from isreq_dashboard.metrics.base import PER_PULSE
from isreq_dashboard.metrics.highest import highest_series

st.set_page_config(page_title="IS Operations Analytics", layout="wide")
st.caption(
    "**IS Operations Analytics** — one console: **ISReq** (planned request work; pages 1–11), "
    "**PagerDuty** (reactive alert load; pages 20–23), and **Change Management** (change requests "
    "+ maintenance windows; pages 30–31). Independent data, shared platform — no cross-join."
)
st.title("Is Highest intake outpacing Highest closure?")

cfg = get_metric_config()
factory = get_session_factory()
sel = render_controls()
marks = get_period_marks(sel.cadence)
render_scope_jql(sel, extra_clauses=(f'priority WAS "{cfg.highest_priority_name}"',),
                 note="This north-star is about **Highest** tickets (became vs closed).")

with factory() as session:
    render_freshness(session)
    frame = highest_series(session, cfg, sel)

    if frame.empty:
        st.info("No Highest activity in the synced data yet.")
        st.stop()

    st.subheader("Became Highest vs Highest closed, per period")
    series_chart(frame.set_index("period")[["became_highest", "highest_closed"]],
                 kind="bar", marks=marks)

    if sel.cadence == PER_PULSE:
        st.subheader("Open Highest backlog by sprint (currently Highest & open)")
        series_chart(frame.set_index("period")[["highest_backlog"]], kind="bar", marks=marks)
    else:
        st.subheader("Cumulative Highest backlog over weeks (became − exits)")
        series_chart(frame.set_index("period")[["highest_backlog"]], kind="line", marks=marks)

    st.subheader("Drill down")
    period = st.selectbox("Period", list(frame["period"]))
    kinds = ["Became Highest", "Highest closed"]
    if sel.cadence != PER_PULSE:
        kinds.append("Highest open at end")  # point-in-time, weekly only
    kind = st.radio("Show", kinds, horizontal=True)
    if kind == "Became Highest":
        rows = dd.became_highest_in_period(session, cfg, sel, period)
    elif kind == "Highest closed":
        rows = dd.closed_in_period(session, cfg, sel, period)
    else:
        from isreq_dashboard.domain import weeks

        try:
            t_end = weeks.week_end_utc(cfg.anchor, int(period.lstrip("W")))
            rows = dd.highest_open_at(session, cfg, sel, t_end)
        except ValueError:
            rows = []
    render_tickets(rows, caption=f"{kind} — {period}")
    _event = {"Became Highest": "became_highest", "Highest closed": "highest_closed",
              "Highest open at end": "highest_open_at"}[kind]
    render_drill_jql(sel, cfg, period=period, cadence=sel.cadence, event=_event, rows=rows)
