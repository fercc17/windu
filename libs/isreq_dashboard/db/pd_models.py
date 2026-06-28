"""SQLAlchemy ORM models for the PagerDuty analysis — all in the ``pd`` schema.

Deliberately separate from ``db/models.py`` (the audited ``isreq`` models): its own
``DeclarativeBase`` and its own ``MetaData(schema="pd")``, with no foreign keys to
``isreq`` and no shared primary keys. The two analyses are co-tenants of one
database and share nothing but the platform (Art. VIII isolation, PRS "no join").

Portable typing is reused from ``db/models.py`` (``UTCDateTime`` keeps timestamps
tz-aware UTC; ``JSON_VARIANT`` is JSONB on Postgres, plain JSON on the SQLite used
in tests) so the same models run in production and in the test harness with a
``pd`` schema attached.

The schema name is read once from ``ISREQ_PD_SCHEMA`` (default ``pd``); set it empty
for an unschema'd backend.
"""

from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, MetaData, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Reuse the portable column types from the isreq models (importing the module is
# side-effect free beyond defining the isreq Base, which we do not touch here).
from isreq_dashboard.db.models import JSON_VARIANT, UTCDateTime

PD_SCHEMA = os.environ.get("ISREQ_PD_SCHEMA", "pd") or None


def _fk(target: str) -> str:
    return f"{PD_SCHEMA}.{target}" if PD_SCHEMA else target


class PdBase(DeclarativeBase):
    metadata = MetaData(schema=PD_SCHEMA)


class PdUser(PdBase):
    """A PagerDuty user (an SRE). ``region`` is derived from the user->region map."""

    __tablename__ = "pd_user"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)  # AMER/EMEA/APAC/Unknown


class PdTeam(PdBase):
    __tablename__ = "pd_team"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)


class PdEscalationPolicy(PdBase):
    __tablename__ = "pd_escalation_policy"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)


class PdService(PdBase):
    __tablename__ = "pd_service"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    team_id: Mapped[str | None] = mapped_column(Text)
    escalation_policy_id: Mapped[str | None] = mapped_column(Text)


class PdIncident(PdBase):
    """One PagerDuty incident. Ack/resolve times and the on-call SRE are
    incident-level; the cloud/model/charm signal is alert-level (see ``PdAlert``)."""

    __tablename__ = "pd_incident"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    incident_number: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(Text)
    urgency: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    service_id: Mapped[str | None] = mapped_column(Text)
    escalation_policy_id: Mapped[str | None] = mapped_column(Text)
    team_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())  # trigger time
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # first ack
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    assigned_user_id: Mapped[str | None] = mapped_column(Text)
    synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("ix_pd_incident_created_at", "created_at"),
        Index("ix_pd_incident_service", "service_id"),
        Index("ix_pd_incident_assigned", "assigned_user_id"),
    )


class PdAlert(PdBase):
    """One alert under an incident. Carries the derived cloud/model/charm and the
    raw payload (``raw_details``) so the classifier can be re-run as rules improve
    without re-syncing."""

    __tablename__ = "pd_alert"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey(_fk("pd_incident.id")))
    summary: Mapped[str | None] = mapped_column(Text)
    alertname: Mapped[str | None] = mapped_column(Text)  # normalized alert type
    cloud: Mapped[str | None] = mapped_column(Text)
    juju_model: Mapped[str | None] = mapped_column(Text)
    juju_model_uuid: Mapped[str | None] = mapped_column(Text)
    charm: Mapped[str | None] = mapped_column(Text)  # juju application
    juju_unit: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    raw_details: Mapped[dict | None] = mapped_column(JSON_VARIANT)
    synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("ix_pd_alert_incident", "incident_id"),
        Index("ix_pd_alert_alertname", "alertname"),
        Index("ix_pd_alert_cloud", "cloud"),
        Index("ix_pd_alert_model", "juju_model"),
        Index("ix_pd_alert_charm", "charm"),
        Index("ix_pd_alert_created_at", "created_at"),
    )


class PdSyncState(PdBase):
    """Sync watermark for the PagerDuty backfill, kept in ``pd`` (never cross-querying
    ``isreq.sync_state`` — the two analyses share no tables, Art. VIII)."""

    __tablename__ = "pd_sync_state"

    resource: Mapped[str] = mapped_column(Text, primary_key=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_full_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    note: Mapped[str | None] = mapped_column(Text)


class PdLogEntry(PdBase):
    """An incident timeline event (trigger/acknowledge/escalate/resolve/assign).
    Feeds MTTA/MTTR derivation and the who-handled-what SRE measures."""

    __tablename__ = "pd_log_entry"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey(_fk("pd_incident.id")))
    type: Mapped[str | None] = mapped_column(Text)  # trigger/acknowledge/escalate/resolve/assign
    at: Mapped[datetime] = mapped_column(UTCDateTime())
    agent_user_id: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_pd_log_incident_type_at", "incident_id", "type", "at"),
        Index("ix_pd_log_agent", "agent_user_id"),
    )
