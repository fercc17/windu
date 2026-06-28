"""Views for storage resources (RadosGW buckets)."""
from __future__ import annotations

from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render

from .models import StorageEnvironmentAccess, StorageResource


def storage_list(request):
    """All storage resources with their accessing-environment count (#45)."""
    storages = StorageResource.objects.annotate(
        env_count=Count("environment_accesses", distinct=True)
    )

    # Filters (#45) — applied server-side, all optional.
    q = (request.GET.get("q") or "").strip()
    cloud = (request.GET.get("cloud") or "").strip()
    storage_type = (request.GET.get("type") or "").strip()
    owner_team = (request.GET.get("owner") or "").strip()

    if q:
        storages = storages.filter(Q(name__icontains=q) | Q(bucket_name__icontains=q))
    if cloud:
        storages = storages.filter(cloud=cloud)
    if storage_type:
        storages = storages.filter(storage_type=storage_type)
    if owner_team:
        storages = storages.filter(owner_team=owner_team)

    storages = storages.order_by("name")

    # Distinct option lists for the dropdowns (from the full, unfiltered table).
    base = StorageResource.objects.all()
    clouds = sorted(c for c in base.values_list("cloud", flat=True).distinct() if c)
    owners = sorted(
        o for o in base.values_list("owner_team", flat=True).distinct() if o
    )

    context = {
        "storages": storages,
        "clouds": clouds,
        "owners": owners,
        "storage_types": StorageResource.STORAGE_TYPE_CHOICES,
        "filters": {"q": q, "cloud": cloud, "type": storage_type, "owner": owner_team},
        "active_filters": any([q, cloud, storage_type, owner_team]),
    }
    return render(request, "storage/storage_list.html", context)


def storage_detail(request, name: str):
    """One storage resource and the environments that access it (#45)."""
    storage = get_object_or_404(StorageResource, name=name)
    accesses = (
        StorageEnvironmentAccess.objects.filter(storage=storage)
        .select_related("environment")
        .order_by("environment__name")
    )
    clouds = sorted({a.environment.cloud for a in accesses if a.environment.cloud})
    return render(
        request,
        "storage/storage_detail.html",
        {
            "storage": storage,
            "accesses": accesses,
            "clouds": clouds,
            "cross_cloud": len(clouds) > 1,
        },
    )


def team_storage(request, name: str):
    """All storage owned by a team, with a cross-cloud flag (#45/#53)."""
    storages = (
        StorageResource.objects.filter(owner_team=name)
        .annotate(
            env_count=Count("environment_accesses", distinct=True),
            cloud_count=Count(
                "environment_accesses__environment__cloud", distinct=True
            ),
        )
        .order_by("name")
    )
    rows = [
        {"storage": s, "env_count": s.env_count, "cross_cloud": s.cloud_count > 1}
        for s in storages
    ]
    totals = {"size_gb": sum(s.size_gb for s in storages), "count": len(rows)}
    return render(
        request,
        "storage/team_storage.html",
        {"team": name, "rows": rows, "totals": totals},
    )


def storage_matrix(request):
    """Matrix: rows = storage, columns = teams, cells = environments (#47)."""
    from collections import defaultdict

    accesses = StorageEnvironmentAccess.objects.select_related("storage", "environment")
    cells: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    storages: dict[str, StorageResource] = {}
    teams: set[str] = set()
    for a in accesses:
        team = a.environment.team or a.environment.owner or "—"
        cells[a.storage.name][team].append(a.environment.name)
        storages[a.storage.name] = a.storage
        teams.add(team)

    team_list = sorted(teams)
    rows = [
        {
            "storage": storages[sname],
            "cells": [cells[sname].get(t, []) for t in team_list],
        }
        for sname in sorted(storages)
    ]
    return render(
        request,
        "storage/storage_matrix.html",
        {"teams": team_list, "rows": rows},
    )


def storage_blast_radius(request, name: str):
    """API: GET /api/storage/<name>/blast-radius/ — envs sharing this bucket (#46)."""
    storage = get_object_or_404(StorageResource, name=name)
    accesses = (
        StorageEnvironmentAccess.objects.filter(storage=storage)
        .select_related("environment")
        .order_by("environment__name")
    )
    affected = [
        {
            "environment_name": a.environment.name,
            "depth": 1,
            "dependency_type": "storage",
            "access_type": a.access_type,
            "env_type": a.environment.env_type,
            "status": a.environment.status,
            "criticality_tier": a.environment.criticality_tier,
            "owner": a.environment.owner,
            "team": a.environment.team,
            "region": a.environment.cloud,
        }
        for a in accesses
    ]
    return JsonResponse(
        {
            "target": name,
            "target_type": "storage",
            "affected_count": len(affected),
            "affected_environments": affected,
        }
    )
