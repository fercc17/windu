"""Load OpenStack host-aggregate membership onto ``Node.host_aggregate``.

Host aggregates aren't in Netbox or the juju fixtures, so they're supplied as
committed CSVs under ``data/host_aggregates/`` (``node,host_aggregate``). This is
the single place that knows how to apply them; it is called both by the
``load_host_aggregates`` management command and at the end of ``reconcile_netbox``
(so aggregates are re-applied every time nodes are seeded — solving the
migrate-before-nodes ordering problem).
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path

from django.conf import settings

from .models import Node

logger = logging.getLogger(__name__)

DATA_DIR = Path(settings.BASE_DIR) / "data" / "host_aggregates"


def load_csv(path: Path, *, dry_run: bool = False) -> tuple[int, int]:
    """Apply one ``node,host_aggregate`` CSV. Returns (matched, missing).

    Hostnames are matched case-insensitively (Netbox stores them mixed-case).
    """
    with path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    if rows and ("node" not in rows[0] or "host_aggregate" not in rows[0]):
        raise ValueError(f"{path}: expected header 'node,host_aggregate'")

    matched = missing = 0
    for row in rows:
        hostname = (row.get("node") or "").strip()
        aggregate = (row.get("host_aggregate") or "").strip()
        if not hostname:
            continue
        qs = Node.objects.filter(hostname__iexact=hostname)
        if not qs.exists():
            missing += 1
            logger.warning("no Node matches hostname %r (%s)", hostname, path.name)
            continue
        if not dry_run:
            qs.update(host_aggregate=aggregate)
        matched += 1
    return matched, missing


def apply_all(*, dry_run: bool = False) -> tuple[int, int]:
    """Apply every CSV in ``DATA_DIR``. Returns (total_matched, total_missing)."""
    if not DATA_DIR.exists():
        logger.info("no host-aggregate data dir at %s — skipping", DATA_DIR)
        return 0, 0
    total_matched = total_missing = 0
    for path in sorted(DATA_DIR.glob("*.csv")):
        matched, missing = load_csv(path, dry_run=dry_run)
        total_matched += matched
        total_missing += missing
    return total_matched, total_missing
