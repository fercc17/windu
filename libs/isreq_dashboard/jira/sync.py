"""Incremental, idempotent sync into the ``isreq`` schema (Art. X, research R-002/R-010).

The only component that talks to Jira, and only to read. It:
  1. searches issues (incrementally via the ``updated >=`` watermark) with changelog,
  2. completes truncated changelogs and always-complete worklogs,
  3. upserts issues/labels/changelog/worklogs on stable keys (idempotent, SC-009),
  4. rebuilds priority/status intervals per touched issue from the complete changelog,
  5. advances the ``sync_state`` watermark ONLY on full success.

All writes are confined to ``isreq``; no DROP/TRUNCATE/drop-all here (Art. VIII).
The persist + derive steps take a plain list of raw issues, so they are testable
against SQLite with a fake client.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from isreq_dashboard.db.models import (
    Changelog,
    Issue,
    IssueLabel,
    PriorityInterval,
    StatusInterval,
    SyncState,
    User,
    Worklog,
)
from isreq_dashboard.domain.regions import ALLOWED_REGIONS, UNKNOWN
from isreq_dashboard.domain.intervals import Change
from isreq_dashboard.domain.priority import build_priority_intervals
from isreq_dashboard.domain.status import build_status_intervals
from isreq_dashboard.jira import mapping
from isreq_dashboard.jira.client import ReadOnlyJiraClient
from isreq_dashboard.jira.worklog import fetch_complete_worklogs

log = logging.getLogger("isreq.sync")
WATERMARK_RESOURCE = "issues"


@dataclass
class SyncStats:
    issues: int = 0
    changelog_rows: int = 0
    worklogs: int = 0
    intervals: int = 0
    completed_changelogs: int = 0
    errors: list[str] = field(default_factory=list)


def _creation_values(raw_issue: dict, cl_rows: list[dict]) -> tuple[str | None, str | None]:
    """Creation priority/status: the ``from`` of the first change, else current value."""
    fields = raw_issue.get("fields", {})
    cur_priority = (fields.get("priority") or {}).get("name")
    cur_status = (fields.get("status") or {}).get("name")

    def first_from(field_name: str, current: str | None) -> str | None:
        changes = sorted(
            (r for r in cl_rows if r["field"] == field_name),
            key=lambda r: r["changed_at"],
        )
        return changes[0]["from_value"] if changes else current

    return first_from("priority", cur_priority), first_from("status", cur_status)


def _rebuild_intervals(session: Session, key: str, created_at: datetime, raw_issue: dict, cl_rows: list[dict]) -> int:
    creation_priority, creation_status = _creation_values(raw_issue, cl_rows)
    p_changes = [
        Change(r["changed_at"], r["to_value"]) for r in cl_rows if r["field"] == "priority"
    ]
    s_changes = [
        Change(r["changed_at"], r["to_value"]) for r in cl_rows if r["field"] == "status"
    ]

    session.query(PriorityInterval).filter_by(issue_key=key).delete()
    session.query(StatusInterval).filter_by(issue_key=key).delete()

    n = 0
    for iv in build_priority_intervals(created_at, creation_priority, p_changes):
        session.add(PriorityInterval(issue_key=key, priority=iv.value,
                                     valid_from=iv.valid_from, valid_to=iv.valid_to))
        n += 1
    for iv in build_status_intervals(created_at, creation_status, s_changes):
        session.add(StatusInterval(issue_key=key, status=iv.value,
                                   valid_from=iv.valid_from, valid_to=iv.valid_to))
        n += 1
    return n


def process_issue(session: Session, raw_issue: dict, raw_worklogs: list[dict], cfg, *, now: datetime) -> SyncStats:
    """Upsert one issue + its labels/changelog/worklogs and rebuild its intervals."""
    stats = SyncStats()
    irow = mapping.issue_row(raw_issue, cfg)
    irow["synced_at"] = now
    session.merge(Issue(**irow))
    stats.issues = 1

    key = irow["key"]
    session.query(IssueLabel).filter_by(issue_key=key).delete()
    for label in irow["labels"] or []:
        session.merge(IssueLabel(issue_key=key, label=label))

    cl_rows = mapping.changelog_rows(raw_issue)
    for r in cl_rows:
        session.merge(Changelog(**r))
    stats.changelog_rows = len(cl_rows)

    for wrow in mapping.worklog_rows(key, raw_worklogs):
        wrow["synced_at"] = now
        session.merge(Worklog(**wrow))
        stats.worklogs += 1

    stats.intervals = _rebuild_intervals(session, key, irow["created_at"], raw_issue, cl_rows)
    return stats


def load_users_from_csv(session_factory: sessionmaker[Session], csv_path: Path) -> int:
    """Load/refresh the user->region map into ``isreq.users`` (T060, sync.md #6, FR-026b).

    Regions MUST be one of AMER/EMEA/APAC/Unknown; an out-of-set value is a hard error
    (never silently guessed). Comment lines (``#``) and blanks are skipped. Idempotent:
    upserts on ``account_id``.
    """
    allowed = set(ALLOWED_REGIONS) | {UNKNOWN}
    n = 0
    with session_factory() as session:
        with Path(csv_path).open(newline="") as fh:
            rows = (line for line in fh if line.strip() and not line.lstrip().startswith("#"))
            for row in csv.DictReader(rows):
                region = (row.get("region") or "").strip()
                if region not in allowed:
                    raise ValueError(
                        f"invalid region {region!r} for {row.get('account_id')!r}; "
                        f"must be one of {sorted(allowed)}"
                    )
                ext = (row.get("is_external") or "").strip().lower()
                session.merge(
                    User(
                        account_id=row["account_id"].strip(),
                        display_name=(row.get("display_name") or "").strip() or None,
                        region=region,
                        is_external=ext in {"true", "1", "yes", "y", "t", "external"},
                    )
                )
                n += 1
        session.commit()
    log.info("loaded %d user->region rows", n)
    return n


def _format_watermark(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


def build_jql(cfg, watermark: datetime | None) -> str:
    jql = f"project = {cfg.toml.project_key}"
    if watermark is not None:
        jql += f' AND updated >= "{_format_watermark(watermark)}"'
    return jql + " ORDER BY updated ASC"


def run_sync(
    client: ReadOnlyJiraClient,
    session_factory: sessionmaker[Session],
    cfg,
    *,
    mode: str = "incremental",
    now: datetime | None = None,
) -> SyncStats:
    """Full sync run. Advances the watermark only on success."""
    now = now or datetime.now(timezone.utc)
    total = SyncStats()
    with session_factory() as session:
        state = session.get(SyncState, WATERMARK_RESOURCE)
        watermark = state.last_sync_at if (state and mode == "incremental") else None
        jql = build_jql(cfg, watermark)
        log.info("sync start mode=%s", mode)

        for raw_issue in client.search_issues(jql):
            key = raw_issue["key"]
            if mapping.changelog_truncated(raw_issue):
                raw_issue.setdefault("changelog", {})["histories"] = client.issue_changelog(key)
                total.completed_changelogs += 1
            # Use inline worklogs when they are complete; only call the per-issue
            # endpoint for issues whose worklogs exceed the inline page (FR-004, R-003).
            inline, wtotal = mapping.inline_worklogs(raw_issue)
            worklogs = inline if wtotal <= len(inline) else fetch_complete_worklogs(client, key)
            s = process_issue(session, raw_issue, worklogs, cfg, now=now)
            total.issues += s.issues
            total.changelog_rows += s.changelog_rows
            total.worklogs += s.worklogs
            total.intervals += s.intervals
            if total.issues % 250 == 0:
                session.flush()
                log.info("synced %d issues so far...", total.issues)

        # advance watermark only after the whole pass succeeded
        session.merge(SyncState(resource=WATERMARK_RESOURCE, last_sync_at=now,
                                last_full_sync_at=now if mode == "full" else
                                (state.last_full_sync_at if state else None)))
        session.commit()

    log.info("sync done issues=%d changelog=%d worklogs=%d intervals=%d completed_changelogs=%d",
             total.issues, total.changelog_rows, total.worklogs, total.intervals,
             total.completed_changelogs)
    return total
