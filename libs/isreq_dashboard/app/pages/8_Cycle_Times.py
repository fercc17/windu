"""Cycle times (US5, FR-022-025): time-in-status decomposition per period.

For tickets *closed* in a period, the mean time-to-close is split by the status the
time was spent in (contribution-averaged, so segments sum to the mean time-to-close).
Three stacked charts — AVG, SD, CV — share the workflow status legend. A detail
expander keeps the per-duration triage / close / In-Review stats (honest mean + SD +
CV, never a lone mean, Art. III). Durations in days; CV unitless.
"""

from __future__ import annotations

import altair as alt
import streamlit as st

from isreq_dashboard.app.components.charts import stacked_bar
from isreq_dashboard.app.components.controls import (
    render_controls, render_freshness, render_scope_jql)
from isreq_dashboard.app.data import get_metric_config, get_period_marks, get_session_factory
from isreq_dashboard.metrics.cycle_times import (
    KIND_CLOSE,
    KIND_IN_REVIEW,
    KIND_TRIAGE,
    STAT_AVG,
    STAT_CV,
    STAT_SD,
    cycle_time_series,
    status_time_decomposition,
)
from isreq_dashboard.metrics.pickup import pickup_series, pickup_stats

st.set_page_config(page_title="ISReq — Cycle Times", layout="wide")
st.title("Cycle times — time-in-status per period")

cfg = get_metric_config()
factory = get_session_factory()
sel = render_controls()
render_scope_jql(sel)
marks = get_period_marks(sel.cadence)

DAY = 86400.0

st.caption(
    "For tickets **closed** in each period (bucketed by their **first** close), the mean "
    "time-to-close is broken down by the status the time was spent in. Contribution-averaged: "
    "each segment is *total time in that status ÷ tickets closed that period* (0 for tickets "
    "that skipped it), so the **stack height = mean time to first close**. Closed statuses are "
    "the total, not a segment. (The detail table below counts *each* close, so its time-to-close "
    "runs a touch higher where tickets reopen.)"
)


def _to_days(df):
    return df.assign(value=df["value"] / DAY)


with factory() as session:
    render_freshness(session)
    decomp = status_time_decomposition(session, cfg, sel)
    order = decomp["order"]
    if not order or decomp[STAT_AVG].empty:
        st.info("No closed tickets in the synced data for this selection yet.")
        st.stop()

    st.subheader("AVG — time in status (stack height = mean time to first close), days")
    stacked_bar(_to_days(decomp[STAT_AVG]), "value", marks=marks, group_order=order,
                value_title="Days")

    st.subheader("SD — dispersion of time in status, days")
    st.caption(
        "Spread of each status's time across tickets closed that period (incl. 0 for "
        "tickets that skipped it). Segment heights are comparable; the stacked *total* is "
        "not a real quantity — standard deviations don't add."
    )
    stacked_bar(_to_days(decomp[STAT_SD]), "value", marks=marks, group_order=order,
                value_title="Days")

    st.subheader("CV — relative variability of time in status (SD ÷ AVG, unitless)")
    st.caption("Higher = more volatile relative to its own mean. Segments comparable; total not meaningful.")
    stacked_bar(decomp[STAT_CV], "value", marks=marks, group_order=order, value_title="CV (ratio)")

    low = [str(p) for p, n in decomp["cohort"].items() if n < cfg.low_n_threshold]
    if low:
        st.caption(
            f"⚠ Low sample (n < {cfg.low_n_threshold} tickets closed): {', '.join(low)} — interpret with caution."
        )

    st.divider()
    st.subheader("Time to pick up — creation → first logged work")
    st.caption(
        "How long a ticket waits before **any work is logged** on it (issue #16): the gap "
        "from creation to its earliest worklog. Distinct from *time to triage* (a status "
        "change) and *time invested* (total effort). Only tickets that have a worklog can be "
        "measured, so this is gated by logging discipline — **n** is shown."
    )
    overall = pickup_stats(session, cfg, sel)

    def _d(x):
        return "—" if x is None else f"{x / DAY:.2f} d"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Mean wait to pickup", _d(overall.mean))
    c2.metric("Std dev", _d(overall.stddev_sample))
    c3.metric("CV", "—" if overall.cv is None else f"{overall.cv:.2f}")
    c4.metric("n (tickets with worklog)", overall.n)
    if overall.low_sample:
        st.caption(f"⚠ Low sample (n={overall.n}).")
    ps = pickup_series(session, cfg, sel)
    if not ps.empty and int(ps["n"].sum()) > 0:
        st.markdown("**Mean time to pick up, per period (days)**")
        pdays = ps.assign(days=(ps["mean"] / DAY).round(2))
        pickup_chart = (
            alt.Chart(pdays)
            .mark_bar()
            .encode(
                x=alt.X("period:N", sort=list(pdays["period"]), title=None),
                y=alt.Y("days:Q", title="Avg pickup (days)", axis=alt.Axis(format=".2f")),
                tooltip=[
                    alt.Tooltip("period:N", title="Period"),
                    alt.Tooltip("days:Q", title="Avg pickup (days)", format=".2f"),
                    alt.Tooltip("n:Q", title="n"),
                ],
            )
            .properties(height=320)
        )
        st.altair_chart(pickup_chart, width="stretch")
        table = ps.assign(
            **{
                "AVG (days)": (ps["mean"] / DAY).round(2),
                "SD (days)": (ps["stddev"] / DAY).round(2),
                "CV": ps["cv"].round(2),
            }
        ).set_index("period")[["AVG (days)", "SD (days)", "CV", "n"]]
        st.dataframe(table, width="stretch")
    else:
        st.caption("No worklog data for this selection — pickup time can't be measured.")

    with st.expander("Per-duration detail — triage / close / In-Review (AVG · SD · CV per period)"):
        for title, kind, help_text in [
            ("Time to triage", KIND_TRIAGE, f"Creation → first exit from **{cfg.untriaged_status}**."),
            ("Time to close", KIND_CLOSE, "Creation → close (each close counts; reopen→reclose counts twice)."),
            (f"Time in '{cfg.in_review_status}'", KIND_IN_REVIEW,
             f"Each completed **{cfg.in_review_status}** spell (entry → exit)."),
        ]:
            st.markdown(f"**{title}** — {help_text}")
            df = cycle_time_series(session, cfg, sel, kind)
            if df.empty or int(df["n"].sum()) == 0:
                st.caption("No events for this selection.")
                continue
            table = df.assign(
                **{
                    "AVG (days)": (df["mean"] / DAY).round(2),
                    "SD (days)": (df["stddev"] / DAY).round(2),
                    "CV": df["cv"].round(2),
                }
            ).set_index("period")[["AVG (days)", "SD (days)", "CV", "n"]]
            st.dataframe(table, width="stretch")
