"""
Load OpenStack host-aggregate membership onto ``Node.host_aggregate``.

Host aggregates are not in Netbox or the juju server-list fixtures, so they are
supplied as CSV files (``node,host_aggregate``) under ``data/host_aggregates/``.
Node hostnames are matched case-insensitively because Netbox stores them
mixed-case (e.g. ``Ps5-Ra1-N1``) while the aggregate dumps are lowercase.

``reconcile_netbox`` calls the same loader at the end of a sync, so normally you
don't need to run this by hand — use it to preview (``--dry-run``) or to re-apply
after editing a CSV.

Run::

    python manage.py load_host_aggregates                      # all CSVs in data dir
    python manage.py load_host_aggregates data/host_aggregates/ps5.csv
    python manage.py load_host_aggregates --dry-run
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from cmdb.apps.netbox import host_aggregates


class Command(BaseCommand):
    help = "Load host-aggregate membership onto Node.host_aggregate from CSV file(s)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "paths", nargs="*",
            help="CSV file(s): node,host_aggregate. Default: all CSVs in data/host_aggregates/.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report matches/misses but write nothing.",
        )

    def handle(self, *args: Any, **opts: Any) -> None:
        dry_run = opts["dry_run"]
        paths = [Path(p) for p in opts["paths"]]

        if paths:
            total_matched = total_missing = 0
            for path in paths:
                if not path.exists():
                    raise CommandError(f"file not found: {path}")
                try:
                    matched, missing = host_aggregates.load_csv(path, dry_run=dry_run)
                except ValueError as exc:
                    raise CommandError(str(exc))
                total_matched += matched
                total_missing += missing
                self.stdout.write(f"{path.name}: matched {matched}, missing {missing}")
        else:
            total_matched, total_missing = host_aggregates.apply_all(dry_run=dry_run)

        msg = (
            f"load_host_aggregates done: matched={total_matched} missing={total_missing}"
            + (" [DRY RUN]" if dry_run else "")
        )
        self.stdout.write(self.style.SUCCESS(msg))
