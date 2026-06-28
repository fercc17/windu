"""M5 — Time invested (FR-017/018): sum of worklog seconds per period, bucketed by
each worklog's ``started`` date, attributed at issue/area level ONLY.

Best-effort and dependent on logging discipline. There is no author column and no
per-person breakdown is offered or computable (Art. VI)."""

from __future__ import annotations

import pandas as pd
from sqlalchemy import select

from isreq_dashboard.db.models import Worklog
from isreq_dashboard.metrics.base import (
    MetricConfig,
    Selector,
    event_period,
    learn_pulse_naming,
    load_scoped_issues,
)

GROUP_AREA = "area"
GROUP_SUB_AREA = "sub_area"
GROUP_ISSUE = "issue"
UNKNOWN_GROUP = "Backlog"  # rebranded from "Unknown" (issue #15)

BEST_EFFORT_CAVEAT = (
    "Best-effort: depends on logging discipline; not authoritative effort; "
    "no per-person attribution (Art. VI / FR-018)."
)


def time_invested_series(session, cfg: MetricConfig, sel: Selector, group: str = GROUP_AREA) -> pd.DataFrame:
    """Long frame ``[period, group, seconds]``; bucket each worklog by its started date."""
    issues = load_scoped_issues(session, cfg, sel)
    keys = set(issues)
    if not keys:
        return pd.DataFrame(columns=["period", "group", "seconds"])

    naming = learn_pulse_naming(issues.values())
    records = []
    for wl in session.scalars(select(Worklog).where(Worklog.issue_key.in_(keys))):
        issue = issues[wl.issue_key]
        # bucket by the worklog's STARTED time — weekly or its event-time pulse (issue #14)
        period = event_period(sel.cadence, wl.started_at, cfg.anchor, naming)
        if group == GROUP_ISSUE:
            group_val = issue.key
        elif group == GROUP_SUB_AREA:
            group_val = f"{issue.area or UNKNOWN_GROUP} ▸ {issue.sub_area or UNKNOWN_GROUP}"
        else:
            group_val = issue.area or UNKNOWN_GROUP
        records.append({"period": period, "group": group_val, "seconds": wl.time_spent_seconds})

    if not records:
        return pd.DataFrame(columns=["period", "group", "seconds"])
    df = pd.DataFrame(records)
    return (
        df.groupby(["period", "group"])["seconds"].sum().reset_index().sort_values(["period", "group"])
    )
