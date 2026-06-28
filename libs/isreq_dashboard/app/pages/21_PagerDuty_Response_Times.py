"""PagerDuty — Response times: MTTA/MTTR per period + slowest alert types.

Honest durations: the slow-types table reports median + mean + sample SD + CV AND the
count, so "slow but rare" is never confused with "slow and frequent".
"""

from __future__ import annotations

import streamlit as st

from isreq_dashboard.app.components.charts import series_chart
from isreq_dashboard.app.components.pd_controls import render_pd_controls
from isreq_dashboard.app.data import get_pd_session_factory, get_period_marks
from isreq_dashboard.metrics import pd_durations

st.set_page_config(page_title="IS Ops — PagerDuty Response Times", layout="wide")
st.title("PagerDuty — response times (MTTA / MTTR)")

factory = get_pd_session_factory()
cfg, cadence = render_pd_controls()

with factory() as s:
    per = pd_durations.mtta_mttr_by_period(s, cfg, cadence)
    if per.empty:
        st.info("No PagerDuty incidents synced yet. Run `pd-sync`.")
        st.stop()

    st.subheader("MTTA & MTTR per period (mean, days)")
    wide = (
        per.set_index("period")[["mtta_days", "mttr_days"]]
        .rename(columns={"mtta_days": "MTTA (d)", "mttr_days": "MTTR (d)"})
    )
    series_chart(wide, kind="line", marks=get_period_marks(cadence))
    st.caption("MTTA = trigger → first acknowledge; MTTR = trigger → resolve (incident level).")

    st.subheader("Slowest alert types (by resolve time)")
    st.caption(
        "Median + mean + sample SD + CV **and** count — so a slow-but-rare type is "
        "distinguishable from a slow-and-frequent one. Mixed-type incidents are attributed "
        "to their dominant alert type."
    )
    slow = pd_durations.slowest_by_alertname(s, cfg)
    if slow.empty:
        st.info("No resolved incidents to time yet.")
    else:
        show = slow.copy()
        for col in ("median_days", "mean_days", "sd_days", "cv"):
            show[col] = show[col].round(2)
        show["low_sample"] = show["low_sample"].map({True: "⚠ low n", False: ""})
        st.dataframe(
            show.rename(columns={
                "alertname": "Alert", "count": "Count", "median_days": "Median (d)",
                "mean_days": "Mean (d)", "sd_days": "SD (d)", "cv": "CV", "low_sample": "Note",
            }),
            hide_index=True, width="stretch",
        )
