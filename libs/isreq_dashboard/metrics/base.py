"""Shared metric scaffolding: config, selectors, scoped loaders, period keys.

Each metric is a pure function of synced data + ``(cadence, scope, pr_mp)`` selectors
(contracts/metrics.md). Week numbering is custom (not SQL-native), so metrics fetch
scope-filtered rows and bucket by period in Python/pandas — correct and portable
across Postgres and SQLite. For the ~3k-20k issue envelope this is well within the
<1s budget; pushing aggregation into SQL is a later optimisation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from isreq_dashboard.db.models import (
    Issue,
    IssueLabel,
    PriorityInterval,
    StatusInterval,
    SyncState,
)
from isreq_dashboard.domain import weeks
from isreq_dashboard.domain.intervals import Interval
from isreq_dashboard.domain.regions import region_from_timestamp

WEEKLY = "weekly"
PER_PULSE = "per_pulse"
SCOPE_ALL = "all"              # no filter — every ISReq ticket
SCOPE_HIGHEST = "highest"      # tickets that ever held Highest priority
SCOPE_PS5 = "ps5_blocker"      # tickets carrying the ps5-blocker label
SCOPE_HIGHEST_OR_PS5 = "highest_or_ps5"  # union of the two filters
PR_MP_INCLUDED = "included"
PR_MP_EXCLUDED = "excluded"
PR_MP_ONLY = "only"
UNKNOWN_PULSE = "Backlog"  # unsprinted tickets bucket (rebranded from "Unknown", issue #15)

# Breakdown dimensions shared by intake / throughput / backlog / time-invested.
GROUP_AREA = "area"
GROUP_SUB_AREA = "sub_area"
GROUP_REGION = "region_time_of_day"
UNKNOWN_GROUP = "Backlog"  # missing area/sub-area/region bucket (rebranded, issue #15)


@dataclass(frozen=True)
class MetricConfig:
    anchor: date
    closed_statuses: list[str]
    highest_priority_name: str = "Highest"
    ps5_blocker_label: str = "ps5-blocker"
    region_windows: Mapping[str, Mapping[str, str]] | None = None
    low_n_threshold: int = 5
    untriaged_status: str = "Untriaged"
    in_review_status: str = "In Review"


@dataclass(frozen=True)
class Selector:
    cadence: str = WEEKLY      # weekly | per_pulse
    scope: str = SCOPE_ALL     # all | ps5_blocker
    pr_mp: str = PR_MP_INCLUDED  # included | excluded | only


@dataclass(frozen=True)
class IssueRow:
    key: str
    title: str | None
    assignee_account_id: str | None
    assignee_name: str | None
    area: str | None
    sub_area: str | None
    pulse: str | None
    created_at: datetime
    is_pr_mp: bool
    current_status: str | None = None
    reporter_account_id: str | None = None
    reporter_name: str | None = None


def anchor_datetime(anchor: date) -> datetime:
    """Anchor as midnight UTC — the pre-inception cutoff (tickets before it are excluded)."""
    return datetime(anchor.year, anchor.month, anchor.day, tzinfo=timezone.utc)


def period_for(
    cadence: str,
    issue: IssueRow,
    event_time: datetime,
    anchor: date,
) -> str:
    """Period key for an event: week of the event (weekly) or the issue's pulse (per-pulse)."""
    if cadence == PER_PULSE:
        return issue.pulse or UNKNOWN_PULSE
    return weeks.period_key(event_time, anchor)


# Trailing integer of a sprint name, e.g. "IS Pulse 2026#09" -> ("IS Pulse 2026#", "09").
_PULSE_NUM_RE = re.compile(r"^(.*?)(\d+)\s*$")


def learn_pulse_naming(issues) -> tuple[dict[int, str], str]:
    """``({pulse_number: exact sprint name}, dominant prefix)`` learnt from sprinted
    issues, so a pulse derived from an event time can be labelled with the same
    ``IS Pulse YYYY#NN`` string and merge with the real sprint bars."""
    by_number: dict[int, str] = {}
    prefix_counts: dict[str, int] = {}
    for issue in issues:
        if not issue.pulse:
            continue
        m = _PULSE_NUM_RE.match(issue.pulse)
        if not m:
            continue
        prefix, number = m.group(1), int(m.group(2))
        by_number.setdefault(number, issue.pulse)
        prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
    prefix = max(prefix_counts, key=prefix_counts.get) if prefix_counts else ""
    return by_number, prefix


def pulse_label_for_time(t: datetime, anchor: date, naming: tuple[dict[int, str], str]) -> str | None:
    """Label of the pulse whose 2-week window contains ``t`` (``None`` pre-inception)."""
    by_number, prefix = naming
    n = weeks.pulse_number_at(t, anchor)
    if n is None:
        return None
    return by_number.get(n, f"{prefix}{n:02d}")


