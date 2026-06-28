"""SQLAlchemy ORM models — all objects live in the ``isreq`` schema (Art. VIII).

Mirrors data-model.md. Types are written portably (``JSON`` carries a ``JSONB``
variant on Postgres) so the same models run against Postgres in production and an
in-memory SQLite (with an attached ``isreq`` schema) in tests.

The schema name is read once from ``ISREQ_DB_SCHEMA`` (default ``isreq``); set it to
empty for an unschema'd backend.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    TypeDecorator,
    false,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

SCHEMA = os.environ.get("ISREQ_DB_SCHEMA", "isreq") or None

# JSONB on Postgres, plain JSON elsewhere (SQLite tests).
JSON_VARIANT = JSON().with_variant(JSONB(), "postgresql")
# bigserial on Postgres; autoincrementing INTEGER ROWID alias on SQLite (tests).
AUTO_BIGINT = BigInteger().with_variant(Integer(), "sqlite")


class UTCDateTime(TypeDecorator):
    """timestamptz that always yields tz-aware UTC datetimes.

    Postgres returns aware datetimes already; SQLite (used in tests) drops tzinfo,
    so we re-attach UTC on the way out. This keeps the pure-domain interval logic
    working on aware datetimes identically on both backends.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    def process_result_value(self, value, dialect):  # noqa: ANN001
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


def _fk(target: str) -> str:
    return f"{SCHEMA}.{target}" if SCHEMA else target


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


class Issue(Base):
    __tablename__ = "issues"

    key: Mapped[str] = mapped_column(Text, primary_key=True)  # ISREQ-NNN
    jira_id: Mapped[int | None] = mapped_column(BigInteger)
    title: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    current_status: Mapped[str | None] = mapped_column(Text)
    current_priority: Mapped[str | None] = mapped_column(Text)  # display only (Art. VII)
    assignee_account_id: Mapped[str | None] = mapped_column(Text)
    assignee_name: Mapped[str | None] = mapped_column(Text)
    # Reporter = who raised the ticket. Joined to users.is_external to tell apart a
    # Highest raised inside the IS team vs by an external requester (FR/issue #7).
    reporter_account_id: Mapped[str | None] = mapped_column(Text)
    reporter_name: Mapped[str | None] = mapped_column(Text)
    area: Mapped[str | None] = mapped_column(Text)
    sub_area: Mapped[str | None] = mapped_column(Text)
    pulse: Mapped[str | None] = mapped_column(Text)
    is_pr_mp: Mapped[bool] = mapped_column(Boolean, default=False)
    labels: Mapped[list | None] = mapped_column(JSON_VARIANT)
    jira_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("ix_issues_created_at", "created_at"),
        Index("ix_issues_current_status", "current_status"),
        Index("ix_issues_area_sub_area", "area", "sub_area"),
        Index("ix_issues_is_pr_mp", "is_pr_mp"),
    )


class IssueLabel(Base):
    __tablename__ = "issue_labels"

    issue_key: Mapped[str] = mapped_column(ForeignKey(_fk("issues.key")), primary_key=True)
    label: Mapped[str] = mapped_column(Text, primary_key=True)

    __table_args__ = (Index("ix_issue_labels_label", "label"),)


class Changelog(Base):
    __tablename__ = "changelog"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    issue_key: Mapped[str] = mapped_column(ForeignKey(_fk("issues.key")))
    field: Mapped[str] = mapped_column(Text)
    from_value: Mapped[str | None] = mapped_column(Text)
    to_value: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    author_account_id: Mapped[str | None] = mapped_column(Text)
    author_name: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_changelog_issue_field_at", "issue_key", "field", "changed_at"),)


class Worklog(Base):
    __tablename__ = "worklogs"
    # No author column — per-person attribution is forbidden (FR-018).

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    issue_key: Mapped[str] = mapped_column(ForeignKey(_fk("issues.key")))
    time_spent_seconds: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("ix_worklogs_started_at", "started_at"),
        Index("ix_worklogs_issue_key", "issue_key"),
    )


class User(Base):
    __tablename__ = "users"

    account_id: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str] = mapped_column(String(8))  # AMER/EMEA/APAC/Unknown
    # team-membership, independent of region (an external may still have a known
    # region, e.g. an EMEA-based requester who isn't on the IS team).
    is_external: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                              server_default=false())


class SyncState(Base):
    __tablename__ = "sync_state"

    resource: Mapped[str] = mapped_column(Text, primary_key=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_full_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    note: Mapped[str | None] = mapped_column(Text)


class PriorityInterval(Base):
    __tablename__ = "priority_intervals"

    id: Mapped[int] = mapped_column(AUTO_BIGINT, primary_key=True, autoincrement=True)
    issue_key: Mapped[str] = mapped_column(ForeignKey(_fk("issues.key")))
    priority: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime())
    valid_to: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("ix_pri_iv_issue_from", "issue_key", "valid_from"),
        Index("ix_pri_iv_priority_span", "priority", "valid_from", "valid_to"),
    )


class StatusInterval(Base):
    __tablename__ = "status_intervals"

    id: Mapped[int] = mapped_column(AUTO_BIGINT, primary_key=True, autoincrement=True)
    issue_key: Mapped[str] = mapped_column(ForeignKey(_fk("issues.key")))
    status: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime())
    valid_to: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("ix_stat_iv_issue_from", "issue_key", "valid_from"),
        Index("ix_stat_iv_status_span", "status", "valid_from", "valid_to"),
    )
