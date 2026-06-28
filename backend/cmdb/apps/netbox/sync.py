"""
Shared Netbox ``dcim.device`` -> ``Node`` sync logic.

Used by both the webhook receiver (#23) and the nightly reconciliation command
(#24) so the field mapping and the cloud-derivation rule live in one place.

Idempotent: upserts are keyed on ``netbox_id`` via ``update_or_create`` (the ORM
equivalent of INSERT ... ON CONFLICT DO UPDATE) and soft-delete only — a device
that vanishes is marked ``status='decommissioning'``, never deleted.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from .models import Node

logger = logging.getLogger(__name__)

# Hostname prefixes that directly name a cloud.
_PREFIX_CLOUDS = ("ps5", "ps6", "ps7", "ps8")

# Netbox site slug -> cloud, for devices whose hostname does not start with a
# recognised cloud prefix (see docs/findings/netbox-audit.md §2).
_SITE_CLOUD = {
    "il3": "ps5",
    "csb-cage02": "ps6",
    "drs": "ps7",
    "vl2": "ps8",
    "tel": "edge-tel",
    "tor3": "edge-et3",
}


def cloud_from_hostname(hostname: str, site: Optional[str] = None) -> str:
    """Best-effort cloud for a node, by hostname prefix then site (§2)."""
    h = (hostname or "").strip().lower()
    for cloud in _PREFIX_CLOUDS:
        if h.startswith(cloud):
            return cloud
    if h.startswith("et3"):
        return "edge-et3"
    if h.startswith("tel") or "tel-is" in h or "shelf-tel" in h:
        return "edge-tel"
    if site and site in _SITE_CLOUD:
        return _SITE_CLOUD[site]
    return site or "unknown"


def _nested(d: dict, *keys: str) -> Any:
    """Walk nested dicts safely; return None on any miss."""
    cur: Any = d
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def node_fields_from_device(device: dict[str, Any]) -> dict[str, Any]:
    """Map a Netbox device payload to ``Node`` field kwargs (excludes netbox_id)."""
    name = device.get("name") or ""
    site = _nested(device, "site", "slug") or _nested(device, "site", "name") or ""
    role = (
        _nested(device, "role", "slug")
        or _nested(device, "device_role", "slug")   # Netbox <4.0 key
        or ""
    )
    rack = _nested(device, "rack", "name")
    status = _nested(device, "status", "value")
    if not status and isinstance(device.get("status"), str):
        status = device["status"]
    primary_ip = (
        _nested(device, "primary_ip", "address")
        or _nested(device, "primary_ip4", "address")
    )
    if primary_ip and "/" in primary_ip:
        primary_ip = primary_ip.split("/")[0]  # strip CIDR mask
    return {
        "hostname": name,
        "site": site,
        "cloud": cloud_from_hostname(name, site),
        "role": role,
        "rack": rack,
        "status": status or "active",
        "primary_ip": primary_ip,
    }


def upsert_node_from_device(device: dict[str, Any]) -> tuple[Node, bool]:
    """Idempotent upsert of a Node from a Netbox device payload."""
    netbox_id = device.get("id")
    if netbox_id is None:
        raise ValueError("device payload missing 'id'")
    fields = node_fields_from_device(device)
    if not fields["hostname"]:
        raise ValueError(f"device {netbox_id} has no name")
    node, created = Node.objects.update_or_create(
        netbox_id=netbox_id, defaults=fields
    )
    return node, created
