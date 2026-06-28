"""PagerDuty — On-call load: who received, who handled, who spent the most time.

Three deliberately distinct measures (never conflated): received (paged/assigned),
handled (ack/resolve actions), and time spent (summed ack -> resolve). The count
measures need no constant; time spent is summed straight from the timeline.
"""

from __future__ import annotations

import streamlit as st

from isreq_dashboard.app.components.charts import series_chart
from isreq_dashboard.app.components.pd_controls import render_pd_controls
from isreq_dashboard.app.data import get_pd_session_factory
from isreq_dashboard.metrics import pd_sre

st.set_page_config(page_title="IS Ops — PagerDuty On-call", layout="wide")
st.title("PagerDuty — on-call load (three measures)")
st.caption("Received = paged / assigned · Handled = acknowledge / resolve actions · "
           "Time spent = summed acknowledge → resolve (no interrupt-cost constant).")

factory = get_pd_session_factory()
cfg, _ = render_pd_controls(with_cadence=False)
# Default to "handled": "received" is empty for resolved incidents (PD clears the
# assignee on resolve), so a received-ranked chart would look blank.
MEASURES = {
    "handled": "Handled (actions)",
    "handled_pct": "% of alerts handled",
    "time_spent_hours": "Time spent (h)",
    "time_spent_pct": "% of time spent",
    "disproportion": "Disproportion (% time − % alerts)",
    "received": "Received (paged)",
}
measure = st.sidebar.selectbox("Rank by", list(MEASURES), format_func=lambda m: MEASURES[m])

with factory() as s:
    df = pd_sre.sre_load(s, cfg)
    if df.empty:
        st.info("No PagerDuty incidents synced yet. Run `pd-sync`.")
        st.stop()

    ranked = df.sort_values(measure, ascending=False).reset_index(drop=True)
    st.subheader(f"By {MEASURES[measure].lower()}")
    series_chart(ranked.set_index("name")[[measure]].rename(columns={measure: MEASURES[measure]}), kind="bar")

    st.subheader("% of alerts vs % of time (per SRE, top 15 by volume)")
    st.caption("Where **% time** towers over **% alerts**, that SRE carries the longer-running incidents.")
    top = df.sort_values("handled", ascending=False).head(15)
    series_chart(
        top.set_index("name")[["handled_pct", "time_spent_pct"]].rename(
            columns={"handled_pct": "% alerts", "time_spent_pct": "% time"}),
        kind="bar",
    )

    st.dataframe(
        ranked.rename(columns={
            "name": "SRE", "received": "Received", "handled": "Handled",
            "time_spent_hours": "Time spent (h)", "handled_pct": "% alerts",
            "time_spent_pct": "% time", "disproportion": "Δ time−alerts",
        })[["SRE", "Handled", "% alerts", "Time spent (h)", "% time", "Δ time−alerts", "Received"]],
        hide_index=True, width="stretch",
    )
    st.caption("Received counts current assignment; Handled counts ack/resolve log entries; Time spent attributes "
               "each incident's ack→resolve span to its resolver (else acknowledger).")
    st.info("Note: PagerDuty clears an incident's assignee on resolve, so **Received** is sparse for "
            "historical (resolved) incidents — **Handled** is the more reliable load signal over a backfill. "
            "Capturing the assignee/notified SRE from `assign`/`notify` log entries is a planned refinement.")

    st.divider()
    st.subheader("Time on alert per engineer (hours)")
    st.caption("Distribution of each engineer's per-incident handling time (ack→resolve, attributed to "
               "its resolver else acknowledger): AVG · SD · CV · p50 · p75 · p95, with count and low-sample flag.")
    ts = pd_sre.sre_time_stats(s, cfg)
    if ts.empty:
        st.info("No resolved incidents to time yet.")
    else:
        show = ts.copy()
        show["low_sample"] = show["low_sample"].map({True: "⚠ low n", False: ""})
        st.dataframe(
            show.rename(columns={
                "name": "SRE", "n": "N", "mean_h": "AVG (h)", "sd_h": "SD (h)", "cv": "CV",
                "p50_h": "p50 (h)", "p75_h": "p75 (h)", "p95_h": "p95 (h)", "low_sample": "Note",
            }),
            hide_index=True, width="stretch",
        )
