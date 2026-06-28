"""Data-quality anomaly checks.

These surface tickets whose state shouldn't occur. They read reconstructed
priority history (Art. VII), so they catch tickets that held the offending state
even if it was later changed.
"""

from __future__ import annotations

from sqlalchemy import func, select

from isreq_dashboard.db.models import Changelog, Issue, PriorityInterval, Worklog
from isreq_dashboard.domain import weeks
from isreq_dashboard.metrics.base import (
    MetricConfig,
    _ever_highest_keys,
    _ps5_keys,
    anchor_datetime,
)

# Policy: after this pulse (exclusive) only Highest / ps5-blocker / PR-MP tickets may be
# worked on. Anything else touched after it is a data-quality violation (issue #8).
POLICY_CUTOFF_PULSE = 9

# Auto-generated HR onboarding/offboarding tickets ("[private ticket] HR Automation: …")
# are routine and excluded from the policy-violation table.
HR_TITLE_MARKER = "HR Automation"


def pr_mp_ever_highest(session, cfg: MetricConfig) -> list[dict]:
    """``[PR/MP Review]`` tickets that EVER held Highest priority — these shouldn't happen.

    A PR/MP-review ticket is routine work and should never be Highest. Detected from
    the priority intervals, so a ticket raised to Highest and later dropped is still
    flagged. Returns key/title/assignee + current priority+status + when it first
    became Highest, newest first.
    """
    rows = session.execute(
        select(
            Issue.key,
            Issue.title,
            Issue.assignee_name,
            Issue.current_priority,
            Issue.current_status,
            func.min(PriorityInterval.valid_from).label("first_highest_at"),
        )
        .join(PriorityInterval, PriorityInterval.issue_key == Issue.key)
        .where(
            Issue.is_pr_mp.is_(True),
            PriorityInterval.priority == cfg.highest_priority_name,
            Issue.created_at >= anchor_datetime(cfg.anchor),
        )
        .group_by(
            Issue.key, Issue.title, Issue.assignee_name,
            Issue.current_priority, Issue.current_status,
        )
        .order_by(func.min(PriorityInterval.valid_from).desc())
    ).all()
    return [
        {
            "key": k,
            "title": t,
            "assignee_name": a,
            "current_priority": cp,
            "current_status": cs,
            "first_highest_at": since,
        }
        for k, t, a, cp, cs, since in rows
    ]


def ordinary_worked_after_cutoff(session, cfg: MetricConfig) -> list[dict]:
    """Data-quality violations (issue #8): **ordinary** tickets worked on after Pulse 9.

    Policy: after the sprint pulse (**Pulse 9, exclusive**) the team should only work on
    Highest / ps5-blocker / PR-MP tickets. So any ticket that is **none** of those, yet
    shows **activity after the end of Pulse 9** — a worklog logged, or a status/priority
    change (triage counts) — shouldn't have been touched at all.

    The cutoff is the global end of Pulse 9 (= end of week 13). ``time_after_seconds`` is
    the worklog logged after the cutoff (the wasted effort); ``time_spent_seconds`` is the
    ticket's total worklog. Issue-level only, never per person (Art. VI). Sorted by
    after-cutoff effort, then total, descending.
    """
    cutoff = weeks.week_end_utc(cfg.anchor, weeks.pulse_window(POLICY_CUTOFF_PULSE)[1])
    ever_highest = _ever_highest_keys(session, cfg)
    ps5 = _ps5_keys(session, cfg)

    issues = list(
        session.scalars(select(Issue).where(Issue.created_at >= anchor_datetime(cfg.anchor)))
    )
    hr = HR_TITLE_MARKER.lower()
    candidates = {
        i.key: i
        for i in issues
        if not (
            i.is_pr_mp
            or i.key in ever_highest
            or i.key in ps5
            or (i.title and hr in i.title.lower())  # exclude HR automation tickets
        )
    }
    if not candidates:
        return []
    keys = set(candidates)

    # worklog logged AFTER the cutoff: seconds + most-recent entry
    wl_after: dict[str, tuple[int, object]] = {}
    for k, secs, last in session.execute(
        select(
            Worklog.issue_key,
            func.sum(Worklog.time_spent_seconds),
            func.max(Worklog.started_at),
        )
        .where(Worklog.issue_key.in_(keys), Worklog.started_at >= cutoff)
        .group_by(Worklog.issue_key)
    ).all():
        wl_after[k] = (int(secs or 0), last)

    # any status/priority change AFTER the cutoff (the changelog only stores those two,
    # so this is exactly "the ticket was moved/triaged after the cutoff")
    cl_after: dict[str, object] = dict(
        session.execute(
            select(Changelog.issue_key, func.max(Changelog.changed_at))
            .where(Changelog.issue_key.in_(keys), Changelog.changed_at >= cutoff)
            .group_by(Changelog.issue_key)
        ).all()
    )

    total_wl: dict[str, int] = dict(
        session.execute(
            select(Worklog.issue_key, func.sum(Worklog.time_spent_seconds))
            .where(Worklog.issue_key.in_(keys))
            .group_by(Worklog.issue_key)
        ).all()
    )

    rows: list[dict] = []
    for k, i in candidates.items():
        secs_after, wl_last = wl_after.get(k, (0, None))
        cl_last = cl_after.get(k)
        if wl_last is None and cl_last is None:
            continue  # no activity after the cutoff — fine
        last_activity = max(t for t in (wl_last, cl_last) if t is not None)
        rows.append(
            {
                "key": k,
                "title": i.title,
                "assignee_name": i.assignee_name,
                "time_after_seconds": secs_after,
                "time_spent_seconds": int(total_wl.get(k, 0) or 0),
                "last_activity_at": last_activity,
                "current_status": i.current_status,
                "current_priority": i.current_priority,
            }
        )
    rows.sort(key=lambda r: (r["time_after_seconds"], r["time_spent_seconds"]), reverse=True)
    return rows


def unassigned_past_triage(session, cfg: MetricConfig) -> list[dict]:
    """Tickets that have **moved past triage** (status is neither Untriaged nor Triaged)
    yet have **no assignee** — work in flight with no owner (issue #8 follow-up).

    Returns key/title/current_status/area/created_at, oldest first.
    """
    triage_states = {cfg.untriaged_status, "Triaged"}
    rows = [
        {
            "key": i.key,
            "title": i.title,
            "current_status": i.current_status,
            "area": i.area,
            "created_at": i.created_at,
        }
        for i in session.scalars(
            select(Issue).where(
                Issue.assignee_account_id.is_(None),
                Issue.created_at >= anchor_datetime(cfg.anchor),
            )
        )
        if (i.current_status or "") not in triage_states
    ]
    rows.sort(key=lambda r: r["created_at"])
    return rows
