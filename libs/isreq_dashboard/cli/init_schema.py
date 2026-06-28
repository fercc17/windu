"""Additive schema setup — human-invoked (Art. VIII, research R-009).

Runs ``alembic upgrade head`` (additive, ``isreq``-scoped, never drops). If Alembic
is unavailable, falls back to ``create_all`` — which only issues ``CREATE TABLE``
for missing tables and never drops, so it is equally additive. Re-running is a no-op.
This NEVER runs on boot or on the sync timer.
"""

from __future__ import annotations

import logging
import sys

from isreq_dashboard.config import Settings
from isreq_dashboard.db.engine import make_engine
from isreq_dashboard.db.models import Base


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("isreq.init_schema")
    settings = Settings.load()
    engine = make_engine(settings)

    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config("alembic.ini")
        cfg.attributes["connection"] = engine
        command.upgrade(cfg, "head")
        log.info("alembic upgrade head complete (additive)")
    except ModuleNotFoundError:
        log.warning("alembic not installed; using additive create_all (CREATE TABLE IF NOT EXISTS)")
        Base.metadata.create_all(engine)  # additive only — never drops
        log.info("create_all complete")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
