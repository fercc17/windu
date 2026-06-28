"""SQLAlchemy engine bound to the ``isreq`` schema (Art. VIII, research R-009).

Connects as the non-superuser role with ``search_path=isreq`` set via
``connect_args``, so every unqualified statement targets ``isreq`` and nothing
else. The engine never creates/drops objects on its own — schema setup is the
additive Alembic path in ``cli/init_schema.py``.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine

from isreq_dashboard.config import Settings


def make_engine(settings: Settings, *, echo: bool = False, schema: str | None = None) -> Engine:
    """Production engine: Postgres, search_path pinned to the configured schema.

    ``schema`` overrides ``settings.db_schema`` for callers that target a different
    schema in the same database (e.g. the PagerDuty analysis pins ``pd``). The
    default preserves the ISReq behaviour exactly.
    """
    schema = schema or settings.db_schema or "isreq"
    return create_engine(
        settings.sqlalchemy_url(),
        echo=echo,
        pool_pre_ping=True,
        connect_args={"options": f"-csearch_path={schema}"},
    )
