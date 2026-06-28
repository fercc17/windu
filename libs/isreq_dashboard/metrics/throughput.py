"""M3 — Throughput (FR-015) and M6 — time-to-close statistics (FR-022-024).

Throughput counts **close events** per period: a closed -> reopened -> reclosed
issue contributes one event per close. Time-to-close pairs each close event with
the issue's creation and is summarised honestly (mean + sample stddev + CV + basis
+ low-sample flag) — never a lone mean.
"""

from __future__ import annotations

import statistics

import pandas as pd

from isreq_dashboard.domain import status as st
from isreq_dashboard.domain.stats import CloseStats, close_stats
from isreq_dashboard.metrics.base import (
    MetricConfig,
    Selector,
    event_period,
    group_value,
    learn_pulse_naming,
    load_scoped_issues,
    load_status_intervals,
)


def throughput_series(
    session, cfg: MetricConfig, sel: Selector, group: str | None = None
) -> pd.DataFrame:
    """Per-period count of close events.

    ``group=None`` -> ``[period, throughput]``. With a group (area / sub_area) ->
    ``[period, group, throughput]``, each close event bucketed by its issue's group.
    """
    issues = load_scoped_issues(session, cfg, sel)
    sti = load_status_intervals(session, set(issues))
    naming = learn_pulse_naming(issues.values())
    records: list[dict] = []
    for key, issue in issues.items():
        for t in st.close_events(sti.get(key, []), cfg.closed_statuses):
            # per-pulse buckets by the pulse window of the close event (issue #14)
            rec = {"period": event_period(sel.cadence, t, cfg.anchor, naming)}
            if group is not None:
                rec["group"] = group_value(group, issue, cfg)
            records.append(rec)

    if group is None:
        if not records:
            return pd.DataFrame(columns=["period", "throughput"])
        return (
            pd.DataFrame(records).groupby("period").size().reset_index(name="throughput")
            .sort_values("period").reset_index(drop=True)
        )
    if not records:
        return pd.DataFrame(columns=["period", "group", "throughput"])
    return (
        pd.DataFrame(records).groupby(["period", "group"]).size().reset_index(name="throughput")
        .sort_values(["period", "group"])
    )


def time_to_close_durations(session, cfg: MetricConfig, sel: Selector, period: str | None = None) -> list[float]:
    """Seconds-to-close for each close event (optionally restricted to one period)."""
    issues = load_scoped_issues(session, cfg, sel)
    sti = load_status_intervals(session, set(issues))
    naming = learn_pulse_naming(issues.values())
    out: list[float] = []
    for key, issue in issues.items():
        for t in st.close_events(sti.get(key, []), cfg.closed_statuses):
            if period is not None and event_period(sel.cadence, t, cfg.anchor, naming) != period:
                continue
            out.append((t - issue.created_at).total_seconds())
    return out


def time_to_close_stats(session, cfg: MetricConfig, sel: Selector, period: str | None = None) -> CloseStats:
    """Honest time-to-close summary for a selection (Art. III)."""
    return close_stats(time_to_close_durations(session, cfg, sel, period), cfg.low_n_threshold)


def _percentile(sorted_secs: list[float], p: float) -> float | None:
    """Linear-interpolated ``p``-th percentile (0-100) of a sorted list."""
    if not sorted_secs:
        return None
    k = (len(sorted_secs) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_secs) - 1)
    return sorted_secs[lo] + (sorted_secs[hi] - sorted_secs[lo]) * (k - lo)


def time_to_close_percentiles(session, cfg: MetricConfig, sel: Selector, period: str | None = None) -> dict:
    """Time-to-close p50/p85/p95 (seconds) + the raw durations for a histogram (issue #30).

    The mean is misleading when time-to-close is very dispersed (high CV); percentiles say
    'X% of tickets close within N days'."""
    durs = sorted(time_to_close_durations(session, cfg, sel, period))
    return {
        "n": len(durs),
        "p50": _percentile(durs, 50),
        "p85": _percentile(durs, 85),
        "p95": _percentile(durs, 95),
        "durations": durs,
    }


def time_to_close_by_area(session, cfg: MetricConfig, sel: Selector, group: str = "area") -> pd.DataFrame:
    """Mean/median time-to-close (days) per area or sub-area (issue #34) — which queues
    are slowest to resolve. ``[group, n, mean_days, median_days]``, slowest first."""
    issues = load_scoped_issues(session, cfg, sel)
    sti = load_status_intervals(session, set(issues))
    by_group: dict[str, list[float]] = {}
    for key, issue in issues.items():
        for t in st.close_events(sti.get(key, []), cfg.closed_statuses):
            by_group.setdefault(group_value(group, issue, cfg), []).append(
                (t - issue.created_at).total_seconds()
            )
    rows = [
        {"group": g, "n": len(v),
         "mean_days": round(statistics.fmean(v) / 86400, 1),
         "median_days": round(statistics.median(v) / 86400, 1)}
        for g, v in by_group.items()
    ]
    if not rows:
        return pd.DataFrame(columns=["group", "n", "mean_days", "median_days"])
    return pd.DataFrame(rows).sort_values("mean_days", ascending=False).reset_index(drop=True)
