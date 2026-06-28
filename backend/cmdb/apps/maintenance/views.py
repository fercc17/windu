"""Views for maintenance windows."""
from __future__ import annotations

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from cmdb.apps.environments.models import Environment
from cmdb.apps.netbox.models import Node
from cmdb.integrations import pagerduty

from django.db.models import Count

from .forms import MaintenanceWindowForm
from .models import (
    MaintenanceNotificationChannel,
    MaintenanceWindow,
    MaintenanceWindowEnvironment,
)
from .notifications import trigger_notifications


def _affected_environments(node: Node):
    """Environments whose primary or secondary node is this node (blast radius)."""
    return (
        Environment.objects.filter(Q(primary_node=node) | Q(secondary_node=node))
        .distinct()
        .order_by("name")
    )


def _create_window(request, *, scope_kwargs: dict, affected: list, redirect_to):
    """Shared maintenance-window creation flow for all three scopes.

    ``scope_kwargs`` carries exactly one of node= / cloud= / environment=.
    Silence channel is chosen by scope: env -> COS (per-model), node/cloud -> PD.
    Returns (response, created_bool); response is None until POST succeeds.
    """
    is_env_scope = bool(scope_kwargs.get("environment"))

    if request.method == "POST":
        form = MaintenanceWindowForm(request.POST)
        if form.is_valid():
            starts = form.cleaned_data["starts_at"]
            ends = form.cleaned_data["ends_at"]
            if timezone.is_naive(starts):
                starts = timezone.make_aware(starts, timezone.utc)
            if timezone.is_naive(ends):
                ends = timezone.make_aware(ends, timezone.utc)

            window = MaintenanceWindow.objects.create(
                starts_at=starts,
                ends_at=ends,
                reason=form.cleaned_data["reason"],
                created_by="is-cmdb-ui",
                **scope_kwargs,
            )
            MaintenanceWindowEnvironment.objects.bulk_create(
                [MaintenanceWindowEnvironment(window=window, environment=e) for e in affected],
                ignore_conflicts=True,
            )
            # Per-model silencing happens on COS, not PagerDuty (service-scoped).
            channels = form.selected_channels()
            if is_env_scope and "pagerduty" in channels:
                channels.discard("pagerduty")
                channels.add("cos")
            trigger_notifications(window, affected, channels)
            return window, True
        return form, False

    form = MaintenanceWindowForm(initial={"notify_pagerduty": True, "notify_mattermost": True})
    return form, False


def maintenance_window_new(request, hostname: str):
    """Create a maintenance window for a node, with a blast-radius preview (#32)."""
    node = get_object_or_404(Node, hostname=hostname)
    affected = list(_affected_environments(node))

    result, created = _create_window(
        request, scope_kwargs={"node": node}, affected=affected, redirect_to=None
    )
    if created:
        window = result
        messages.success(
            request,
            f"Maintenance window #{window.pk} created for node {node.hostname} "
            f"({len(affected)} environment(s) affected).",
        )
        return redirect("netbox:node-detail", hostname=node.hostname)

    return render(
        request,
        "maintenance/window_form.html",
        {"scope": "node", "target": node.hostname, "form": result, "affected": affected},
    )


def maintenance_window_new_cloud(request, slug: str):
    """Create a maintenance window for a whole cloud (silences its PD service)."""
    affected = list(Environment.objects.filter(cloud=slug).order_by("name"))

    result, created = _create_window(
        request, scope_kwargs={"cloud": slug}, affected=affected, redirect_to=None
    )
    if created:
        window = result
        messages.success(
            request,
            f"Maintenance window #{window.pk} created for cloud {slug} "
            f"({len(affected)} environment(s) affected).",
        )
        return redirect("netbox:cloud-detail", slug=slug)

    return render(
        request,
        "maintenance/window_form.html",
        {"scope": "cloud", "target": slug, "form": result, "affected": affected},
    )


def maintenance_window_new_env(request, name: str):
    """Create a maintenance window for a single juju model (COS silence)."""
    env = get_object_or_404(Environment, name=name)
    affected = [env]

    result, created = _create_window(
        request, scope_kwargs={"environment": env}, affected=affected, redirect_to=None
    )
    if created:
        window = result
        messages.success(
            request,
            f"Maintenance window #{window.pk} created for juju model {env.name} "
            f"(silenced via COS).",
        )
        return redirect("environment-detail", name=env.name)

    return render(
        request,
        "maintenance/window_form.html",
        {"scope": "environment", "target": env.name, "form": result, "affected": affected},
    )


def maintenance_list(request):
    """Table of all maintenance windows (#37)."""
    windows = (
        MaintenanceWindow.objects.select_related("node")
        .annotate(env_count=Count("maintenancewindowenvironment", distinct=True))
        .order_by("-starts_at")
    )
    return render(request, "maintenance/window_list.html", {"windows": windows})


