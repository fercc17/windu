"""
Build the node -> switch uplink graph from NodeCable records (#39).

For each cable that connects a server node to a switch node, record a
``NodeSwitchConnection``; a node is marked ``uplink_redundancy=True`` when it
uplinks to two or more distinct switches.

The live Netbox holds **no cable data** (see docs/findings/netbox-audit.md §3),
so on the current data this rebuilds to an empty graph and logs that clearly.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from cmdb.apps.netbox.models import Node, NodeCable, NodeSwitchConnection

logger = logging.getLogger(__name__)

# Heuristic for identifying a switch node (Netbox has no dedicated switch role).
_SWITCH_RE = re.compile(r"(sw\d*|switch|leaf|spine|aggsw|fabric|-tor\b|core\d)", re.I)


def _is_switch(node: Node) -> bool:
    return "switch" in (node.role or "").lower() or bool(_SWITCH_RE.search(node.hostname or ""))


class Command(BaseCommand):
    help = "Rebuild NodeSwitchConnection from NodeCable and set uplink_redundancy."

    def handle(self, *args, **opts) -> None:
        cables = (
            NodeCable.objects
            .select_related("interface_a__node", "interface_b__node")
            .all()
        )
        cable_count = cables.count()

        if cable_count == 0:
            with transaction.atomic():
                NodeSwitchConnection.objects.all().delete()
                Node.objects.filter(uplink_redundancy=True).update(uplink_redundancy=False)
            msg = "No cable data found in Netbox — switch graph is empty."
            logger.info(msg)
            self.stdout.write(self.style.WARNING(msg))
            return

        connections = []
        switches_per_node: dict[int, set[str]] = defaultdict(set)
        for cable in cables:
            a = cable.interface_a
            b = cable.interface_b
            if not a or not b:
                continue
            node_a, node_b = a.node, b.node
            # Orient the cable as server -> switch.
            if _is_switch(node_b) and not _is_switch(node_a):
                server_node, server_if, switch_node, switch_if = node_a, a, node_b, b
            elif _is_switch(node_a) and not _is_switch(node_b):
                server_node, server_if, switch_node, switch_if = node_b, b, node_a, a
            else:
                continue  # server-server or switch-switch: not an uplink
            connections.append(
                NodeSwitchConnection(
                    node=server_node,
                    switch_hostname=switch_node.hostname,
                    interface_name=server_if.name,
                    port_name=switch_if.name,
                )
            )
            switches_per_node[server_node.id].add(switch_node.hostname)

        with transaction.atomic():
            NodeSwitchConnection.objects.all().delete()
            NodeSwitchConnection.objects.bulk_create(connections)
            redundant = [nid for nid, sws in switches_per_node.items() if len(sws) >= 2]
            Node.objects.filter(uplink_redundancy=True).exclude(id__in=redundant).update(
                uplink_redundancy=False
            )
            Node.objects.filter(id__in=redundant).update(uplink_redundancy=True)

        msg = (
            f"build_switch_graph: {cable_count} cables -> {len(connections)} uplinks, "
            f"{len(redundant)} nodes with redundant uplinks."
        )
        logger.info(msg)
        self.stdout.write(self.style.SUCCESS(msg))