def event_period(cadence: str, t: datetime, anchor: date, naming: tuple[dict[int, str], str]) -> str:
    """Period key bucketed by *event time*: the week (weekly) or the pulse whose
    window contains ``t`` (per-pulse). Unlike :func:`period_for`, per-pulse here is
    driven by the event, not the issue's sprint field. Pre-anchor events land in the
    labelled ``Pre-inception`` bucket in both cadences (never an ``Unknown`` bar)."""
    if cadence == PER_PULSE:
        label = pulse_label_for_time(t, anchor, naming)
        return label if label is not None else weeks.PRE_INCEPTION_LABEL
    return weeks.period_key(t, anchor)


def group_value(group: str, issue: IssueRow, cfg: "MetricConfig") -> str:
    """Breakdown bucket for an issue. Sub-area is qualified by its area (``Area ▸ Sub``)
    because sub-area names (e.g. "Other") repeat across areas; region is the
    creation-time-of-day derivation (FR-026a)."""
    if group == GROUP_REGION:
        if not cfg.region_windows:
            return UNKNOWN_GROUP
        return region_from_timestamp(issue.created_at, cfg.region_windows)
    if group == GROUP_SUB_AREA:
        return f"{issue.area or UNKNOWN_GROUP} ▸ {issue.sub_area or UNKNOWN_GROUP}"
    return getattr(issue, group) or UNKNOWN_GROUP


def _ps5_keys(session: Session, cfg: MetricConfig) -> set[str]:
    return set(
        session.scalars(
            select(IssueLabel.issue_key).where(IssueLabel.label == cfg.ps5_blocker_label)
        )
    )


def _ever_highest_keys(session: Session, cfg: MetricConfig) -> set[str]:
    """Issues that ever held Highest priority (from reconstructed intervals, Art. VII)."""
    return set(
        session.scalars(
            select(PriorityInterval.issue_key)
            .where(PriorityInterval.priority == cfg.highest_priority_name)
            .distinct()
        )
    )


def load_scoped_issues(session: Session, cfg: MetricConfig, sel: Selector) -> dict[str, IssueRow]:
    """Return ``{key: IssueRow}`` after applying the scope filter (Highest / ps5-blocker /
    their union / none) and the PR/MP filter."""
    rows = {
        i.key: IssueRow(
            key=i.key,
            title=i.title,
            assignee_account_id=i.assignee_account_id,
            assignee_name=i.assignee_name,
            area=i.area,
            sub_area=i.sub_area,
            pulse=i.pulse,
            created_at=i.created_at,
            is_pr_mp=bool(i.is_pr_mp),
            current_status=i.current_status,
            reporter_account_id=i.reporter_account_id,
            reporter_name=i.reporter_name,
        )
        # Exclude pre-inception tickets (created before the anchor — e.g. ISREQ-1) from
        # every metric: no "Pre-inception" bucket, no contribution to any count.
        for i in session.scalars(
            select(Issue).where(Issue.created_at >= anchor_datetime(cfg.anchor))
        )
    }

    keep: set[str] | None = None
    if sel.scope == SCOPE_PS5:
        keep = _ps5_keys(session, cfg)
    elif sel.scope == SCOPE_HIGHEST:
        keep = _ever_highest_keys(session, cfg)
    elif sel.scope == SCOPE_HIGHEST_OR_PS5:
        keep = _ever_highest_keys(session, cfg) | _ps5_keys(session, cfg)
    if keep is not None:
        rows = {k: v for k, v in rows.items() if k in keep}

    if sel.pr_mp == PR_MP_EXCLUDED:
        rows = {k: v for k, v in rows.items() if not v.is_pr_mp}
    elif sel.pr_mp == PR_MP_ONLY:
        rows = {k: v for k, v in rows.items() if v.is_pr_mp}

    return rows


def _load_intervals(session: Session, model, keys: set[str], value_attr: str) -> dict[str, list[Interval]]:
    out: dict[str, list[Interval]] = {}
    if not keys:
        return out
    stmt = select(model).where(model.issue_key.in_(keys)).order_by(model.issue_key, model.valid_from)
    for row in session.scalars(stmt):
        out.setdefault(row.issue_key, []).append(
            Interval(getattr(row, value_attr), row.valid_from, row.valid_to)
        )
    return out


def load_priority_intervals(session: Session, keys: set[str]) -> dict[str, list[Interval]]:
    return _load_intervals(session, PriorityInterval, keys, "priority")


def load_status_intervals(session: Session, keys: set[str]) -> dict[str, list[Interval]]:
    return _load_intervals(session, StatusInterval, keys, "status")


def last_sync_at(session: Session, resource: str = "issues") -> datetime | None:
    """Data-freshness watermark surfaced on every view (FR-005, SC-008, I-5)."""
    row = session.get(SyncState, resource)
    return row.last_sync_at if row else None
