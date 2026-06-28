"""Views for physical nodes (and, later, clouds / resilience / maintenance)."""
from __future__ import annotations

from django.contrib import messages
from django.db.models import Count, Exists, OuterRef, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from cmdb.apps.environments.models import Environment, PlacementHistory

from .clouds import (
    MAIN_ARCH,
    all_cloud_summaries,
    capacity_metric_labels,
    cloud_architectures,
    cloud_slugs,
    cloud_summary,
    completeness_band,
    empty_capacity,
)
from .models import Node, NodeCable, NodeSwitchConnection

ENV_TABLE_CAP = 100  # cloud detail inline env table cap (ps6 has ~900 envs)


def node_detail(request, hostname: str):
    """Detail page for one physical node (#27)."""
    node = get_object_or_404(Node, hostname=hostname)

    has_cable = NodeCable.objects.filter(
        Q(interface_a=OuterRef("pk")) | Q(interface_b=OuterRef("pk"))
    )
    interfaces = node.interfaces.annotate(has_cable=Exists(has_cable)).order_by("name")

    primary_envs = node.primary_environments.all().order_by("name")
    secondary_envs = node.secondary_environments.all().order_by("name")

    # PlacementHistory stores hostnames as text; match this node either way.
    base = hostname.split(".")[0]
    placement_history = PlacementHistory.objects.filter(
        Q(primary_node__iexact=hostname) | Q(secondary_node__iexact=hostname)
        | Q(primary_node__iexact=base) | Q(secondary_node__iexact=base)
    ).order_by("-recorded_at")[:10]

    # Active/upcoming maintenance for the red banner (#38).
    from cmdb.apps.maintenance.queries import windows_for_node

    windows = windows_for_node(node)

    context = {
        "node": node,
        "interfaces": interfaces,
        "primary_envs": primary_envs,
        "secondary_envs": secondary_envs,
        "placed_count": primary_envs.count() + secondary_envs.count(),
        "placement_history": placement_history,
        "active_maintenance": windows[0] if windows else None,
    }
    return render(request, "netbox/node_detail.html", context)


def node_resilience(request, hostname: str):
    """Resilience / switch blast-radius page for a node (#42)."""
    node = get_object_or_404(Node, hostname=hostname)
    uplinks = node.switch_connections.all().order_by("switch_hostname")
    switch_hostnames = sorted({u.switch_hostname for u in uplinks})

    by_switch: dict[str, list] = {}
    if switch_hostnames:
        siblings = (
            NodeSwitchConnection.objects.filter(switch_hostname__in=switch_hostnames)
            .exclude(node=node)
            .select_related("node")
            .order_by("switch_hostname", "node__hostname")
        )
        for sc in siblings:
            by_switch.setdefault(sc.switch_hostname, [])
            if sc.node not in by_switch[sc.switch_hostname]:
                by_switch[sc.switch_hostname].append(sc.node)

    return render(
        request,
        "netbox/node_resilience.html",
        {
            "node": node,
            "uplinks": uplinks,
            "switch_hostnames": switch_hostnames,
            "by_switch": by_switch,
        },
    )


def cloud_list(request):
    """List all clouds (union of Node.cloud and Environment.cloud) (#29)."""
    clouds = all_cloud_summaries()
    for c in clouds:
        c["band"] = completeness_band(c["worst_completeness"])
        c["main_arch"] = MAIN_ARCH
        c["capacity"] = empty_capacity()  # placeholders until numbers are collected
    return render(request, "netbox/cloud_list.html", {
        "clouds": clouds,
        "capacity_metrics": capacity_metric_labels(),
    })


