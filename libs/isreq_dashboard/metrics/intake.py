"""M2 — Intake (FR-014): count of issues created per period, grouped by
area / sub_area / creation-time-of-day region. PR/MP tickets are included by
default (FR-028); the selector can exclude or isolate them."""

from __future__ import annotations

import pandas as pd

from isreq_dashboard.metrics.base import (
    GROUP_AREA,
    GROUP_REGION,
    GROUP_SUB_AREA,
    MetricConfig,
    Selector,
    event_period,
    group_value,
    learn_pulse_naming,
    load_scoped_issues,
)

__all__ = ["GROUP_AREA", "GROUP_SUB_AREA", "GROUP_REGION", "intake_series"]


def intake_series(session, cfg: MetricConfig, sel: Selector, group: str = GROUP_AREA) -> pd.DataFrame:
    """Long frame ``[period, group, count]`` of issues created per period.

    Per-pulse buckets by the **event-time** pulse window of the creation date (issue #14),
    not the sparsely-populated sprint field — so every calendar pulse is represented.
    """
    issues = load_scoped_issues(session, cfg, sel)
    naming = learn_pulse_naming(issues.values())
    records = [
        {
            "period": event_period(sel.cadence, i.created_at, cfg.anchor, naming),
            "group": group_value(group, i, cfg),
        }
        for i in issues.values()
    ]
    if not records:
        return pd.DataFrame(columns=["period", "group", "count"])
    df = pd.DataFrame(records)
    return (
        df.groupby(["period", "group"]).size().reset_index(name="count").sort_values(["period", "group"])
    )
