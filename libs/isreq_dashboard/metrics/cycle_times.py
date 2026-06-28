"""Cycle-time distributions per period (Art. III, FR-022-025).

Three durations, each summarised *honestly* per period — mean + sample standard
deviation (n-1) + coefficient of variation + n + low-sample flag, never a lone mean:

- **time to triage**  — creation → the first time the ticket leaves ``Untriaged``.
- **time to close**   — creation → entry into a closed status (each close counts, FR-015).
- **time in In Review** — each completed ``In Review`` spell: entry → exit.

Bucketing follows the *completing event* (triaged-at / closed-at / left-review-at):
weekly by that event's week, per-pulse by the pulse whose window contains it
(anchor-derived calendar) — so there is no sprint-field ``Unknown`` bucket.
"""

from __future__ import annotations

import pandas as pd

from isreq_dashboard.domain import status as st
from isreq_dashboard.domain.stats import close_stats
from isreq_dashboard.metrics.base import (
    MetricConfig,
    Selector,
    event_period,
    learn_pulse_naming,
    load_scoped_issues,
    load_status_intervals,
)

KIND_TRIAGE = "triage"
KIND_CLOSE = "close"
KIND_IN_REVIEW = "in_review"
KINDS = (KIND_TRIAGE, KIND_CLOSE, KIND_IN_REVIEW)

# Stacked time-in-status decomposition: the workflow (non-terminal) statuses, in the
# bottom-to-top order they appear in a ticket's life. Terminal (closed) statuses are
# the stack *total* (time to close), not a segment. Unknown statuses sort after these.
WORKFLOW_ORDER = ("Untriaged", "Triaged", "In Progress", "In Review",
                  "BLOCKED", "Escalated", "Sleeping", "To Be Deployed")
STAT_AVG = "avg"
STAT_SD = "sd"
STAT_CV = "cv"

__all__ = ["KIND_TRIAGE", "KIND_CLOSE", "KIND_IN_REVIEW", "KINDS",
           "STAT_AVG", "STAT_SD", "STAT_CV", "WORKFLOW_ORDER",
           "cycle_time_series", "cycle_time_durations", "status_time_decomposition"]


# --- (period, seconds) pair builders ----------------------------------------

def _triage_pairs(issues, sti, cfg, sel, naming):
    out: list[tuple[str, float]] = []
    for key, issue in issues.items():
        exit_at = st.triage_exit(sti.get(key, []), cfg.untriaged_status)
        if exit_at is None:
            continue
        out.append((event_period(sel.cadence, exit_at, cfg.anchor, naming),
                    (exit_at - issue.created_at).total_seconds()))
    return out


def _close_pairs(issues, sti, cfg, sel, naming):
    out: list[tuple[str, float]] = []
    for key, issue in issues.items():
        for t in st.close_events(sti.get(key, []), cfg.closed_statuses):
            out.append((event_period(sel.cadence, t, cfg.anchor, naming),
                        (t - issue.created_at).total_seconds()))
    return out


def _in_review_pairs(issues, sti, cfg, sel, naming):
    out: list[tuple[str, float]] = []
    for key, issue in issues.items():
        for iv in st.completed_spells(sti.get(key, []), cfg.in_review_status):
            out.append((event_period(sel.cadence, iv.valid_to, cfg.anchor, naming),
                        (iv.valid_to - iv.valid_from).total_seconds()))
    return out


_BUILDERS = {
    KIND_TRIAGE: _triage_pairs,
    KIND_CLOSE: _close_pairs,
    KIND_IN_REVIEW: _in_review_pairs,
}


# --- aggregation ------------------------------------------------------------

def _stats_frame(pairs: list[tuple[str, float]], low_n: int) -> pd.DataFrame:
    by_period: dict[str, list[float]] = {}
    for period, secs in pairs:
        by_period.setdefault(period, []).append(secs)
    rows = []
    for period in sorted(by_period):
        s = close_stats(by_period[period], low_n)
        rows.append({"period": period, "n": s.n, "mean": s.mean,
                     "stddev": s.stddev_sample, "cv": s.cv, "low_sample": s.low_sample})
    return pd.DataFrame(rows, columns=["period", "n", "mean", "stddev", "cv", "low_sample"])


