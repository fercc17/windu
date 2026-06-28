"""Predictions & forecasts (issue #11).

Transparent, assumption-stated forecasts built on the audited metric definitions:
PR/MP load, backlog clear-time scenarios, ps5-blocker cadence, priority analysis, and
automation targets. Nothing here is a black box — every figure is a historical mean or
a stated ratio.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from isreq_dashboard.app.components.controls import (
    render_controls, render_freshness, render_scope_jql)
from isreq_dashboard.app.data import (
    backlog_baseline_frame,
    burndown_frame,
    get_metric_config,
    get_session_factory,
)
from isreq_dashboard.metrics.predictions import (
    PRIORITY_ORDER,
    automation_targets,
    pr_mp_forecast,
    priority_breakdown,
    ps5_blocker_stats,
    time_to_close_percentiles_by_priority,
)

st.set_page_config(page_title="ISReq — Predictions", layout="wide")
st.title("Predictions & forecasts")
st.caption(
    "Simple, reproducible forecasts from historical averages — assumptions are stated "
    "inline. Treat as planning aids, not guarantees."
)

cfg = get_metric_config()
factory = get_session_factory()
sel = render_controls()  # standard sidebar "View controls" (cadence / scope / PR-MP)
render_scope_jql(sel, note="§2 backlog, §4 priority & §5 automation use this filter; "
                          "§1 is PR/MP-only and §3 is ps5-only by definition.")


def _days(x: float | None) -> str:
    return "—" if x is None else f"{x:.1f} d"


with factory() as session:
    render_freshness(session)

    # The sidebar View controls (scope / PR-MP) drive every filterable section below.
    bsel = sel
    st.caption(
        "Filtered by the sidebar **View controls** (scope · PR/MP · cadence). §1 is always "
        "PR/MP-scoped and §3 always ps5-blocker-scoped, so they honour the other dimension only."
    )

    # 1) PR/MP forecast ------------------------------------------------------
    st.header("1 · PR/MP load forecast")
    f = pr_mp_forecast(session, cfg, sel.cadence, scope=sel.scope)
    c1, c2, c3 = st.columns(3)
    c1.metric(f"Expected PR/MP / {f['unit']}", f"{f['forecast_count']:.1f}")
    c2.metric("Mean effort / ticket", f"{f['mean_hours_per_ticket']:.1f} h")
    c3.metric(f"Expected effort / {f['unit']}", f"{f['forecast_hours']:.1f} h")
    st.caption(
        f"Based on {f['total_pr_mp']} PR/MP tickets across {f['n_periods']} {f['unit']}(s); "
        f"effort from {f['n_with_worklog']} with logged work. "
        f"Mean time-to-close: {_days(f['mean_ttc_days'])}. "
        "Forecast = historical mean PR/MP per period × mean logged hours/ticket."
    )

    # 2) Interactive staffing / automation what-if --------------------------
    st.divider()
    st.header("2 · How long to clear the open backlog?")
    base = backlog_baseline_frame(bsel)  # cached: backlog + intake/close per week
    backlog, intake, close = base["backlog"], base["intake_per_week"], base["close_per_week"]

    b1, b2, b3 = st.columns(3)
    b1.metric("Open backlog now", backlog)
    b2.metric("Intake / week", f"{intake:.1f}")
    b3.metric("Closes / week (today)", f"{close:.1f}")

    st.markdown("**What-if — every input moves the result below**")
    i1, i2, i3 = st.columns(3)
    regions = i1.number_input("Regions", 1, 6, 3)
    base_ppr = i2.number_input(
        "People/region today", 1, 20, 2,
        help="How many people per region produce today's close rate — calibrates per-person throughput.")
    autom = i3.slider("Automate … % of intake", 0, 100, 40,
                      help="Share of intake handled automatically (public clouds + PR/MP).") / 100

    current_staff = max(regions * int(base_ppr), 1)
    per_person = close / current_staff  # tickets/week/person (today's productivity)

    scn_ppr = st.slider("People/region **working tickets** in the scenario", 1, 12, int(base_ppr),
                        help="Move this to see staffing impact. = today's value reproduces the status quo.")
    scn_close = per_person * regions * scn_ppr
    scn_intake = intake * (1 - autom)
    net = scn_close - scn_intake
    weeks = (backlog / net) if net > 0 else None

    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Scenario closes/wk", f"{scn_close:.1f}", delta=f"{scn_close - close:+.1f} vs today")
    r2.metric("Scenario intake/wk", f"{scn_intake:.1f}", delta=f"{scn_intake - intake:+.1f} vs today",
              delta_color="inverse")
    r3.metric("Net / week", f"{net:+.1f}")
    r4.metric("Time to clear", "never — grows" if weeks is None else f"{weeks:.1f} wks")
    st.caption(
        f"Per-person throughput ≈ **{per_person:.2f} tickets/week** (today's {close:.1f}/wk ÷ "
        f"{current_staff} people). Scenario = **{regions * scn_ppr} people** on tickets + "
        f"**{int(autom * 100)}%** automation. Net = closes − intake; ‘never’ = intake ≥ closes. "
        "Rates are whole-history averages — adjust the calibration to match reality."
    )

    # Sensitivity curve: weeks-to-clear vs people/region, at the chosen automation.
    sens = pd.DataFrame([
        {"People/region": p,
         "Weeks to clear": (backlog / (per_person * regions * p - scn_intake))
         if (per_person * regions * p - scn_intake) > 0 else None}
        for p in range(1, 13)
    ])
    st.markdown("**Sensitivity — weeks to clear vs people/region** (at the chosen automation)")
    plottable = sens.dropna(subset=["Weeks to clear"])
    if plottable.empty:
        st.info("At this automation level the queue grows for every staffing — raise automation "
                "or people/region.")
    else:
        curve = alt.Chart(plottable).mark_line(point=True).encode(
            x=alt.X("People/region:O"),
            y=alt.Y("Weeks to clear:Q", axis=alt.Axis(format=".1f")),
            tooltip=[alt.Tooltip("People/region:O"),
                     alt.Tooltip("Weeks to clear:Q", format=".1f")],
        )
        pick = alt.Chart(pd.DataFrame({"People/region": [scn_ppr]})).mark_rule(
            color="#e8590c", strokeDash=[4, 3]).encode(x="People/region:O")
        st.altair_chart((curve + pick).properties(height=280), width="stretch")
        st.caption("Missing points = the queue still grows at that staffing (no clear). "
                   "Orange line = your current pick.")

    # Recovery plan — realistic hiring pipeline (waves) + step onboarding ----------------
    st.divider()
    st.subheader("Recovery plan — engineers to bring the backlog back to healthy")
    h1, h2, h3, h4 = st.columns(4)
    hire_m = h1.number_input("Hiring time (months)", min_value=1, max_value=36, value=6,
                             help="Lead time to hire one engineer (per parallel pipeline).")
    onboard_m = h2.number_input("Onboarding (months)", min_value=0, max_value=24, value=3,
                                help="A hire produces NOTHING until fully onboarded, then full output.")
    parallel = int(h3.number_input("Concurrent hires", min_value=1, max_value=20, value=2,
                                   help="How many you can hire at once — hires arrive in waves this "
                                        "size, one wave each 'hiring time'."))
    healthy_level = h4.number_input("Healthy backlog (open tickets)", min_value=0,
                                    max_value=10000, value=100,
                                    help="The open-ticket count you'd consider 'under control'.")

    hire_w = max(hire_m * 4.345, 1e-9)
    onboard_w = onboard_m * 4.345
    HORIZON = 261  # 5 years of weeks

    def _productive_weeks(n_hire: int) -> list[float]:
        # engineer i is in wave (i // parallel); that wave is hired (wave+1)·hiring-time out,
        # then onboards. Step model: zero output until that week, then full.
        return sorted(((i // parallel) + 1) * hire_w + onboard_w for i in range(n_hire))

    def _simulate(n_hire: int):
        prod_at = _productive_weeks(n_hire)
        b = float(backlog)
        traj = [b]
        peak, peak_w, recovered_w = b, 0, None
        for w in range(1, HORIZON):
            n_prod = sum(1 for pw in prod_at if w >= pw)
            b = max(b + (intake - (close + n_prod * per_person)), 0.0)
            traj.append(b)
            if b > peak:
                peak, peak_w = b, w
            if recovered_w is None and b <= healthy_level:
                recovered_w = w
        return traj, peak, peak_w, recovered_w

    suggested = next((n for n in range(0, 61) if _simulate(n)[3] is not None), None)
    default_n = suggested if suggested is not None else 12
    n_hire = st.slider("Engineers to hire", 0, 60, int(default_n),
                       help="They arrive in waves (Concurrent hires) and produce only after hiring + onboarding.")
    traj, peak, peak_w, recovered_w = _simulate(n_hire)

    today = datetime.now()

    def _dt(weeks_out: float) -> str:
        return (today + timedelta(weeks=weeks_out)).strftime("%b %Y")

    waves = -(-n_hire // parallel)  # ceil
    all_prod_w = _productive_weeks(n_hire)[-1] if n_hire else 0
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Engineers to hire", f"+{n_hire}")
    m2.metric("All productive by", _dt(all_prod_w) if n_hire else "—",
              help=f"{waves} wave(s) of {parallel}, {hire_m} mo apart, +{onboard_m} mo onboarding each")
    m3.metric("Peak backlog", f"~{int(peak)}", help=f"around {_dt(peak_w)}")
    m4.metric("Healthy by", _dt(recovered_w) if recovered_w is not None else "not within 5y")

    if suggested is None:
        st.warning(
            "Even 60 engineers don't reach the target within 5 years at this hiring speed / rates — "
            "raise **Concurrent hires** or automation, ease the target, or recheck the calibration.")
    elif n_hire >= suggested and recovered_w is not None:
        srec = _simulate(suggested)
        st.success(
            f"**Hire {suggested}** ({-(-suggested // parallel)} wave(s) of {parallel}): backlog peaks "
            f"~**{int(srec[1])}** (~{_dt(srec[2])}), then reaches **≤{healthy_level}** by "
            f"**{_dt(srec[3])}**. Fewer than that never recovers within 5 years.")
    else:
        st.warning(
            f"At **{n_hire}** the backlog doesn't reach ≤{healthy_level} within 5 years — "
            f"suggested minimum is **{suggested}**.")

    sim_df = pd.DataFrame({
        "date": [today + timedelta(weeks=w) for w in range(HORIZON)],
        f"Hire {n_hire}": traj,
        "Do nothing": _simulate(0)[0],
    }).melt("date", var_name="scenario", value_name="backlog")
    line = alt.Chart(sim_df).mark_line().encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("backlog:Q", title="Open backlog"),
        color=alt.Color("scenario:N", title=None),
        tooltip=[alt.Tooltip("date:T", title="Month"), alt.Tooltip("scenario:N"),
                 alt.Tooltip("backlog:Q", title="Backlog", format=".0f")],
    )
    healthy_rule = alt.Chart(pd.DataFrame({"y": [healthy_level]})).mark_rule(
        color="green", strokeDash=[4, 3]).encode(y="y:Q")
    st.altair_chart((line + healthy_rule).properties(height=340), width="stretch")
    st.caption(
        f"Hiring runs **{parallel} at a time**, each wave ~{hire_m} mo apart; a hire produces "
        f"**nothing until fully onboarded** (~{hire_m}+{onboard_m} mo after its wave starts), then "
        f"~{per_person:.1f} closes/wk. The backlog grows through the hiring lead before recovery. "
        "Assumes no attrition and steady intake — a planning estimate, not a guarantee."
    )

    st.divider()
    st.markdown("**Backlog burndown projection — next 12 months**")
    bd = burndown_frame(bsel)  # precomputed/cached per data version
    st.caption(
        f"Projects the open backlog forward a **full year** ({'26 pulses' if bd['unit'] == 'pulse' else '52 weeks'}) "
        f"at the current net of **{bd['net_per_step']:+.1f}/{bd['unit']}**. X-axis = calendar date; "
        "the dashed line marks **today**. The line reaching 0 = backlog cleared."
    )
    if bd["steps_to_zero"] is None:
        st.error(
            f"🔺 Net **{bd['net_per_step']:+.1f}/{bd['unit']}** — the backlog (now **{bd['backlog']}**) "
            f"**keeps growing**, so it never clears at this rate. Needs closes > intake "
            "(more people on tickets and/or automation — see the scenarios above)."
        )
    else:
        st.success(
            f"🔻 Net **{bd['net_per_step']:+.1f}/{bd['unit']}** — the backlog (now **{bd['backlog']}**) "
            f"clears in ~**{bd['steps_to_zero']} {bd['unit']}s**."
        )
    proj = bd["projection"]
    line = alt.Chart(proj).mark_line(point=True).encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("projected_backlog:Q", title="Projected open backlog"),
        tooltip=[alt.Tooltip("date:T", title="Week of"),
                 alt.Tooltip("projected_backlog:Q", title="Backlog", format=".0f")],
    )
    t0 = proj.iloc[0]
    today_df = pd.DataFrame({"date": [t0["date"]], "y": [t0["projected_backlog"]],
                             "label": [f"Today: {int(round(t0['projected_backlog']))}"]})
    rule = alt.Chart(today_df).mark_rule(color="#e8590c", strokeDash=[4, 3]).encode(x="date:T")
    label = alt.Chart(today_df).mark_text(align="left", dx=6, dy=-8, color="#e8590c",
                                          fontWeight="bold").encode(x="date:T", y="y:Q", text="label:N")
    st.altair_chart((line + rule + label).properties(height=360), width="stretch")

    # 3) ps5-blocker stats ---------------------------------------------------
    st.divider()
    st.header("3 · ps5-blocker cadence")
    p = ps5_blocker_stats(session, cfg)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("ps5-blockers total", p["total"])
    c2.metric("Arrival / week", f"{p['arrival_per_week']:.2f}")
    c3.metric("Time to first work", _days(p["mean_first_work_days"]))
    c4.metric("Time to resolve", _days(p["mean_resolve_days"]))
    st.caption(
        f"‘Time to first work’ = creation → first exit from ‘{cfg.untriaged_status}’ "
        f"(n={p['n_first_work']}); ‘time to resolve’ = creation → close (n={p['n_resolved']})."
    )

    # 4) Priority analysis ---------------------------------------------------
    st.divider()
    st.header("4 · By priority")
    pb = priority_breakdown(session, cfg, bsel)
    if pb.empty:
        st.info("No data.")
    else:
        view = pb.rename(columns={
            "priority": "Priority", "created": "Created", "closed": "Closed",
            "mean_ttc_days": "Mean time-to-close (d)", "n_closed": "n closed",
        })
        st.dataframe(view, hide_index=True, width="stretch")
        st.bar_chart(pb.set_index("priority")[["created", "closed"]])
        st.caption("By **current** priority (display-only, Art. VII — point-in-time Highest "
                   "questions live on the Home north-star).")

        # Percentiles per priority — the mean hides the right-skewed tail (issue #38).
        pp = time_to_close_percentiles_by_priority(session, cfg, bsel)
        if not pp.empty:
            st.markdown("**Time-to-close percentiles by priority** (more honest than the mean)")
            st.dataframe(
                pp.rename(columns={
                    "priority": "Priority", "n_closed": "n closed",
                    "p50_days": "p50 (d)", "p85_days": "p85 (d)", "p95_days": "p95 (d)"}),
                hide_index=True, width="stretch")
            melt = pp.melt("priority", value_vars=["p50_days", "p85_days", "p95_days"],
                           var_name="pct", value_name="days")
            melt["pct"] = melt["pct"].map({"p50_days": "p50", "p85_days": "p85", "p95_days": "p95"})
            chart = (
                alt.Chart(melt).mark_bar().encode(
                    x=alt.X("priority:N", sort=PRIORITY_ORDER, title=None),
                    xOffset=alt.XOffset("pct:N", sort=["p50", "p85", "p95"]),
                    y=alt.Y("days:Q", title="Days to close"),
                    color=alt.Color("pct:N", title=None,
                                    scale=alt.Scale(domain=["p50", "p85", "p95"],
                                                    range=["#54a24b", "#f58518", "#e45756"])),
                    tooltip=[alt.Tooltip("priority:N", title="Priority"),
                             alt.Tooltip("pct:N", title="Percentile"),
                             alt.Tooltip("days:Q", title="Days", format=".1f")],
                ).properties(height=300)
            )
            st.altair_chart(chart, width="stretch")
            st.caption("Read 'p85 = 14d' as 85% of that priority's tickets close within 14 days. "
                       "The gap between p50 and p95 is the tail the mean glosses over.")

    # 5) Automation targets --------------------------------------------------
    st.divider()
    st.header("5 · Best automation targets (area ▸ sub-area)")
    at = automation_targets(session, cfg, bsel)
    if at.empty:
        st.info("No data.")
    else:
        top = at.iloc[0]
        st.success(
            f"Top candidate: **{top['group']}** — {int(top['tickets'])} tickets, "
            f"{top['hours']:.1f} h logged (score {top['score']})."
        )
        view = at.rename(columns={
            "group": "Area ▸ Sub-area", "tickets": "Tickets", "hours": "Hours logged",
            "avg_hours_per_ticket": "Avg h/ticket", "score": "Automation score",
        })
        st.dataframe(view, hide_index=True, width="stretch")
        st.bar_chart(at.set_index("group")[["score"]].head(12))
        st.caption(
            "Score = 100 × (½·norm(tickets) + ½·norm(hours)). High = high volume **and** "
            "high logged effort → most leverage from automating that queue."
        )
