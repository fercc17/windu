"""Region two ways, never conflated (US7, FR-026/027, Art. V).

Left: creation-time-of-day region (from each ticket's creation timestamp via the
EMEA-anchored windows). Right: per-user region (from the static user->region map).
The two are computed by separate functions and labelled distinctly.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from isreq_dashboard.app.components.charts import stacked_bar
from isreq_dashboard.app.components.controls import (
    render_controls, render_freshness, render_scope_jql)
from isreq_dashboard.app.data import get_metric_config, get_period_marks, get_session_factory
from isreq_dashboard.db.models import Issue, User
from isreq_dashboard.domain.regions import region_from_user_map
from isreq_dashboard.metrics.base import _ever_highest_keys, load_scoped_issues
from isreq_dashboard.metrics.intake import GROUP_REGION, intake_series

st.set_page_config(page_title="ISReq — Region", layout="wide")
st.title("Region — two distinct derivations")
st.caption("Reference timezone defaults to EMEA. These derivations are never substituted (Art. V).")

cfg = get_metric_config()
factory = get_session_factory()
sel = render_controls()
render_scope_jql(sel, note="The 'Highest by reporter' section below is Highest-only, "
                          "regardless of this filter.")

with factory() as session:
    render_freshness(session)
    left, right = st.columns(2)

    with left:
        st.subheader("Creation time-of-day")
        st.caption("region_from_timestamp(created_at, windows)")
        df = intake_series(session, cfg, sel, group=GROUP_REGION)
        if df.empty:
            st.info("No data.")
        else:
            # Shared component so this stacked chart honours the per-bar sort (#1) and
            # the sidebar counts/% toggle (#2), unlike the raw st.bar_chart it replaced.
            stacked_bar(df, "count", marks=get_period_marks(sel.cadence))

    with right:
        st.subheader("Per-user (assignee map)")
        st.caption("region_from_user_map(assignee_account_id) — team membership from the user map")
        users = {u.account_id: u for u in session.scalars(select(User))}
        user_region = {aid: u.region for aid, u in users.items()}
        external_ids = {aid for aid, u in users.items() if u.is_external}
        issues = load_scoped_issues(session, cfg, sel)

        exclude_ext = st.checkbox("Exclude external (non-IS-team) assignees", value=True)
        counts: dict[str, int] = {}
        external_total = 0
        unassigned_total = 0
        for i in issues.values():
            aid = i.assignee_account_id
            if aid is None:
                unassigned_total += 1  # no assignee -> not a per-user region
                continue
            if aid in external_ids:
                external_total += 1
                if not exclude_ext:
                    counts["External"] = counts.get("External", 0) + 1
                continue
            region = region_from_user_map(aid, user_region)  # Art. V per-user derivation
            if region == "Unknown":
                region = "Backlog"  # display rebrand (issue #15); stored value unchanged
            counts[region] = counts.get(region, 0) + 1

        if counts:
            st.bar_chart(pd.Series(counts, name="tickets"))
        else:
            st.info("No data.")
        st.caption(
            f"External/non-team: **{external_total}** "
            f"({'excluded' if exclude_ext else 'shown as “External”'}) · "
            f"Unassigned: **{unassigned_total}** (no assignee — always excluded from per-user regions)."
        )

    st.divider()
    st.subheader("Highest tickets by reporter — inside the team vs external")
    st.caption(
        "A **Highest** raised *inside the IS team* usually carries more weight than one "
        "raised by an external requester. Reporter = who raised the ticket; classified via "
        "the user map's team-membership flag (issue #7)."
    )
    users_all = {u.account_id: u for u in session.scalars(select(User))}
    highest_keys = _ever_highest_keys(session, cfg)
    hi_issues = (
        list(session.scalars(select(Issue).where(Issue.key.in_(highest_keys))))
        if highest_keys
        else []
    )
    have_reporter = sum(1 for i in hi_issues if i.reporter_account_id)
    if not hi_issues:
        st.info("No Highest tickets in the synced data.")
    elif have_reporter == 0:
        st.warning(
            "Reporter data isn't populated yet. Apply migration **0003** and run a **sync** "
            "to capture reporters — this internal/external split appears automatically after that."
        )
    else:
        counts = {"Internal (IS team)": 0, "External": 0, "Backlog (no reporter)": 0}
        for i in hi_issues:
            aid = i.reporter_account_id
            if aid is None:
                bucket = "Backlog (no reporter)"
            elif aid in users_all and not users_all[aid].is_external:
                bucket = "Internal (IS team)"
            else:
                bucket = "External"
            counts[bucket] += 1
        c1, c2, c3 = st.columns(3)
        c1.metric("Internal (IS team)", counts["Internal (IS team)"])
        c2.metric("External", counts["External"])
        c3.metric("Backlog (no reporter)", counts["Backlog (no reporter)"])
        st.bar_chart(pd.Series({k: v for k, v in counts.items() if v}, name="Highest tickets"))
        st.caption(
            "Internal-raised Highest tickets are the higher-impact cohort to watch. "
            f"({have_reporter}/{len(hi_issues)} Highest tickets have reporter data.)"
        )
