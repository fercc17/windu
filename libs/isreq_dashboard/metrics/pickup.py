"""Time to pick up a ticket (issue #16): creation → first **logged work**.

How long a ticket waits before anyone starts working it — the gap between creation
and the earliest worklog ``started`` time. This is distinct from:
  - **time to triage** (creation → leaving Untriaged; a status change, not effort), and
  - **time invested** (total worklog seconds; effort, not wait).

Worked example: a ticket created at T, first worklog at T+24h, 1h of work, closed at
T+25h → pickup = 24h. Only tickets that have at least one worklog can be measured, so
results are gated by logging discipline (n is reported alongside).

Honest stats only — mean + sample stddev + CV + n + low-sample flag (Art. III).
Issue-level; no per-person attribution (Art. VI / FR-018).
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import func, select

from isreq_dashboard.db.models import Worklog
from isreq_dashboard.domain.stats import CloseStats, close_stats
from isreq_dashboard.metrics.base import (
    MetricConfig,
    Selector,
    event_period,
    learn_pulse_naming,
    load_scoped_issues,
)


def _pickup_pairs(session, cfg: MetricConfig, sel: Selector) -> list[tuple[str, float]]:
    """``(period, seconds-to-pickup)`` per ticket that has a worklog, bucketed by the
    pickup event (the first worklog's ``started`` time)."""
    issues = load_scoped_issues(session, cfg, sel)
    if not issues:
        return []
    naming = learn_pulse_naming(issues.values())
    first = dict(
        session.execute(
            select(Worklog.issue_key, func.min(Worklog.started_at))
            .where(Worklog.issue_key.in_(set(issues)))
            .group_by(Worklog.issue_key)
        ).all()
    )
    out: list[tuple[str, float]] = []
    for key, issue in issues.items():
        started = first.get(key)
        if started is None:
            continue
        secs = max((started - issue.created_at).total_seconds(), 0.0)  # guard clock skew
        out.append((event_period(sel.cadence, started, cfg.anchor, naming), secs))
    return out


def pickup_durations(session, cfg: MetricConfig, sel: Selector, period: str | None = None) -> list[float]:
    """Seconds-to-pickup for each measurable ticket (optionally one period)."""
    return [s for p, s in _pickup_pairs(session, cfg, sel) if period is None or p == period]


def pickup_stats(session, cfg: MetricConfig, sel: Selector, period: str | None = None) -> CloseStats:
    """Honest pickup-time summary for a selection."""
    return close_stats(pickup_durations(session, cfg, sel, period), cfg.low_n_threshold)


def pickup_series(session, cfg: MetricConfig, sel: Selector) -> pd.DataFrame:
    """Per-period ``[period, n, mean, stddev, cv, low_sample]`` (mean/stddev in seconds)."""
    by_period: dict[str, list[float]] = {}
    for period, secs in _pickup_pairs(session, cfg, sel):
        by_period.setdefault(period, []).append(secs)
    rows = []
    for period in sorted(by_period):
        s = close_stats(by_period[period], cfg.low_n_threshold)
        rows.append({"period": period, "n": s.n, "mean": s.mean,
                     "stddev": s.stddev_sample, "cv": s.cv, "low_sample": s.low_sample})
    return pd.DataFrame(rows, columns=["period", "n", "mean", "stddev", "cv", "low_sample"])
