"""Data quality — anomaly flags. Things that shouldn't occur in the queue."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from isreq_dashboard.app.components.controls import render_freshness
from isreq_dashboard.app.components.drilldown import render_jql, render_tickets
from isreq_dashboard.app.data import get_metric_config, get_session_factory, get_settings
from isreq_dashboard.metrics.anomalies import (
    ordinary_worked_after_cutoff,
    pr_mp_ever_highest,
    unassigned_past_triage,
)

st.set_page_config(page_title="ISReq — Data Quality", layout="wide")
st.title("Data quality — anomalies")

cfg = get_metric_config()
factory = get_session_factory()
_BASE = get_settings().jira_base_url.rstrip("/")


def _ticket_link_col():
    # sortable st.dataframe column that renders the browse URL as the ticket key
    return st.column_config.LinkColumn("Ticket", display_text=r".*/browse/(.+)$")


with factory() as session:
    render_freshness(session)

    st.header("PR/MP-review tickets classified as Highest")
    st.caption(
        "A `[PR/MP Review]` ticket is routine work and should never be Highest priority. "
        "Detected from reconstructed priority history, so tickets later downgraded are still caught."
    )

    rows = pr_mp_ever_highest(session, cfg)
    if not rows:
        st.success("None found — no PR/MP-review ticket has ever been Highest. ✅")
    else:
        still = sum(1 for r in rows if r["current_priority"] == cfg.highest_priority_name)
        st.error(
            f"⚠️ {len(rows)} PR/MP-review ticket(s) were classified as Highest — these shouldn't happen "
            f"({still} still currently Highest)."
        )
        render_tickets(
            rows,
            extra_columns=[
                ("Current priority", "current_priority"),
                ("Current status", "current_status"),
                (
                    "First became Highest",
                    lambda r: r["first_highest_at"].strftime("%Y-%m-%d")
                    if r["first_highest_at"]
                    else "",
                ),
            ],
        )
        render_jql([r["key"] for r in rows],
                   label='JQL ≈ summary ~ "[PR/MP Review]" AND priority WAS "Highest"')

    st.divider()
    st.header("Ordinary tickets worked on after Pulse 9 (policy violation)")
    st.caption(
        "Policy: after the sprint pulse (**Pulse 9, exclusive**) only **Highest / ps5-blocker / "
        "PR-MP** tickets should be worked on. These tickets are **none of those**, yet show "
        "activity after the end of Pulse 9 — a worklog logged, or a status/priority change "
        "(triage counts) — so they shouldn't have been touched at all. *Time after P9* is the "
        "worklog logged past the cutoff (wasted effort). HR-automation tickets are excluded. "
        "Issue-level only (Art. VI). **Click a column header to sort.**"
    )
    off = ordinary_worked_after_cutoff(session, cfg)
    if not off:
        st.success("None found — no ordinary ticket was worked on after Pulse 9. ✅")
    else:
        total_after = sum(r["time_after_seconds"] for r in off) / 3600
        st.warning(
            f"⚠️ {len(off)} ordinary ticket(s) worked on after Pulse 9 — "
            f"**{total_after:.1f} h** logged after the cutoff."
        )
        df = pd.DataFrame(
            [
                {
                    "Ticket": f"{_BASE}/browse/{r['key']}",
                    "Assignee": r["assignee_name"] or "—",
                    "Time after P9 (h)": round(r["time_after_seconds"] / 3600, 1),
                    "Total time (h)": round(r["time_spent_seconds"] / 3600, 1),
                    "Last activity": r["last_activity_at"].date(),
                    "Priority": r["current_priority"] or "—",
                    "Status": r["current_status"] or "—",
                    "Title": r["title"] or "",
                }
                for r in off
            ]
        )
        st.dataframe(
            df, hide_index=True, width="stretch",
            column_config={"Ticket": _ticket_link_col()},
        )
        render_jql([r["key"] for r in off])

    st.divider()
    st.header("Past triage but unassigned")
    st.caption(
        "Tickets that have **moved past triage** (status is neither Untriaged nor Triaged) yet "
        "have **no assignee** — work in flight without an owner. Closed statuses are hidden by "
        "default; add them via the filter. **Click a column header to sort.**"
    )
    ua = unassigned_past_triage(session, cfg)
    if not ua:
        st.success("None found — every past-triage ticket has an assignee. ✅")
    else:
        statuses = sorted({r["current_status"] or "—" for r in ua})
        closed = set(cfg.closed_statuses)
        default = [s for s in statuses if s not in closed] or statuses
        pick = st.multiselect("Status", statuses, default=default)
        shown = [r for r in ua if (r["current_status"] or "—") in pick]
        st.warning(f"⚠️ {len(shown)} unassigned ticket(s) past triage (of {len(ua)} total).")
        df2 = pd.DataFrame(
            [
                {
                    "Ticket": f"{_BASE}/browse/{r['key']}",
                    "Status": r["current_status"] or "—",
                    "Area": r["area"] or "—",
                    "Created": r["created_at"].date(),
                    "Title": r["title"] or "",
                }
                for r in shown
            ]
        )
        st.dataframe(
            df2, hide_index=True, width="stretch",
            column_config={"Ticket": _ticket_link_col()},
        )
        render_jql([r["key"] for r in shown])
