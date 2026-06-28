"""
Link Environment -> Node from live placement data.

After the poller/collector writes placement to Redis, this command matches the
placement ``primary_host`` / ``secondary_host`` (and, as a fallback, the first
two entries of ``hosts``) against ``netbox.Node.hostname`` and sets
``Environment.primary_node`` / ``secondary_node``.

Matching is case-insensitive after stripping the domain suffix, because
OpenStack reports ``ps5-ra1-n1.maas`` while Netbox stores ``Ps5-Ra1-N1``
(see docs/findings/netbox-audit.md §2).

Never creates nodes; only links when a matching Node already exists. Never
*clears* an existing link on a placement gap (a missing Redis key is a poller
health signal, not a reason to forget where an env was placed).

Run::

    python manage.py link_placement_nodes
    python manage.py link_placement_nodes --dry-run
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from django.core.management.base import BaseCommand

from cmdb.apps.environments.models import Environment
from cmdb.apps.netbox.models import Node
from cmdb.redis_client import get_placement

logger = logging.getLogger(__name__)

# Netbox sometimes bakes a human annotation into the hostname field, e.g.
# ``ps7-ra1-n1(GPU)`` / ``ps7-ra5-n1 (GPU)`` (spacing is inconsistent). It is not
# part of the real hostname, so strip a trailing parenthetical before matching.
_TRAILING_ANNOTATION = re.compile(r"\s*\([^)]*\)\s*$")


def _norm(hostname: Optional[str]) -> str:
    """Normalise a hostname for cross-source matching.

    Strips the domain suffix and any trailing parenthetical annotation, then
    lowercases — so Netbox's ``ps7-ra5-n1 (GPU)`` matches OpenStack's
    ``ps7-ra5-n1.ps7.canonical.com``.
    """
    host = (hostname or "").split(".")[0]
    host = _TRAILING_ANNOTATION.sub("", host)
    return host.strip().lower()


class Command(BaseCommand):
    help = "Link Environment.primary_node/secondary_node from Redis placement."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would change without writing.",
        )

    def handle(self, *args: Any, **opts: Any) -> None:
        dry_run = opts.get("dry_run", False)

        node_by_norm: dict[str, int] = {}
        for pk, hostname in Node.objects.values_list("id", "hostname"):
            node_by_norm[_norm(hostname)] = pk

        with_placement = 0
        set_primary = set_secondary = unmatched = 0

        for env in Environment.objects.all().only(
            "id", "name", "primary_node_id", "secondary_node_id"
        ):
            placement = get_placement(env.name)
            if not placement:
                continue
            with_placement += 1

            hosts = placement.get("hosts") or []
            primary_host = placement.get("primary_host") or (hosts[0] if hosts else None)
            secondary_host = placement.get("secondary_host") or (
                hosts[1] if len(hosts) > 1 else None
            )

            primary_pk = node_by_norm.get(_norm(primary_host)) if primary_host else None
            secondary_pk = node_by_norm.get(_norm(secondary_host)) if secondary_host else None

            if primary_host and primary_pk is None:
                unmatched += 1
            if secondary_host and secondary_pk is None:
                unmatched += 1

            changed_fields = []
            if primary_pk is not None and env.primary_node_id != primary_pk:
                env.primary_node_id = primary_pk
                changed_fields.append("primary_node")
                set_primary += 1
            if secondary_pk is not None and env.secondary_node_id != secondary_pk:
                env.secondary_node_id = secondary_pk
                changed_fields.append("secondary_node")
                set_secondary += 1

            if changed_fields and not dry_run:
                env.save(update_fields=changed_fields)

        msg = (
            f"link_placement_nodes done: envs_with_placement={with_placement} "
            f"primary_set={set_primary} secondary_set={set_secondary} "
            f"unmatched_hosts={unmatched}" + (" [DRY RUN]" if dry_run else "")
        )
        logger.info(msg)
        self.stdout.write(self.style.SUCCESS(msg))
