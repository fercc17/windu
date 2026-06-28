"""Change Management — maintenance windows: one tab to SEE them, one to DEFINE them.

Local ``chg`` schema (our own data; never created in PagerDuty). The view shows a
timeline + table with live status (Scheduled / Active / Completed) derived from now; the
define tab is a form to create a window, optionally tied to a change request.
"""

from __future__ import annotations

import datetime as dt

import altair as alt
import pandas as pd
import streamlit as st

from isreq_dashboard.app.data import get_chg_session_factory
from isreq_dashboard.changes import store

st.set_page_config(page_title="IS Ops — Maintenance Windows", layout="wide")
st.title("Change Management — maintenance windows")
st.caption("Planned maintenance windows for the IS estate. Local only — referencing PagerDuty "
           "services by name, but never created in PagerDuty (read-only on the source).")

factory = get_chg_session_factory()
NOW = dt.datetime.now(dt.timezone.utc)
_STATUS_COLOR = {"Scheduled": "#2980b9", "Active": "#16a085", "Completed": "#7f8c8d", "Cancelled": "#c0392b"}

view_tab, define_tab = st.tabs(["📅 View windows", "➕ Define a window"])

# --- VIEW -------------------------------------------------------------------
with view_tab:
    with factory() as s:
        windows = store.list_windows(s)
        rows = [{
            "id": w.id, "summary": w.summary, "cr_id": w.cr_id,
            "services": ", ".join(w.services or []), "start_at": w.start_at, "end_at": w.end_at,
            "status": store.window_status(w, NOW), "created_by": w.created_by,
        } for w in windows]

    if not rows:
        st.info("No maintenance windows yet. Use the **Define a window** tab to add one.")
    else:
        df = pd.DataFrame(rows)
        active = int((df["status"] == "Active").sum())
        upcoming = int((df["status"] == "Scheduled").sum())
        c1, c2, c3 = st.columns(3)
        c1.metric("Windows", len(df))
        c2.metric("Active now", active)
        c3.metric("Upcoming", upcoming)

        st.subheader("Timeline")
        order = list(df.sort_values("start_at")["summary"])
        bars = alt.Chart(df).mark_bar(height=16, cornerRadius=3).encode(
            x=alt.X("start_at:T", title=None),
            x2="end_at:T",
            y=alt.Y("summary:N", sort=order, title=None),
            color=alt.Color("status:N",
                            scale=alt.Scale(domain=list(_STATUS_COLOR), range=list(_STATUS_COLOR.values())),
                            legend=alt.Legend(title="Status")),
            tooltip=["summary", "cr_id", "services", "status", "start_at", "end_at"],
        )
        now_rule = alt.Chart(pd.DataFrame({"now": [NOW]})).mark_rule(
            color="#e8590c", strokeDash=[4, 3]).encode(x="now:T")
        st.altair_chart((bars + now_rule).properties(height=max(120, 32 * len(df))), width="stretch")
        st.caption("Orange dashed line = now (UTC).")

        st.subheader("All windows")
        st.dataframe(
            df.rename(columns={"id": "ID", "summary": "Summary", "cr_id": "CR", "services": "Services",
                               "start_at": "Start (UTC)", "end_at": "End (UTC)", "status": "Status",
                               "created_by": "Created by"}),
            hide_index=True, width="stretch",
        )

# --- DEFINE -----------------------------------------------------------------
with define_tab:
    st.markdown("**Define a maintenance window**")
    with factory() as s:
        crs = store.list_crs(s)
    cr_options = ["(none)"] + [f"{c.id} — {c.title}" for c in crs]

    with st.form("new_window", clear_on_submit=True):
        summary = st.text_input("Summary")
        cr_pick = st.selectbox("Linked change request (optional)", cr_options)
        services = st.text_input("Affected services / clouds (comma-separated)",
                                 placeholder="prodstack6, ceph, content-cache")
        col_a, col_b = st.columns(2)
        start_date = col_a.date_input("Start date (UTC)", value=NOW.date())
        start_time = col_a.time_input("Start time (UTC)", value=dt.time(22, 0))
        dur_hours = col_b.number_input("Duration (hours)", min_value=0.5, max_value=72.0, value=2.0, step=0.5)
        created_by = col_b.text_input("Created by")
        description = st.text_area("Description")
        submitted = st.form_submit_button("Create maintenance window")
        if submitted:
            if not summary.strip():
                st.error("A summary is required.")
            else:
                start_at = dt.datetime.combine(start_date, start_time, tzinfo=dt.timezone.utc)
                end_at = start_at + dt.timedelta(hours=float(dur_hours))
                cr_id = None if cr_pick == "(none)" else cr_pick.split(" — ", 1)[0]
                svc = [x.strip() for x in services.split(",") if x.strip()]
                with factory() as s:
                    mw = store.create_window(s, summary=summary.strip(), start_at=start_at, end_at=end_at,
                                             services=svc, cr_id=cr_id,
                                             created_by=created_by.strip() or None,
                                             description=description.strip() or None)
                    s.commit()
                    new_id = mw.id
                st.success(f"Created maintenance window #{new_id} "
                           f"({start_at:%Y-%m-%d %H:%M} → {end_at:%H:%M} UTC)")
                st.rerun()
