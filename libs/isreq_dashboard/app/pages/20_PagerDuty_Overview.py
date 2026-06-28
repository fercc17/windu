"""PagerDuty — Overview: volume, most-common alerts, per cloud/model/region, coverage.

Second analysis, co-hosted with ISReq, reading only the ``pd`` schema (never isreq).
Every view toggles weekly / per-pulse and is splittable by region (trigger time-of-day).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from isreq_dashboard.app.components.charts import stacked_bar
from isreq_dashboard.app.components.pd_controls import render_pd_controls
from isreq_dashboard.app.data import get_pd_session_factory, get_period_marks
from isreq_dashboard.metrics import pd_common

st.set_page_config(page_title="IS Ops — PagerDuty Overview", layout="wide")
st.title("PagerDuty — alert overview")
st.caption("Reactive alert load for the IS SRE team (PagerDuty). A separate analysis from ISReq, "
           "its own `pd` schema, no shared data.")

factory = get_pd_session_factory()
cfg, cadence = render_pd_controls()
GROUPS = {
    "cloud": "Cloud", "juju_model": "Model", "charm": "Charm",
    "alertname": "Alert type", "region": "Region (trigger time)",
}
group = st.sidebar.selectbox("Break down by", list(GROUPS), format_func=lambda g: GROUPS[g])

with factory() as s:
    freq = pd_common.alert_frequency(s, cfg)
    if freq.empty:
        st.info("No PagerDuty alerts synced yet. Run `pd-sync` (fixture) or `pd-sync --full` (live).")
        st.stop()

    cov = pd_common.classification_coverage(s, cfg)
    total = int(freq["count"].sum())
    top = freq.iloc[0]
    c1, c2, c3 = st.columns(3)
    c1.metric("Alerts", f"{total}")
    c2.metric("Distinct alert types", f"{freq['alertname'].nunique()}")
    c3.metric("Top alert share", f"{top['share']:.0%}", help=f"{top['alertname']} — {top['per_day']:.1f}/day")

    st.subheader("Most common alerts")
    st.caption("Count, per-day rate over the data window, and share of all alerts.")
    show = freq.copy()
    show["per_day"] = show["per_day"].round(2)
    show["share"] = (show["share"] * 100).round(1)
    st.dataframe(
        show.rename(columns={"alertname": "Alert", "count": "Count", "per_day": "Per day", "share": "Share %"}),
        hide_index=True, width="stretch",
    )

    st.subheader(f"Volume per period — by {GROUPS[group].lower()}")
    vol = pd_common.alert_volume(s, cfg, cadence, group=group)
    stacked_bar(vol, "count", marks=get_period_marks(cadence), value_title="Alerts")
    st.caption(
        "Coverage of derived fields: "
        + " · ".join(f"{k} {v:.0%}" for k, v in cov.items())
        + ". 'Unknown' = nagios / non-juju alerts; shown honestly, never dropped."
    )

    st.divider()
    st.subheader("Drill down — alerts in period")
    periods = sorted(vol["period"].unique())
    period = st.selectbox("Period", periods)
    rows = pd_common.alerts_in_period(s, cfg, cadence, period)
    st.caption(f"{len(rows)} alerts triggered in {period}")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
