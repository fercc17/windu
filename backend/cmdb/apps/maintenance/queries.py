"""
Shared queries for "is this under maintenance?" indicators (#38).

A node/environment is flagged when it has a window that is ``active`` or
``scheduled`` to start within the next 24 hours.
"""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .models import MaintenanceWindow

UPCOMING_HOURS = 24


def _active_or_upcoming() -> Q:
    horizon = timezone.now() + timedelta(hours=UPCOMING_HOURS)
    return Q(status="active") | Q(status="scheduled", starts_at__lte=horizon)


def windows_for_node(node) -> "list[MaintenanceWindow]":
    """Active/upcoming windows for a node, soonest first."""
    return list(
        MaintenanceWindow.objects.filter(_active_or_upcoming(), node=node).order_by("starts_at")
    )


def node_ids_under_maintenance() -> set[int]:
    return set(
        MaintenanceWindow.objects.filter(_active_or_upcoming()).values_list("node_id", flat=True)
    )


def environment_ids_under_maintenance() -> set[int]:
    """Environment ids whose primary or secondary node is under maintenance."""
    node_ids = node_ids_under_maintenance()
    if not node_ids:
        return set()
    from cmdb.apps.environments.models import Environment

    return set(
        Environment.objects.filter(
            Q(primary_node_id__in=node_ids) | Q(secondary_node_id__in=node_ids)
        ).values_list("id", flat=True)
    )