def maintenance_detail(request, pk: int):
    """Detail of one maintenance window: envs affected + notification log (#37)."""
    window = get_object_or_404(MaintenanceWindow.objects.select_related("node"), pk=pk)
    affected = (
        Environment.objects.filter(maintenancewindowenvironment__window=window)
        .order_by("name")
    )
    channels = window.channels.all().order_by("-sent_at")
    can_cancel = window.status in ("scheduled", "active")
    return render(
        request,
        "maintenance/window_detail.html",
        {
            "window": window,
            "affected": affected,
            "channels": channels,
            "can_cancel": can_cancel,
        },
    )


def decommission_log(request):
    """
    Notification log for clouds being decommissioned (#56).

    A cloud is "being decommissioned" when every node in it has
    status='decommissioning' (cloud_status from netbox.clouds). Lists the
    notification-channel records for maintenance windows on those clouds'
    nodes, filterable by cloud and sent_at date range.
    """
    from cmdb.apps.netbox.clouds import cloud_slugs, cloud_status

    decommissioning = [s for s in cloud_slugs() if cloud_status(s) == "decommissioning"]

    channels = (
        MaintenanceNotificationChannel.objects.filter(
            window__node__cloud__in=decommissioning
        )
        .select_related("window__node")
        .order_by("-sent_at")
    )

    cloud_filter = request.GET.get("cloud") or ""
    start = request.GET.get("start") or ""
    end = request.GET.get("end") or ""
    # This log is the node/cloud-decommission audit and renders node links, so it
    # stays scoped to node-targeted windows (env/cloud windows aren't shown here).
    channels = channels.filter(window__node__isnull=False)
    if cloud_filter:
        channels = channels.filter(window__node__cloud=cloud_filter)
    if start:
        channels = channels.filter(sent_at__date__gte=start)
    if end:
        channels = channels.filter(sent_at__date__lte=end)

    rows = []
    for ch in channels:
        env_names = list(
            Environment.objects.filter(
                maintenancewindowenvironment__window_id=ch.window_id
            ).values_list("name", flat=True)[:10]
        )
        rows.append({
            "cloud": ch.window.node.cloud,
            "node": ch.window.node.hostname,
            "window_id": ch.window_id,
            "environments": env_names,
            "channel": ch.get_channel_display(),
            "sent_at": ch.sent_at,
            "success": ch.success,
        })

    return render(
        request,
        "maintenance/decommission_log.html",
        {
            "rows": rows,
            "decommissioning_clouds": decommissioning,
            "cloud_filter": cloud_filter,
            "start": start,
            "end": end,
        },
    )


@require_POST
def maintenance_window_cancel(request, pk: int):
    """Cancel a maintenance window and its PagerDuty silence (#34)."""
    window = get_object_or_404(MaintenanceWindow, pk=pk)
    if window.status in ("cancelled", "completed"):
        messages.info(request, f"Window #{pk} is already {window.status}.")
        return redirect("maintenance:window-detail", pk=pk)

    if window.pagerduty_window_id:
        try:
            if pagerduty.cancel_maintenance_window(window.pagerduty_window_id):
                messages.success(request, "PagerDuty maintenance window cancelled.")
            else:
                messages.warning(
                    request,
                    "PD cancellation skipped (PAGERDUTY_WRITE_TOKEN not set).",
                )
        except NotImplementedError:
            messages.warning(request, "PD cancellation requires write token.")
        except Exception as exc:  # noqa: BLE001
            messages.error(request, f"PD cancellation error: {exc}")

    if window.cos_silence_id:
        from cmdb.integrations import cos
        try:
            if cos.expire_silence(window):
                messages.success(request, "COS silence expired.")
            else:
                messages.warning(
                    request, "COS silence expiry skipped (COS_ALERTMANAGER_URL not set)."
                )
        except NotImplementedError:
            messages.warning(request, "COS silence expiry requires COS_ALERTMANAGER_URL.")
        except Exception as exc:  # noqa: BLE001
            messages.error(request, f"COS silence expiry error: {exc}")

    window.status = "cancelled"
    window.save(update_fields=["status", "updated_at"])

    # Notify Mattermost on close, but only if it was used when the window opened
    # (#35 — notify on open and close). Best-effort; never blocks cancellation.
    if window.channels.filter(channel="mattermost").exists():
        from django.utils import timezone
        from cmdb.integrations import mattermost
        from .models import MaintenanceNotificationChannel, MaintenanceWindowEnvironment
        affected = [
            mwe.environment
            for mwe in MaintenanceWindowEnvironment.objects.filter(
                window=window
            ).select_related("environment")
        ]
        ok, err = mattermost.send_maintenance_notification(window, affected, status="Closed")
        MaintenanceNotificationChannel.objects.create(
            window=window, channel="mattermost", sent_at=timezone.now(),
            success=ok, error_message=err,
        )
        if ok:
            messages.success(request, "Mattermost close notification sent.")

    messages.success(request, f"Maintenance window #{pk} cancelled.")
    return redirect("maintenance:window-detail", pk=pk)
