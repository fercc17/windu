"""systemd-timer entrypoint: read-only incremental sync into ``isreq`` (Art. X).

Idempotent and safe to re-run. Loads the user->region CSV, then runs the sync.
Never prints secrets (Settings masks them). This is NOT where schema is created
(see init_schema) and NEVER drops anything (see admin_reset).
"""

from __future__ import annotations

import argparse
import logging
import sys

from isreq_dashboard.config import Settings
from isreq_dashboard.db.engine import make_engine
from isreq_dashboard.db.session import make_session_factory
from isreq_dashboard.jira import sync
from isreq_dashboard.jira.client import JiraClient


def _logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ISReq read-only sync (issues + changelog + worklog).")
    parser.add_argument("--full", action="store_true", help="full backfill (still additive, upsert-only)")
    parser.add_argument("--discover-fields", action="store_true",
                        help="list Jira fields to resolve area/sub-area/pulse ids, then exit")
    args = parser.parse_args(argv)
    _logging()
    log = logging.getLogger("isreq.sync_main")

    settings = Settings.load()  # raises loudly on missing/malformed config
    client = JiraClient(settings.jira_base_url, settings.jira_email, settings.jira_api_token)

    if args.discover_fields:
        for f in client.list_fields():
            print(f"{f.get('id')}\t{f.get('name')}")
        return 0

    engine = make_engine(settings)
    factory = make_session_factory(engine)

    if settings.users_csv and settings.users_csv.is_file():
        sync.load_users_from_csv(factory, settings.users_csv)
    else:
        log.warning("users CSV not found at %s; per-user region will be Unknown", settings.users_csv)

    stats = sync.run_sync(client, factory, settings, mode="full" if args.full else "incremental")
    log.info("sync complete: %s issues, %s worklogs", stats.issues, stats.worklogs)
    client.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
