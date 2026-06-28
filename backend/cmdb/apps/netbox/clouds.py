"""
Cloud aggregation helpers.

Clouds have no model of their own — they are the union of ``Node.cloud`` and
``Environment.cloud`` distinct values, summarised on the fly (#29). Cloud status
is derived from node status (all nodes decommissioning -> the cloud is being
decommissioned), which #56/#59 also rely on.
"""
from __future__ import annotations

from typing import Optional

from django.db.models import Count, Min, Q

from cmdb.apps.environments.models import Environment

from .models import Node

# Roles that count as actual cloud compute/storage nodes.
_CLOUD_NODE_ROLES = ("server", "storage", "hypervisor")

# Hostname patterns for network / facility equipment that Netbox tracks as
# devices but which are NOT cloud nodes (switches, OOB/BMC, firewalls, patch
# panels, PDUs, cable management). A node matching any of these is "equipment".
_EQUIPMENT_HOSTNAME = Q()
for _pat in (
    r"(^|-)(sw|switch|c5k|fex|leaf|spine|tor)(-|[0-9])",  # switches
):
    _EQUIPMENT_HOSTNAME |= Q(hostname__iregex=_pat)
for _sub in ("oob", "-bmc", "-ipmi", "fw", "firewall", "patch", "panel", "pdu",
             "cable-management"):
    _EQUIPMENT_HOSTNAME |= Q(hostname__icontains=_sub)

# Roles that are never cloud nodes regardless of hostname.
_EQUIPMENT_ROLES = ("misc", "scs_console", "corporate-laptop")


def _cloud_node_q() -> Q:
    """Q matching genuine cloud nodes (compute/storage), excluding equipment."""
    return (
        Q(role__in=_CLOUD_NODE_ROLES)
        & ~Q(role__in=_EQUIPMENT_ROLES)
        & ~_EQUIPMENT_HOSTNAME
    )


def node_split(slug: str) -> dict:
    """Counts of cloud nodes vs network/facility equipment for a cloud."""
    nodes = Node.objects.filter(cloud=slug)
    cloud_nodes = nodes.filter(_cloud_node_q()).count()
    total = nodes.count()
    return {"cloud_nodes": cloud_nodes, "equipment": total - cloud_nodes, "total": total}

_NAMED_PROVIDERS = {"aws": "aws", "gcp": "gcp", "azure": "azure"}

# Clouds whose provider is microcloud (edge sites and ic* clusters).
_MICROCLOUD_PREFIXES = ("edge", "ic", "microcloud")

# Authoritative region for clouds where it can't be derived from env data
# (edge/microcloud clouds have no region-tagged environments).
_CLOUD_REGION_OVERRIDES = {
    "tmo": "apac",
    "bjp": "apac",
    "csb-cage01": "amer",
    "edge-et3": "amer",
}


def cloud_slugs() -> list[str]:
    """Sorted union of Node.cloud and Environment.cloud distinct values."""
    node_clouds = set(
        Node.objects.exclude(cloud="").values_list("cloud", flat=True).distinct()
    )
    env_clouds = set(
        Environment.objects.exclude(cloud__isnull=True)
        .exclude(cloud="")
        .values_list("cloud", flat=True)
        .distinct()
    )
    return sorted(node_clouds | env_clouds)


def cloud_provider(slug: str) -> str:
    if slug in _NAMED_PROVIDERS:
        return _NAMED_PROVIDERS[slug]
    if slug.startswith(_MICROCLOUD_PREFIXES):
        return "microcloud"
    return "openstack"


def cloud_region(slug: str) -> Optional[str]:
    """Region for a cloud: explicit override, else most common env region."""
    if slug in _CLOUD_REGION_OVERRIDES:
        return _CLOUD_REGION_OVERRIDES[slug]
    row = (
        Environment.objects.filter(cloud=slug)
        .exclude(region__isnull=True)
        .exclude(region="")
        .values("region")
        .annotate(n=Count("id"))
        .order_by("-n")
        .first()
    )
    return row["region"] if row else None


def cloud_status(slug: str) -> str:
    """active / decommissioning / unknown, derived from node status."""
    nodes = Node.objects.filter(cloud=slug)
    total = nodes.count()
    if total == 0:
        return "unknown"
    if nodes.exclude(status="decommissioning").count() == 0:
        return "decommissioning"
    return "active"


def cloud_summary(slug: str) -> dict:
    """One row for the cloud list / header (#29)."""
    nodes = Node.objects.filter(cloud=slug)
    split = node_split(slug)
    return {
        "slug": slug,
        "region": cloud_region(slug),
        "provider": cloud_provider(slug),
        "status": cloud_status(slug),
        "node_count": split["total"],
        "cloud_node_count": split["cloud_nodes"],
        "equipment_count": split["equipment"],
        "environment_count": Environment.objects.filter(cloud=slug).count(),
        # Worst (minimum) node completeness in the cloud; None if no nodes.
        "worst_completeness": nodes.aggregate(m=Min("physical_completeness"))["m"],
    }


def all_cloud_summaries() -> list[dict]:
    return [cloud_summary(slug) for slug in cloud_slugs()]


def completeness_band(value: Optional[float]) -> str:
    """green >= 0.8, amber 0.5-0.8, red < 0.5, grey if unknown."""
    if value is None:
        return "grey"
    if value >= 0.8:
        return "green"
    if value >= 0.5:
        return "amber"
    return "red"


# --- Capacity scaffolding -------------------------------------------------
# Each cloud has a primary architecture; only AMD is tracked for now.
MAIN_ARCH = "AMD"

# Resource metrics shown on the cloud pages. Real figures are NOT collected
# yet, so empty_capacity() returns placeholders (None) which the templates
# render as "—". Populate the values here once a source exists.
CAPACITY_METRICS = (
    ("vcpu", "vCPU"),
    ("ram", "RAM"),
    ("slow_ceph", "Slow ceph"),
    ("fast_ceph", "Fast ceph"),
    ("instance_storage", "Instance storage"),
    ("quota_vcpu", "Quota vCPU"),
    ("quota_ram", "Quota RAM"),
    ("quota_instance_storage", "Quota instance storage"),
)

# Map env compute_architecture values to the labels used on the cloud pages.
_ARCH_LABELS = {
    "amd64": "AMD",
    "arm64": "ARM",
    "ppc64el": "PowerPC",
    "s390x": "s390x",
    "riscv64": "RISC-V",
    "multi": "Multi",
}


def capacity_metric_labels() -> list[str]:
    """Column labels for the capacity tables, in display order."""
    return [label for _, label in CAPACITY_METRICS]


def empty_capacity() -> list[dict]:
    """Placeholder max / used% / available per metric (numbers not collected)."""
    return [
        {"key": key, "label": label, "max": None, "used_pct": None, "available": None}
        for key, label in CAPACITY_METRICS
    ]


def cloud_architectures(slug: str) -> list[str]:
    """Architecture labels present in a cloud (from env compute_architecture).

    The main arch (AMD) is always included and listed first.
    """
    raw = (
        Environment.objects.filter(cloud=slug)
        .exclude(compute_architecture__isnull=True)
        .exclude(compute_architecture="")
        .values_list("compute_architecture", flat=True)
        .distinct()
    )
    labels = {_ARCH_LABELS.get(a, a) for a in raw}
    labels.discard(MAIN_ARCH)
    return [MAIN_ARCH] + sorted(labels)
