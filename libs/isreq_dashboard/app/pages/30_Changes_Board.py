"""Change Management — board of change requests across every lifecycle stage.

Reads/writes the local ``chg`` schema (our own data; never Jira/PagerDuty). Shows the
CRs as a kanban across the ITIL stages, a filterable detail table, and a form to raise a
new CR (Normal CR# / Standard sCR# / Emergency eCR#, auto-numbered per type).
"""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from isreq_dashboard.app.data import get_chg_session_factory
from isreq_dashboard.changes import store
from isreq_dashboard.domain import changes as ch

st.set_page_config(page_title="IS Ops — Change Board", layout="wide")
st.title("Change Management — change requests")
st.caption("Local change requests across the ITIL lifecycle. Normal **CR#**, Standard **sCR#**, "
           "Emergency **eCR#** (auto-numbered per type). Own `chg` schema; no writes to Jira/PagerDuty.")

factory = get_chg_session_factory()

# --- filters ----------------------------------------------------------------
type_choice = st.sidebar.selectbox(
    "Change type", ["All", *ch.CHANGE_TYPES], format_func=lambda t: "All types" if t == "All" else ch.TYPE_LABEL[t])
type_filter = None if type_choice == "All" else type_choice

with factory() as s:
    crs = store.list_crs(s, change_type=type_filter)

if not crs:
    st.info("No change requests yet.")
    if st.button("Load sample data (one CR per stage + windows)"):
        with factory() as s:
            store.seed_samples(s)
        st.rerun()
    st.stop()

# --- headline ---------------------------------------------------------------
open_n = sum(1 for c in crs if ch.is_open(c.stage))
by_type = {t: sum(1 for c in crs if c.change_type == t) for t in ch.CHANGE_TYPES}
c1, c2, c3, c4 = st.columns(4)
c1.metric("Change requests", len(crs))
c2.metric("Open", open_n)
c3.metric("Normal · Standard · Emergency",
          f"{by_type['normal']} · {by_type['standard']} · {by_type['emergency']}")
c4.metric("Closed", sum(1 for c in crs if c.stage == ch.CLOSED))


def _badge(c) -> str:
    color = ch.STAGE_COLOR.get(c.stage, "#7f8c8d")
    risk = f" · {html.escape(c.risk)}" if c.risk else ""
    return (
        f"<div style='border-left:4px solid {color};padding:4px 8px;margin:4px 0;"
        f"background:rgba(127,127,127,0.08);border-radius:3px'>"
        f"<b>{html.escape(c.id)}</b> <span style='font-size:0.8em;opacity:0.7'>"
        f"{ch.TYPE_LABEL[c.change_type]}{risk}</span><br>"
        f"<span style='font-size:0.85em'>{html.escape(c.title)}</span></div>"
    )


# --- kanban across the happy-path stages ------------------------------------
st.subheader("Board")
cols = st.columns(len(ch.HAPPY_PATH))
for col, stage in zip(cols, ch.HAPPY_PATH):
    in_stage = [c for c in crs if c.stage == stage]
    col.markdown(
        f"<div style='font-weight:600;font-size:0.8em;text-transform:uppercase;"
        f"color:{ch.STAGE_COLOR[stage]}'>{stage} ({len(in_stage)})</div>",
        unsafe_allow_html=True,
    )
    for c in in_stage:
        col.markdown(_badge(c), unsafe_allow_html=True)

off = [c for c in crs if c.stage in ch.TERMINAL_OFF]
if off:
    st.caption("Off-flow: " + " · ".join(f"{c.id} ({c.stage})" for c in off))

# --- detail table -----------------------------------------------------------
st.subheader("All change requests")
rows = [{
    "ID": c.id, "Type": ch.TYPE_LABEL[c.change_type], "Stage": c.stage, "Risk": c.risk,
    "Title": c.title, "Service": c.service, "Assignee": c.assignee,
    "Scheduled start": c.scheduled_start, "Closure": c.closure_code,
} for c in crs]
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

# --- create a new CR --------------------------------------------------------
with st.expander("➕ Raise a new change request"):
    with st.form("new_cr", clear_on_submit=True):
        ct = st.selectbox("Change type", ch.CHANGE_TYPES, format_func=lambda t: ch.TYPE_LABEL[t])
        title = st.text_input("Title")
        col_a, col_b = st.columns(2)
        risk = col_a.selectbox("Risk", ch.RISK_LEVELS, index=0)
        # offer only the stages valid for the chosen type's flow
        stage = col_b.selectbox("Initial stage", ch.FLOW[ct], index=0)
        service = col_a.text_input("Affected service / cloud")
        assignee = col_b.text_input("Assignee")
        description = st.text_area("Description")
        submitted = st.form_submit_button("Create change request")
        if submitted:
            if not title.strip():
                st.error("A title is required.")
            else:
                with factory() as s:
                    cr = store.create_cr(s, change_type=ct, title=title.strip(), stage=stage,
                                         risk=risk, service=service.strip() or None,
                                         assignee=assignee.strip() or None,
                                         description=description.strip() or None)
                    s.commit()
                    new_id = cr.id
                st.success(f"Created {new_id}")
                st.rerun()
