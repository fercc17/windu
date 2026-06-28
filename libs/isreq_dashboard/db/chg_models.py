"""ORM models for the Change Management module — all in the ``chg`` schema.

A third, independent co-tenant of the database (after ``isreq`` and ``pd``), with its
own ``DeclarativeBase`` and ``MetaData(schema="chg")``. Unlike the read-only analytics
schemas, this one is written interactively (CRs and maintenance windows are created from
the UI). It owns its own data; it never writes to Jira or PagerDuty.

The schema name is read from ``ISREQ_CHG_SCHEMA`` (default ``chg``); empty for an
unschema'd backend (tests attach a ``chg`` schema on SQLite).
"""

from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, MetaData, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from isreq_dashboard.db.models import AUTO_BIGINT, JSON_VARIANT, UTCDateTime

CHG_SCHEMA = os.environ.get("ISREQ_CHG_SCHEMA", "chg") or None


def _fk(target: str) -> str:
    return f"{CHG_SCHEMA}.{target}" if CHG_SCHEMA else target


class ChgBase(DeclarativeBase):
    metadata = MetaData(schema=CHG_SCHEMA)


class ChgChangeRequest(ChgBase):
    """One change request. ``id`` is the human id (``CR#100`` / ``eCR#200`` / ``sCR#300``);
    ``number`` is the bare incremental integer used to compute the next id per type."""

    __tablename__ = "chg_change_request"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    number: Mapped[int] = mapped_column(Integer)
    change_type: Mapped[str] = mapped_column(Text)  # normal / standard / emergency
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(Text)  # Draft..Closed / Rejected / Cancelled
    risk: Mapped[str | None] = mapped_column(Text)  # low / medium / high
    requested_by: Mapped[str | None] = mapped_column(Text)
    assignee: Mapped[str | None] = mapped_column(Text)
    service: Mapped[str | None] = mapped_column(Text)  # affected service / cloud
    scheduled_start: Mapped[datetime | None] = mapped_column(UTCDateTime())
    scheduled_end: Mapped[datetime | None] = mapped_column(UTCDateTime())
    closure_code: Mapped[str | None] = mapped_column(Text)  # successful / failed / backed_out
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("ix_chg_cr_type", "change_type"),
        Index("ix_chg_cr_stage", "stage"),
        Index("ix_chg_cr_sched", "scheduled_start"),
    )


class ChgMaintenanceWindow(ChgBase):
    """A maintenance window: a time range (optionally tied to a CR) during which work
    happens on one or more services. Local only — never created in PagerDuty."""

    __tablename__ = "chg_maintenance_window"

    id: Mapped[int] = mapped_column(AUTO_BIGINT, primary_key=True, autoincrement=True)
    summary: Mapped[str] = mapped_column(Text)
    cr_id: Mapped[str | None] = mapped_column(ForeignKey(_fk("chg_change_request.id")))
    services: Mapped[list | None] = mapped_column(JSON_VARIANT)  # affected service / cloud names
    start_at: Mapped[datetime] = mapped_column(UTCDateTime())
    end_at: Mapped[datetime] = mapped_column(UTCDateTime())
    status: Mapped[str | None] = mapped_column(Text)  # scheduled / cancelled (live status derived from time)
    created_by: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("ix_chg_mw_start", "start_at"),
        Index("ix_chg_mw_cr", "cr_id"),
    )
