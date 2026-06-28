"""PagerDuty — Pareto: how few alert types / services / models cause most of the pages."""

from __future__ import annotations

import altair as alt
import streamlit as st

from isreq_dashboard.app.components.pd_controls import render_pd_controls
from isreq_dashboard.app.data import get_pd_session_factory
from isreq_dashboard.metrics import pd_pareto

st.set_page_config(page_title="IS Ops — PagerDuty Pareto", layout="wide")
st.title("PagerDuty — Pareto (the noisy few)")

factory = get_pd_session_factory()
cfg, _ = render_pd_controls(with_cadence=False)
DIMS = {pd_pareto.DIM_ALERTNAME: "Alert type", pd_pareto.DIM_SERVICE: "Service", pd_pareto.DIM_MODEL: "Model"}
dim = st.sidebar.selectbox("Dimension", list(DIMS), format_func=lambda d: DIMS[d])

with factory() as s:
    df = pd_pareto.pareto(s, cfg, dimension=dim)
    if df.empty:
        st.info("No PagerDuty alerts synced yet. Run `pd-sync`.")
        st.stop()

    n80 = min(int((df["cum_share"] < 0.8).sum()) + 1, len(df))
    st.metric(f"{DIMS[dim]}s causing 80% of alerts", f"{n80} of {len(df)}")

    top = df.head(30)
    order = list(top["label"])
    bar = alt.Chart(top).mark_bar(color="#4c78a8").encode(
        x=alt.X("label:N", sort=order, title=None),
        y=alt.Y("count:Q", title="Alerts"),
        tooltip=["label", "count", alt.Tooltip("cum_share:Q", format=".0%", title="Σ share")],
    )
    line = alt.Chart(top).mark_line(color="#e8590c", point=True).encode(
        x=alt.X("label:N", sort=order),
        y=alt.Y("cum_share:Q", axis=alt.Axis(format="%", title="Cumulative share")),
    )
    st.altair_chart(
        alt.layer(bar, line).resolve_scale(y="independent").properties(height=400),
        width="stretch",
    )

    show = df.copy()
    show["cum_share"] = (show["cum_share"] * 100).round(1)
    st.dataframe(
        show.rename(columns={"label": DIMS[dim], "count": "Count", "cum_count": "Σ Count", "cum_share": "Σ Share %"}),
        hide_index=True, width="stretch",
    )
