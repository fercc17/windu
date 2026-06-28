"""Reports (issue #17): who files the most tickets — overall, Highest, ps5-blocker.

Three bar charts of filing volume by reporter. Counts only — never per-person effort
(Art. VI). Reporter comes from the sync (issue #7).
"""

from __future__ import annotations

import urllib.parse

import altair as alt
import streamlit as st

from isreq_dashboard.app.components.controls import render_freshness
from isreq_dashboard.app.data import get_metric_config, get_session_factory, get_settings
from isreq_dashboard.metrics.reports import (
    top_reporters_all,
    top_reporters_highest,
    top_reporters_ps5,
)

st.set_page_config(page_title="ISReq — Reports", layout="wide")
st.title("Reports — who files the most tickets")
st.caption(
    "Filing volume by **reporter** (who raised the ticket). Counts only — no per-person "
    "effort attribution (Art. VI). Reporter is captured on sync."
)

cfg = get_metric_config()
factory = get_session_factory()
_settings = get_settings()
_BASE = _settings.jira_base_url.rstrip("/")
_PROJ = _settings.toml.project_key
top_n = st.sidebar.slider("Show top N reporters", 5, 40, 15)


def _bar(df, title: str, jql: str) -> None:
    st.subheader(title)
    if df is None or df.empty:
        st.info("No data.")
        return
    chart = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("count:Q", title="Tickets filed",
                    axis=alt.Axis(tickMinStep=1, format="d")),
            y=alt.Y("reporter:N", sort="-x", title=None),
            tooltip=[alt.Tooltip("reporter:N", title="Reporter"),
                     alt.Tooltip("count:Q", title="Tickets")],
        )
        .properties(height=min(60 + 24 * len(df), 760))
    )
    st.altair_chart(chart, width="stretch")
    url = f"{_BASE}/issues/?jql={urllib.parse.quote(jql)}"
    st.caption(f":orange[**⚠️ experimental**] · population behind this chart — "
               f"[open in Jira]({url}); copy the JQL to cross-check:")
    st.code(jql, language="text")


with factory() as session:
    render_freshness(session)
    _bar(top_reporters_all(session, cfg, top_n), "All tickets filed",
         f"project = {_PROJ} ORDER BY created ASC")
    _bar(top_reporters_highest(session, cfg, top_n), "Highest tickets filed",
         f'project = {_PROJ} AND priority WAS "{cfg.highest_priority_name}" ORDER BY created ASC')
    _bar(top_reporters_ps5(session, cfg, top_n), "ps5-blocker tickets filed",
         f'project = {_PROJ} AND labels = "{cfg.ps5_blocker_label}" ORDER BY created ASC')
