"""Incremental, idempotent sync into the ``pd`` schema (mirrors ``jira/sync.py``).

The only component that talks to PagerDuty, and only to read. It:
  1. upserts reference data (teams, services, users, escalation policies),
  2. lists incidents (backfill since the configured date, or incrementally since the
     watermark) and, per incident, fetches its alerts and timeline,
  3. derives ack/resolve times from the timeline and cloud/model/charm from each
     alert payload, and upserts incident/alert/log-entry rows on stable keys,
  4. advances the ``pd_sync_state`` watermark ONLY on full success.

All writes are confined to ``pd``; no DROP/TRUNCATE here (Art. VIII). The persist
step takes a plain client, so it is testable against SQLite with the fixture client.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from isreq_dashboard.db.pd_models import (
    PdAlert,
    PdEscalationPolicy,
    PdIncident,
    PdLogEntry,
    PdService,
    PdSyncState,
    PdTeam,
    PdUser,
)
from isreq_dashboard.pagerduty import mapping
from isreq_dashboard.pagerduty.client import ALL_STATUSES, ReadOnlyPagerDutyClient

log = logging.getLogger("isreq.pd_sync")
WATERMARK_RESOURCE = "pd_incidents"


@dataclass
class PdSyncStats:
    incidents: int = 0
    alerts: int = 0
    log_entries: int = 0
    services: int = 0
    teams: int = 0
    users: int = 0
    escalation_policies: int = 0
    errors: list[str] = field(default_factory=list)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_reference(session: Session, client: ReadOnlyPagerDutyClient, team_ids, stats: PdSyncStats) -> None:
    for raw in client.list_teams():
        session.merge(PdTeam(**mapping.team_row(raw)))
        stats.teams += 1
    for raw in client.list_escalation_policies(team_ids=team_ids):
        session.merge(PdEscalationPolicy(**mapping.escalation_policy_row(raw)))
        stats.escalation_policies += 1
    for raw in client.list_services(team_ids=team_ids):
        session.merge(PdService(**mapping.service_row(raw)))
        stats.services += 1
    for raw in client.list_users(team_ids=team_ids):
        session.merge(PdUser(**mapping.user_row(raw)))
        stats.users += 1


def process_incident(session: Session, client: ReadOnlyPagerDutyClient, raw_incident: dict, *, now: datetime) -> tuple[int, int]:
    """Upsert one incident + its alerts + timeline; return (alerts, log_entries)."""
    incident_id = raw_incident["id"]

    raw_logs = client.incident_log_entries(incident_id)
    log_rows = mapping.log_entry_rows(incident_id, raw_logs)
    acknowledged_at, resolved_at = mapping.derive_times(log_rows)

    irow = mapping.incident_row(raw_incident)
    irow["acknowledged_at"] = acknowledged_at
    irow["resolved_at"] = resolved_at
    irow["synced_at"] = now
    session.merge(PdIncident(**irow))

    for lrow in log_rows:
        session.merge(PdLogEntry(**lrow))

    n_alerts = 0
    for raw_alert in client.incident_alerts(incident_id):
        arow = mapping.alert_row(incident_id, raw_alert)
        arow["synced_at"] = now
        session.merge(PdAlert(**arow))
        n_alerts += 1

    return n_alerts, len(log_rows)


def resolve_missing_users(session: Session, client: ReadOnlyPagerDutyClient) -> int:
    """Fetch + upsert any acting users (log-entry agents / incident assignees) not yet in
    ``pd_user`` — typically cross-team responders outside the synced team roster, so every
    acting user maps to a name rather than a bare id. Returns the count newly added."""
    known = set(session.scalars(select(PdUser.id)))
    acting = set(session.scalars(
        select(PdLogEntry.agent_user_id).where(PdLogEntry.agent_user_id.is_not(None)).distinct()
    ))
    acting |= set(session.scalars(
        select(PdIncident.assigned_user_id).where(PdIncident.assigned_user_id.is_not(None)).distinct()
    ))
    n = 0
    for uid in sorted(acting - known):
        raw = client.get_user(uid)
        if raw:
            session.merge(PdUser(**mapping.user_row(raw)))
            n += 1
    return n


def run_sync(
    client: ReadOnlyPagerDutyClient,
    session_factory: sessionmaker[Session],
    settings,
    *,
    mode: str = "incremental",
    now: datetime | None = None,
    commit_every: int = 200,
) -> PdSyncStats:
    """Full sync run. Advances the watermark only on success.

    Incident batches are committed every ``commit_every`` incidents so a large
    backfill (thousands of incidents) persists progress instead of holding one long
    transaction, and is restartable: a run that dies mid-way leaves the committed
    incidents in place and, because the watermark is only advanced at the very end,
    a re-run simply re-upserts (idempotent) from ``since``.
    """
    now = now or datetime.now(timezone.utc)
    pd_cfg = settings.toml.pd
    if pd_cfg is None:
        raise RuntimeError("no [pd] config block; PagerDuty analysis is not configured")
    team_ids = settings.pd_team_ids
    stats = PdSyncStats()

    with session_factory() as session:
        state = session.get(PdSyncState, WATERMARK_RESOURCE)
        watermark = state.last_sync_at if (state and mode == "incremental") else None
        # Backfill from the configured `since` date; incrementally from the watermark.
        since_dt = watermark or datetime(pd_cfg.since.year, pd_cfg.since.month, pd_cfg.since.day, tzinfo=timezone.utc)
        log.info("pd sync start mode=%s since=%s teams=%s", mode, _iso(since_dt), team_ids)

        _load_reference(session, client, team_ids, stats)

        for raw_incident in client.list_incidents(
            since=_iso(since_dt), until=_iso(now), team_ids=team_ids, statuses=ALL_STATUSES
        ):
            n_alerts, n_logs = process_incident(session, client, raw_incident, now=now)
            stats.incidents += 1
            stats.alerts += n_alerts
            stats.log_entries += n_logs
            if stats.incidents % commit_every == 0:
                session.commit()  # persist this batch; restartable on failure
                log.info("synced %d incidents so far...", stats.incidents)

        # resolve cross-team responders the team roster missed, so every acting user
        # maps to a name (not a bare id)
        added = resolve_missing_users(session, client)
        if added:
            stats.users += added
            log.info("resolved %d cross-team responder name(s)", added)

        # advance watermark only after the whole pass succeeded
        session.merge(
            PdSyncState(
                resource=WATERMARK_RESOURCE,
                last_sync_at=now,
                last_full_sync_at=now if mode == "full" else (state.last_full_sync_at if state else None),
            )
        )
        session.commit()

    log.info(
        "pd sync done incidents=%d alerts=%d log_entries=%d services=%d users=%d",
        stats.incidents, stats.alerts, stats.log_entries, stats.services, stats.users,
    )
    return stats
