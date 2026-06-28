"""
Refresh the Charmhub release cache (:class:`CharmRelease`).

Reads the distinct charm names already present in ``Environment.charm_versions``
and, for each, asks charmhub.io for the latest revision published to every
channel. The results are cached so the charm "Outdated" view can compare what
we run against what Charmhub ships — WITHOUT touching the infra/terraform repos.

Run::

    python manage.py refresh_charmhub
    python manage.py refresh_charmhub --charm postgresql --charm lego
    python manage.py refresh_charmhub --dry-run
"""
from __future__ import annotations

import logging
from typing import Any

from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime

from cmdb.apps.environments.models import CharmRelease, Environment
from cmdb.integrations.charmhub_client import CharmhubClient, CharmNotFound

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Cache the latest Charmhub revision per channel for every deployed charm."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--charm", action="append", dest="charms", default=None,
            help="Limit to specific charm name(s); repeatable. Default: all deployed charms.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Report but write nothing.")

    def _deployed_charm_names(self) -> list[str]:
        names: set[str] = set()
        for env in Environment.objects.exclude(charm_versions={}):
            names.update(env.charm_versions.keys())
        return sorted(names)

    def handle(self, *args: Any, **opts: Any) -> None:
        dry_run = opts["dry_run"]
        charms = opts["charms"] or self._deployed_charm_names()
        if not charms:
            self.stdout.write("No charms found in Environment.charm_versions — nothing to do.")
            return

        client = CharmhubClient()
        upserted = not_found = errored = 0

        for charm in charms:
            try:
                releases = client.latest_releases(charm)
            except CharmNotFound:
                not_found += 1
                self.stdout.write(self.style.WARNING(f"  not on charmhub: {charm}"))
                continue
            except Exception as exc:  # network / HTTP error — skip, keep going
                errored += 1
                logger.warning("charmhub lookup failed for %s: %s", charm, exc)
                self.stdout.write(self.style.ERROR(f"  error: {charm}: {exc}"))
                continue

            for (track, risk), rel in sorted(releases.items()):
                released = parse_datetime(rel.released_at) if rel.released_at else None
                if dry_run:
                    self.stdout.write(
                        f"  [dry-run] {charm} {track}/{risk} -> rev {rel.revision} ({rel.version})"
                    )
                    upserted += 1
                    continue
                CharmRelease.objects.update_or_create(
                    charm=charm, track=track, risk=risk,
                    defaults={
                        "latest_revision": rel.revision,
                        "latest_version": rel.version,
                        "released_at": released,
                    },
                )
                upserted += 1

            self.stdout.write(self.style.SUCCESS(f"  {charm}: {len(releases)} channel(s)"))

        verb = "would upsert" if dry_run else "upserted"
        self.stdout.write(
            f"\nDone. {verb} {upserted} channel rows across {len(charms)} charm(s); "
            f"{not_found} not on charmhub, {errored} errored."
        )
