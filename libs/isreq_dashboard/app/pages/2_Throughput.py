"""Throughput (US3, FR-015) + honest time-to-close statistics (US5, FR-022-024)."""

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
from isreq_dashboard.app.components.drilldown import render_tickets
from isreq_dashboard.app.data import get_metric_config, get_period_marks, get_session_factory
from isreq_dashboard.metrics import drilldown as dd
from isreq_dashboard.metrics.base import GROUP_AREA, GROUP_SUB_AREA
from isreq_dashboard.metrics.throughput import (
    throughput_series,
    time_to_close_by_area,
    time_to_close_percentiles,
    time_to_close_stats,
)

st.set_page_config(page_title="ISReq — Throughput", layout="wide")
st.title("Throughput — close events per period")

cfg = get_metric_config()
factory = get_session_factory()
sel = render_controls()

BREAKDOWN = {"Total": None, "Area": GROUP_AREA, "Sub-area": GROUP_SUB_AREA}
group = BREAKDOWN[st.sidebar.selectbox("Break down by", list(BREAKDOWN), index=0)]
render_scope_jql(sel)


def _fmt_days(seconds: float | None) -> str:
    return "—" if seconds is None else f"{seconds / 86400:.1f} d"


with factory() as session:
    render_freshness(session)
    total = throughput_series(session, cfg, sel)
    if total.empty:
        st.info("No close events in the synced data yet.")
        st.stop()

    marks = get_period_marks(sel.cadence)
    if group is None:
        series_chart(total.set_index("period")[["throughput"]], kind="bar", marks=marks)
    elif group == GROUP_AREA:
        g_sub = throughput_series(session, cfg, sel, group=GROUP_SUB_AREA)
        area_drilldown(g_sub, "throughput", marks=marks, key="tp_area")
    else:
        g = throughput_series(session, cfg, sel, group=group)
        pivot = g.pivot_table(index="period", columns="group", values="throughput", fill_value=0)
        stacked_bar(g, "throughput", marks=marks)
        st.dataframe(pivot, width="stretch")

    st.subheader("Time-to-close (honest statistics)")
    st.caption(
        "**Mean = mean time to resolve a ticket**: the average elapsed time from a ticket's "
        "**creation** to its **close** (entry into Closed/Done/Rejected), in days. Each close "
        "event is measured from the ticket's original creation, so a reopened-then-reclosed "
        "ticket contributes one duration per close. Reflects the current scope / PR-MP filters."
    )
    period = st.selectbox("Period (or All)", ["All", *list(total["period"])])
    stats = time_to_close_stats(session, cfg, sel, None if period == "All" else period)
    if stats.low_sample:
        st.warning(f"Low sample size (n={stats.n}) — interpret with caution.")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Mean", _fmt_days(stats.mean))
    c2.metric("Std dev (sample)", _fmt_days(stats.stddev_sample))
    c3.metric("CV", "—" if stats.cv is None else f"{stats.cv:.2f}")
    c4.metric("n", stats.n)
    st.caption(f"Basis: **{stats.basis}** (n−1 std dev). Mean is never shown without dispersion (Art. III).")

    # Percentiles + histogram (issue #30) — the mean misleads at high CV.
    pc = time_to_close_percentiles(session, cfg, sel, None if period == "All" else period)
    if pc["n"]:
        st.markdown("**Time-to-close percentiles** (more honest than the mean)")
        q1, q2, q3 = st.columns(3)
        q1.metric("p50 (median)", _fmt_days(pc["p50"]))
        q2.metric("p85", _fmt_days(pc["p85"]))
        q3.metric("p95", _fmt_days(pc["p95"]))
        st.caption("Read as '85% of tickets close within p85'. The long p95 tail is what the mean hides.")
        hist = (
            alt.Chart(pd.DataFrame({"days": [d / 86400 for d in pc["durations"]]}))
            .mark_bar()
            .encode(
                x=alt.X("days:Q", bin=alt.Bin(maxbins=40), title="Time to close (days)"),
                y=alt.Y("count():Q", title="Tickets"),
                tooltip=[alt.Tooltip("count():Q", title="Tickets")],
            ).properties(height=240)
        )
        st.altair_chart(hist, width="stretch")

    # Time-to-close by area (issue #34) — which queues are slowest.
    st.markdown("**Time-to-close by area** (slowest first)")
    ba = time_to_close_by_area(session, cfg, sel)
    if ba.empty:
        st.caption("No close events for this selection.")
    else:
        st.dataframe(ba.rename(columns={"group": "Area", "mean_days": "Mean (d)",
                                        "median_days": "Median (d)"}),
                     hide_index=True, width="stretch")

    st.subheader("Drill down — closed in period")
    dperiod = st.selectbox("Period", list(total["period"]), key="drill")
    drill_rows = dd.closed_in_period(session, cfg, sel, dperiod)
    render_tickets(drill_rows, caption=f"Closed in {dperiod}")
    render_drill_jql(sel, cfg, period=dperiod, cadence=sel.cadence, event="closed", rows=drill_rows)