def cloud_detail(request, slug: str):
    """Detail page for one cloud (#29) + completeness banner (#28) + stakeholders (#59)."""
    if slug not in cloud_slugs():
        raise Http404(f"Unknown cloud {slug!r}")

    from .models import CloudStakeholder

    cloud_decommissioning = Node.objects.filter(
        cloud=slug, status="decommissioning"
    ).exists()

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            email = (request.POST.get("email") or "").strip()
            name = (request.POST.get("name") or "").strip()
            if not email or not name:
                messages.error(request, "Both name and email are required.")
            elif CloudStakeholder.objects.filter(cloud_slug=slug, email=email).exists():
                messages.info(request, f"{email} is already a stakeholder.")
            else:
                CloudStakeholder.objects.create(cloud_slug=slug, email=email, name=name)
                messages.success(request, f"Added stakeholder {email}.")
        elif action == "remove":
            sid = request.POST.get("stakeholder_id")
            count = CloudStakeholder.objects.filter(cloud_slug=slug).count()
            if cloud_decommissioning and count <= 1:
                messages.error(
                    request,
                    "Cannot remove the last stakeholder while this cloud is "
                    "being decommissioned.",
                )
            else:
                CloudStakeholder.objects.filter(cloud_slug=slug, id=sid).delete()
                messages.success(request, "Stakeholder removed.")
        return redirect("netbox:cloud-detail", slug=slug)

    summary = cloud_summary(slug)
    summary["band"] = completeness_band(summary["worst_completeness"])

    nodes = Node.objects.filter(cloud=slug).order_by("hostname")
    incomplete_nodes = [n for n in nodes if n.physical_completeness < 0.8]

    # Host-aggregate breakdown (OpenStack pools: production / builders / ...).
    aggregate_counts = (
        Node.objects.filter(cloud=slug, host_aggregate__isnull=False)
        .exclude(host_aggregate="")
        .values("host_aggregate")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    environments = Environment.objects.filter(cloud=slug).order_by("name")
    env_total = environments.count()

    context = {
        "slug": slug,
        "summary": summary,
        "nodes": nodes,
        "incomplete_count": len(incomplete_nodes),
        "environments": environments[:ENV_TABLE_CAP],
        "env_total": env_total,
        "env_cap": ENV_TABLE_CAP,
        "env_truncated": env_total > ENV_TABLE_CAP,
        "stakeholders": CloudStakeholder.objects.filter(cloud_slug=slug),
        "cloud_decommissioning": cloud_decommissioning,
        "aggregate_counts": aggregate_counts,
        # Capacity scaffolding: per-architecture rows with placeholder numbers.
        "main_arch": MAIN_ARCH,
        "capacity_metrics": capacity_metric_labels(),
        "arch_capacity": [
            {"arch": arch, "is_main": arch == MAIN_ARCH, "capacity": empty_capacity()}
            for arch in cloud_architectures(slug)
        ],
    }
    return render(request, "netbox/cloud_detail.html", context)


def trigger_netbox_collection(request):
    """Kick off a Netbox reconciliation in the background (#admin action).

    The full ``reconcile_netbox`` sync paginates 1000+ devices and can take
    minutes, so it must not run inside the request. We launch the existing
    management command as a detached subprocess and return immediately; output
    is appended to ``logs/reconcile_netbox.log`` at the repo root.
    """
    import os
    import subprocess
    import sys

    from django.conf import settings

    if request.method != "POST":
        return redirect("netbox:cloud-list")

    if not (os.environ.get("NETBOX_URL") and os.environ.get("NETBOX_TOKEN")):
        messages.error(
            request,
            "NETBOX_URL / NETBOX_TOKEN are not configured — cannot collect "
            "Netbox data. Set them in .env and retry.",
        )
        return redirect("netbox:cloud-list")

    manage_py = settings.BASE_DIR / "manage.py"
    log_dir = settings.BASE_DIR / "logs"
    try:
        log_dir.mkdir(exist_ok=True)
        logfile = open(log_dir / "reconcile_netbox.log", "ab")  # noqa: SIM115
        subprocess.Popen(
            [sys.executable, str(manage_py), "reconcile_netbox"],
            cwd=str(settings.BASE_DIR),
            stdout=logfile,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # detach from the request's process group
        )
        messages.success(
            request,
            "Netbox collection started in the background. Node data will refresh "
            "in a few minutes (see logs/reconcile_netbox.log).",
        )
    except Exception as exc:  # noqa: BLE001
        messages.error(request, f"Failed to start Netbox collection: {exc}")

    return redirect("netbox:cloud-list")
