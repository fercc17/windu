"""Entrypoint: read-only PagerDuty sync into the ``pd`` schema (mirrors sync_main).

Fixture-first by design: with no ``PD_API_TOKEN`` (or ``--fixture``) it replays a
recorded JSON so the pipeline runs end-to-end locally; once the token is in ``.env``
it talks to PagerDuty for real with no code change. Idempotent and safe to re-run;
never creates schema (see migration 0004) and never drops anything.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from isreq_dashboard.config import Settings
from isreq_dashboard.db.engine import make_engine
from isreq_dashboard.db.session import make_session_factory
from isreq_dashboard.pagerduty import sync
from isreq_dashboard.pagerduty.client import FixturePagerDutyClient, PagerDutyClient

# Shipped recording used when no token is configured (fixture-first development).
DEFAULT_FIXTURE = Path("tests/fixtures/pagerduty/sample_incidents.json")


def _logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PagerDuty read-only sync into the pd schema.")
    parser.add_argument("--full", action="store_true", help="full backfill from the configured `since` (additive, upsert-only)")
    parser.add_argument("--fixture", metavar="PATH", help="replay a recorded JSON instead of calling PagerDuty")
    args = parser.parse_args(argv)
    _logging()
    log = logging.getLogger("isreq.pd_sync_main")

    settings = Settings.load()  # raises loudly on missing/malformed config
    if settings.toml.pd is None:
        log.error("no [pd] block in config.toml; nothing to sync")
        return 2

    if args.fixture:
        log.info("using fixture source: %s", args.fixture)
        client = FixturePagerDutyClient(args.fixture)
    elif settings.pd_api_token:
        log.info("using live PagerDuty source (token present)")
        client = PagerDutyClient(str(settings.pd_api_token), base_url=settings.toml.pd.api_base)
    else:
        log.warning("PD_API_TOKEN not set; falling back to fixture %s (set the token in .env for real data)", DEFAULT_FIXTURE)
        client = FixturePagerDutyClient(DEFAULT_FIXTURE)

    engine = make_engine(settings, schema=settings.pd_db_schema)
    factory = make_session_factory(engine)

    stats = sync.run_sync(client, factory, settings, mode="full" if args.full else "incremental")
    log.info(
        "pd sync complete: %s incidents, %s alerts, %s log entries",
        stats.incidents, stats.alerts, stats.log_entries,
    )
    client.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