def _load(session, cfg: MetricConfig, sel: Selector):
    issues = load_scoped_issues(session, cfg, sel)
    sti = load_status_intervals(session, set(issues))
    naming = learn_pulse_naming(issues.values())
    return issues, sti, naming


def cycle_time_series(session, cfg: MetricConfig, sel: Selector, kind: str) -> pd.DataFrame:
    """``[period, n, mean, stddev, cv, low_sample]`` for one duration ``kind``.

    ``mean``/``stddev`` are in **seconds** (None when undefined); ``cv`` is the
    unitless stddev/mean. Periods with ``n < low_n_threshold`` are flagged."""
    issues, sti, naming = _load(session, cfg, sel)
    return _stats_frame(_BUILDERS[kind](issues, sti, cfg, sel, naming), cfg.low_n_threshold)


def cycle_time_durations(
    session, cfg: MetricConfig, sel: Selector, kind: str, period: str | None = None
) -> list[float]:
    """Raw seconds for ``kind`` (optionally one period) — for drill-down / summaries."""
    issues, sti, naming = _load(session, cfg, sel)
    pairs = _BUILDERS[kind](issues, sti, cfg, sel, naming)
    return [secs for p, secs in pairs if period is None or p == period]


# --- time-in-status decomposition (contribution-averaged, stacks to time-to-close) --

def _status_time_per_closed_ticket(ivs, t_close, closed: set[str]) -> dict[str, float]:
    """Seconds spent in each non-terminal status from creation to the first close.

    Intervals are contiguous from creation, so the sum across statuses equals
    ``t_close - created_at`` (the time to close) exactly."""
    times: dict[str, float] = {}
    for iv in ivs:
        if iv.valid_to is None or iv.valid_to > t_close:
            continue                       # open spell, or anything after the first close
        if iv.value is None or iv.value in closed:
            continue                       # terminal time is the total, not a segment
        times[iv.value] = times.get(iv.value, 0.0) + (iv.valid_to - iv.valid_from).total_seconds()
    return times


def status_time_decomposition(session, cfg: MetricConfig, sel: Selector) -> dict:
    """Contribution-averaged time-in-status per period, over tickets *closed* in that
    period (bucketed by the close event).

    Returns ``{"avg": df, "sd": df, "cv": df, "cohort": {period: n}, "order": [...]}``
    where each df is long ``[period, group, value]`` (group = status; avg/sd in
    **seconds**, cv unitless). Each ticket contributes its time in a status (0 if it
    never entered it), so the per-period AVG segments **sum to the mean time-to-close**.
    """
    issues, sti, naming = _load(session, cfg, sel)
    closed = set(cfg.closed_statuses)

    cohorts: dict[str, list[dict[str, float]]] = {}
    for key in issues:
        ivs = sti.get(key, [])
        t_close = st.first_close_at(ivs, cfg.closed_statuses)
        if t_close is None:
            continue                       # never closed -> not in any cohort
        period = event_period(sel.cadence, t_close, cfg.anchor, naming)
        cohorts.setdefault(period, []).append(_status_time_per_closed_ticket(ivs, t_close, closed))

    universe: set[str] = set()
    for tickets in cohorts.values():
        for tmap in tickets:
            universe.update(tmap)
    order = [s for s in WORKFLOW_ORDER if s in universe] + sorted(universe - set(WORKFLOW_ORDER))

    avg_rows, sd_rows, cv_rows = [], [], []
    cohort_sizes: dict[str, int] = {}
    for period in sorted(cohorts):
        tickets = cohorts[period]
        cohort_sizes[period] = len(tickets)
        for status in order:
            vals = [t.get(status, 0.0) for t in tickets]   # contribution: zeros included
            s = close_stats(vals, cfg.low_n_threshold)
            avg_rows.append({"period": period, "group": status, "value": s.mean or 0.0})
            sd_rows.append({"period": period, "group": status, "value": s.stddev_sample or 0.0})
            cv_rows.append({"period": period, "group": status, "value": s.cv or 0.0})

    cols = ["period", "group", "value"]
    return {
        STAT_AVG: pd.DataFrame(avg_rows, columns=cols),
        STAT_SD: pd.DataFrame(sd_rows, columns=cols),
        STAT_CV: pd.DataFrame(cv_rows, columns=cols),
        "cohort": cohort_sizes,
        "order": order,
    }
