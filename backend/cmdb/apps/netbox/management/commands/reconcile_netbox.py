"""
Nightly full reconciliation of Node/NodeInterface/NodeCable against Netbox.

Paginates every ``dcim/devices/`` (then interfaces and cables) and upserts them
idempotently on ``netbox_id``. Devices present in the DB but absent from Netbox
are soft-deleted (``status='decommissioning'``). ``physical_completeness`` is
recomputed per node as ``interfaces_with_cables / total_interfaces``.

Run::

    python manage.py reconcile_netbox            # full sync
    python manage.py reconcile_netbox --max-pages 2 --dry-run   # safe probe

Rate limiting (``time.sleep(0.1)`` between pages) is handled by ``NetboxClient``.
"""
from __future__ import annotations

import logging
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Q

from cmdb.apps.netbox.models import Node, NodeInterface, NodeCable
from cmdb.apps.netbox.sync import upsert_node_from_device
from cmdb.integrations.netbox_client import NetboxClient

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Reconcile Node/NodeInterface/NodeCable against the live Netbox instance."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--max-pages", type=int, default=None,
            help="Limit pages per endpoint (testing). Disables decommissioning.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Fetch and report counts but write nothing.",
        )

    def handle(self, *args: Any, **opts: Any) -> None:
        max_pages = opts.get("max_pages")
        dry_run = opts.get("dry_run", False)
        partial = max_pages is not None

        client = NetboxClient()
        inserted = updated = errors = 0
        seen_ids: set[int] = set()

        # --- 1. Devices -> Node --------------------------------------------
        for device in client.paginate("dcim/devices/", max_pages=max_pages):
            nid = device.get("id")
            if nid is None:
                continue
            seen_ids.add(nid)
            if dry_run:
                continue
            try:
                _node, created = upsert_node_from_device(device)
                inserted += int(created)
                updated += int(not created)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.warning("device %s skipped: %s", nid, exc)

        # --- 2. Interfaces -> NodeInterface --------------------------------
        node_by_netbox: dict[int, int] = dict(
            Node.objects.values_list("netbox_id", "id")
        )
        iface_synced = 0
        for iface in client.paginate("dcim/interfaces/", max_pages=max_pages):
            dev_id = (iface.get("device") or {}).get("id")
            node_pk = node_by_netbox.get(dev_id)
            if node_pk is None or dry_run:
                continue
            speed = iface.get("speed")  # Netbox reports kbps
            try:
                NodeInterface.objects.update_or_create(
                    netbox_id=iface["id"],
                    defaults={
                        "node_id": node_pk,
                        "name": iface.get("name") or "",
                        "mac_address": iface.get("mac_address") or None,
                        "speed_mbps": (speed // 1000) if speed else None,
                    },
                )
                iface_synced += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("interface %s skipped: %s", iface.get("id"), exc)

        # --- 3. Cables -> NodeCable ----------------------------------------
        iface_by_netbox: dict[int, int] = dict(
            NodeInterface.objects.values_list("netbox_id", "id")
        )
        cable_synced = 0
        for cable in client.paginate("dcim/cables/", max_pages=max_pages):
            if dry_run:
                continue
            a_id = _first_interface_termination(cable.get("a_terminations"))
            b_id = _first_interface_termination(cable.get("b_terminations"))
            a_pk = iface_by_netbox.get(a_id)
            if a_pk is None:
                continue  # cable not between known interfaces
            try:
                NodeCable.objects.update_or_create(
                    netbox_id=cable["id"],
                    defaults={
                        "interface_a_id": a_pk,
                        "interface_b_id": iface_by_netbox.get(b_id),
                        "cable_type": (cable.get("type") or {}).get("value")
                        if isinstance(cable.get("type"), dict) else cable.get("type"),
                    },
                )
                cable_synced += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("cable %s skipped: %s", cable.get("id"), exc)

        # --- 4. Recompute physical_completeness ----------------------------
        completeness_updates = 0
        if not dry_run:
            completeness_updates = self._recompute_completeness()

        # --- 5. Soft-delete absent devices (full sync only) ----------------
        decommissioned = 0
        if partial:
            logger.info("partial run (--max-pages) — skipping decommission step")
        elif not dry_run and seen_ids:
            decommissioned = (
                Node.objects.exclude(netbox_id__in=seen_ids)
                .exclude(status="decommissioning")
                .update(status="decommissioning")
            )

        # --- 6. Apply host-aggregate membership ----------------------------
        # Runs here, AFTER nodes are upserted, so the committed CSVs in
        # data/host_aggregates/ always land on freshly-seeded nodes (the
        # migrate-then-reconcile ordering otherwise leaves the field empty).
        agg_matched = agg_missing = 0
        if not dry_run:
            from cmdb.apps.netbox import host_aggregates
            agg_matched, agg_missing = host_aggregates.apply_all()

        msg = (
            f"reconcile_netbox done: seen={len(seen_ids)} inserted={inserted} "
            f"updated={updated} errors={errors} interfaces={iface_synced} "
            f"cables={cable_synced} completeness_recalc={completeness_updates} "
            f"decommissioned={decommissioned} "
            f"host_aggregates={agg_matched} (missing {agg_missing})"
            + (" [DRY RUN]" if dry_run else "")
        )
        logger.info(msg)
        self.stdout.write(self.style.SUCCESS(msg))

    @staticmethod
    def _recompute_completeness() -> int:
        """Set physical_completeness = cabled_ifaces / total_ifaces per node."""
        nodes = Node.objects.annotate(
            total_ifaces=Count("interfaces", distinct=True),
            cabled_ifaces=Count(
                "interfaces",
                filter=Q(interfaces__cables_as_a__isnull=False)
                | Q(interfaces__cables_as_b__isnull=False),
                distinct=True,
            ),
        )
        changed = []
        for node in nodes:
            comp = (node.cabled_ifaces / node.total_ifaces) if node.total_ifaces else 0.0
            if abs(comp - node.physical_completeness) > 1e-9:
                node.physical_completeness = comp
                changed.append(node)
        if changed:
            with transaction.atomic():
                Node.objects.bulk_update(changed, ["physical_completeness"])
        return len(changed)


def _first_interface_termination(terminations: Any) -> int | None:
    """Return the netbox interface id of the first dcim.interface termination."""
    if not isinstance(terminations, list):
        return None
    for term in terminations:
        if not isinstance(term, dict):
            continue
        otype = term.get("object_type")
        if otype in (None, "dcim.interface"):
            obj = term.get("object") or {}
            oid = term.get("object_id") or obj.get("id")
            if oid:
                return oid
    return None
