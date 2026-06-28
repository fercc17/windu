"""DESTRUCTIVE reset — explicitly human-invoked, NEVER on boot or the sync timer.

This is the ONLY module permitted to DROP/TRUNCATE ``isreq`` objects (Art. VIII).
It is physically separate from sync/startup so that guarantee is structural, not
behavioural. It refuses to run without an explicit confirmation flag.
"""

from __future__ import annotations

import argparse
import sys

from isreq_dashboard.config import Settings
from isreq_dashboard.db.engine import make_engine
from isreq_dashboard.db.models import Base


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DESTRUCTIVE: drop all isreq tables.")
    parser.add_argument(
        "--yes-i-am-sure",
        action="store_true",
        help="required confirmation; without it this command refuses to run",
    )
    args = parser.parse_args(argv)

    if not args.yes_i_am_sure:
        print(
            "Refusing to drop anything. This DESTROYS all isreq data.\n"
            "Re-run with --yes-i-am-sure if you really mean it.",
            file=sys.stderr,
        )
        return 2

    settings = Settings.load()
    engine = make_engine(settings)
    print("Dropping all isreq.* tables ...")
    Base.metadata.drop_all(engine)  # confined to the isreq schema MetaData
    print("Done. Re-run init_schema to recreate (additive).")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
