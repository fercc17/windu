"""Flow rates (issue #6): creation rate vs close rate.

Two views, both reusing the existing definitions so the numbers match the rest of
the dashboard:

- :func:`flow_series` — per period: tickets ``created`` (Intake) vs ``closed``
  (Throughput close events), plus the running ``cum_created`` / ``cum_closed`` /
  ``cum_net`` since inception.
- :func:`flow_headline` — overall rates "from the beginning": tickets created and
  closed per week since the anchor, and the mean inter-arrival time between
  creations ("how fast a ticket got created").
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from isreq_dashboard.domain import status as status_domain
from isreq_dashboard.metrics.base import (
    GROUP_AREA,
    MetricConfig,
    Selector,
    load_scoped_issues,
    load_status_intervals,
)
from isreq_dashboard.metrics.intake import intake_series
from isreq_dashboard.metrics.throughput import throughput_series


def flow_series(session, cfg: MetricConfig, sel: Selector) -> pd.DataFrame:
    """``[period, created, closed, net, cum_created, cum_closed, cum_net]`` per period."""
    created = intake_series(session, cfg, sel, group=GROUP_AREA)
    if created.empty:
        created = pd.DataFrame(columns=["period", "created"])
    else:
        created = (
            created.groupby("period", as_index=False)["count"]
            .sum()
            .rename(columns={"count": "created"})
        )

    closed = throughput_series(session, cfg, sel)
    if closed.empty:
        closed = pd.DataFrame(columns=["period", "closed"])
    else:
        closed = closed.rename(columns={"throughput": "closed"})

    df = pd.merge(created, closed, on="period", how="outer")
    if df.empty:
        return pd.DataFrame(
            columns=["period", "created", "closed", "net", "cum_created", "cum_closed", "cum_net"]
        )
    df = df.fillna(0).sort_values("period").reset_index(drop=True)
    df["created"] = df["created"].astype(int)
    df["closed"] = df["closed"].astype(int)
    df["net"] = df["created"] - df["closed"]
    df["cum_created"] = df["created"].cumsum()
    df["cum_closed"] = df["closed"].cumsum()
    df["cum_net"] = df["net"].cumsum()
    return df


def flow_headline(session, cfg: MetricConfig, sel: Selector) -> dict:
    """Overall rates since the anchor: created/closed per week + mean inter-arrival.

    The span is anchor → last observed event (creation or close), so the rate reflects
    the data actually present rather than wall-clock time (which may run ahead of sync).
    """
    issues = load_scoped_issues(session, cfg, sel)
    creations = sorted(i.created_at for i in issues.values())
    sti = load_status_intervals(session, set(issues))
    closes = sorted(
        t
        for k in issues
        for t in status_domain.close_events(sti.get(k, []), cfg.closed_statuses)
    )

    anchor_dt = datetime(cfg.anchor.year, cfg.anchor.month, cfg.anchor.day, tzinfo=timezone.utc)
    last = max([*creations, *closes], default=anchor_dt)
    weeks_span = max((last - anchor_dt).total_seconds() / (7 * 86400), 1e-9)

    n_created, n_closed = len(creations), len(closes)
    mean_interarrival_seconds = (
        (creations[-1] - creations[0]).total_seconds() / (n_created - 1)
        if n_created >= 2
        else None
    )
    return {
        "total_created": n_created,
        "total_closed": n_closed,
        "net": n_created - n_closed,
        "weeks_span": weeks_span,
        "created_per_week": n_created / weeks_span,
        "closed_per_week": n_closed / weeks_span,
        "mean_interarrival_seconds": mean_interarrival_seconds,
    }
