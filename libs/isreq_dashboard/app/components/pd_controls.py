"""Shared PagerDuty sidebar controls: data-window caption + cadence + date range.

Renders once per PD page and returns ``(cfg, cadence)`` — a ``PdMetricConfig`` carrying
the optional [start, end) trigger-time window the user picked, so every metric on the
page is scoped to it. The data extent (from→to) is shown so the user can see the time
lapse the analysis is drawn from.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import replace

import streamlit as st

from isreq_dashboard.app.data import get_pd_metric_config, get_pd_session_factory, pd_last_sync
from isreq_dashboard.metrics.pd_base import PER_PULSE, WEEKLY, data_window


def render_pd_controls(*, with_cadence: bool = True):
    """Return ``(cfg, cadence)``. ``cadence`` is ``WEEKLY`` when ``with_cadence`` is False."""
    base = get_pd_metric_config()
    with get_pd_session_factory()() as s:
        lo, hi = data_window(s)

    cadence = WEEKLY
    if with_cadence:
        cadence = (
            WEEKLY
            if st.sidebar.radio("Cadence", ["Weekly", "Per pulse"], horizontal=True) == "Weekly"
            else PER_PULSE
        )

    cfg = base
    if lo and hi:
        st.sidebar.caption(f"Data covers **{lo:%Y-%m-%d} → {hi:%Y-%m-%d}** (UTC, trigger time)")
        dr = st.sidebar.date_input(
            "Date range", value=(lo.date(), hi.date()),
            min_value=lo.date(), max_value=hi.date(),
            help="Scope every metric on this page to a start–end window.",
        )
        if isinstance(dr, (tuple, list)) and len(dr) == 2:
            start = _dt.datetime.combine(dr[0], _dt.time.min, tzinfo=_dt.timezone.utc)
            end = _dt.datetime.combine(dr[1], _dt.time.min, tzinfo=_dt.timezone.utc) + _dt.timedelta(days=1)
            cfg = replace(base, start=start, end=end)
            if (dr[0], dr[1]) != (lo.date(), hi.date()):
                st.sidebar.caption(f"Showing **{dr[0]:%Y-%m-%d} → {dr[1]:%Y-%m-%d}**")
    else:
        st.sidebar.info("No PagerDuty data synced yet — run `pd-sync`.")

    ts = pd_last_sync()
    st.sidebar.caption(f"Last PD sync: {ts:%Y-%m-%d %H:%M UTC}" if ts else "No PD sync yet.")
    return cfg, cadence
