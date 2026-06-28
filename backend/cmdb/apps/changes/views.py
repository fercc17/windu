"""
Read-only CAB views: list change requests and show one CR's full record.

The CMDB is read-only for its audience (CLAUDE.md); the CR lifecycle is driven
through the service layer / management commands, not the UI.
"""
from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import transaction
from django.views.decorators.http import require_POST
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from cmdb.apps.environments.models import Environment
from cmdb.apps.maintenance.models import MaintenanceWindow
from cmdb.apps.netbox.models import Node, NodeSwitchConnection

from . import services
from .models import (
    Change,
    ChangeAffectedEnvironment,
    ChangeApproval,
    ChangeStatus,
    ChangeTarget,
    ChangeTemplate,
    ChangeType,
    RiskTier,
    StandardMaintenanceWindow,
    Temperature,
)

TERMINAL_STATUSES = {
    ChangeStatus.APPLIED, ChangeStatus.CLOSED, ChangeStatus.ROLLED_BACK,
    ChangeStatus.CANCELLED, ChangeStatus.REJECTED, ChangeStatus.EXPIRED,
}

STATUS_BADGE = {
    ChangeStatus.DRAFT: "secondary",
    ChangeStatus.SUBMITTED: "info",
    ChangeStatus.AWAITING_L1: "warning",
    ChangeStatus.AWAITING_L2: "warning",
    ChangeStatus.AWAITING_L3: "warning",
    ChangeStatus.AWAITING_L4: "warning",
    ChangeStatus.APPROVED: "primary",
    ChangeStatus.SCHEDULED: "info",
    ChangeStatus.IN_PROGRESS: "primary",
    ChangeStatus.VERIFYING: "info",
    ChangeStatus.APPLIED: "success",
    ChangeStatus.CLOSED: "dark",
    ChangeStatus.REJECTED: "danger",
    ChangeStatus.CANCELLED: "secondary",
    ChangeStatus.BLOCKED: "danger",
    ChangeStatus.EXPIRED: "secondary",
    ChangeStatus.ROLLED_BACK: "danger",
}
RISK_BADGE = {RiskTier.LOW: "success", RiskTier.MEDIUM: "warning", RiskTier.HIGH: "danger"}
TYPE_BADGE = {ChangeType.STANDARD: "secondary", ChangeType.NORMAL: "primary",
              ChangeType.EMERGENCY: "danger"}
DECISION_BADGE = {"pending": "warning", "approved": "success", "rejected": "danger",
                  "blocked": "danger", "acknowledged": "info"}


def _decorate(c: Change) -> Change:
    c.status_badge = STATUS_BADGE.get(c.status, "secondary")
    c.risk_badge = RISK_BADGE.get(c.risk_tier, "light")
    c.type_badge = TYPE_BADGE.get(c.change_type, "secondary")
    return c


def _people_roster():
    """People relevant to existing changes — used by the 'I am' picker."""
    roster = set()
    for proposer, executer, peer in Change.objects.values_list("proposer", "executer", "peer_reviewer"):
        roster |= {proposer, executer, peer}
    roster |= set(ChangeApproval.objects.values_list("party", flat=True))
    roster |= set(ChangeAffectedEnvironment.objects.values_list("cia_owner", flat=True))
    roster |= set(ChangeAffectedEnvironment.objects.values_list("cia_risk_owner", flat=True))
    return sorted(x for x in roster if x)


def changes_list(request):
    # "I am" viewer identity (no auth yet): ?me= sets it, persisted in the session.
    if "me" in request.GET:
        request.session["cab_me"] = request.GET.get("me") or ""
    me = request.session.get("cab_me", "")

    qs = Change.objects.all().select_related("maintenance_window").prefetch_related("targets", "affected")
    f_status = request.GET.get("status") or ""
    f_type = request.GET.get("type") or ""
    f_impact = request.GET.get("impact") or ""
    f_region = request.GET.get("region") or ""
    if f_status:
        qs = qs.filter(status=f_status)
    if f_type:
        qs = qs.filter(change_type=f_type)
    if f_impact:
        qs = qs.filter(temperature=f_impact)
    if f_region:
        qs = qs.filter(region=f_region)

    involved_ids = set()
    if me:
        involved_ids = set(Change.objects.filter(
            Q(proposer=me) | Q(executer=me) | Q(peer_reviewer=me)
            | Q(approvals__party=me)
            | Q(affected__cia_owner=me) | Q(affected__cia_risk_owner=me)
            | Q(affected__consumer_team=me)
        ).values_list("id", flat=True))

    mine, upcoming, history = [], [], []
    for c in qs:
        _decorate(c)
        if c.status in TERMINAL_STATUSES:
            history.append(c)
        elif me and c.id in involved_ids:
            mine.append(c)
        else:
            upcoming.append(c)

    # Upcoming sorted by window start, soonest first (windowless last).
    upcoming.sort(key=lambda c: (
        c.maintenance_window.starts_at if c.maintenance_window_id else None) is None)
    upcoming.sort(key=lambda c: c.maintenance_window.starts_at
                  if c.maintenance_window_id else timezone.now() + timedelta(days=3650))

    context = {
        "mine": mine, "upcoming": upcoming, "history": history,
        "total": len(mine) + len(upcoming) + len(history),
        "grand_total": Change.objects.count(),
        "me": me, "roster": _people_roster(),
        "status_choices": ChangeStatus.choices,
        "type_choices": ChangeType.choices,
        "temperature_choices": Temperature.choices,
        "regions": sorted({r for r in Change.objects.values_list("region", flat=True) if r}),
        "f_status": f_status,
        "f_type": f_type,
        "f_impact": f_impact,
        "f_region": f_region,
    }
    return render(request, "changes/list.html", context)


def ci_search(request):
    """Typeahead for configuration items, filtered by target type (max 20)."""
    target_type = request.GET.get("type", "")
    q = (request.GET.get("q") or "").strip()
    results: list[str] = []
    if target_type == ChangeTarget.TargetType.JUJU_MODEL:
        qs = Environment.objects.all()
        if q:
            qs = qs.filter(name__icontains=q)
        results = list(qs.order_by("name").values_list("name", flat=True)[:20])
    elif target_type == ChangeTarget.TargetType.NODE:
        qs = Node.objects.all()
        if q:
            qs = qs.filter(hostname__icontains=q)
        results = list(qs.order_by("hostname").values_list("hostname", flat=True)[:20])
    elif target_type == ChangeTarget.TargetType.CLOUD:
        clouds = (
            Environment.objects.exclude(cloud__isnull=True).exclude(cloud="")
            .values_list("cloud", flat=True).distinct()
        )
        results = sorted({c for c in clouds if not q or q.lower() in c.lower()})[:20]
    elif target_type == ChangeTarget.TargetType.SWITCH:
        switches = NodeSwitchConnection.objects.values_list("switch_hostname", flat=True).distinct()
        results = sorted({s for s in switches if not q or q.lower() in s.lower()})[:20]
    return JsonResponse({"results": results})


def people_search(request):
    """Typeahead for people (proposer/executer), from CIA owners; IS team first."""
    q = (request.GET.get("q") or "").strip()
    is_team = set(Environment.objects.filter(team="is").values_list("cia_owner", flat=True))
    is_team |= set(Environment.objects.filter(team="is").values_list("cia_risk_owner", flat=True))
    is_team = {e for e in is_team if e}
    owner = Environment.objects.exclude(cia_owner__isnull=True).exclude(cia_owner="")
    risk = Environment.objects.exclude(cia_risk_owner__isnull=True).exclude(cia_risk_owner="")
    if q:
        owner = owner.filter(cia_owner__icontains=q)
        risk = risk.filter(cia_risk_owner__icontains=q)
    people = set(owner.values_list("cia_owner", flat=True)) | set(risk.values_list("cia_risk_owner", flat=True))
    people = {e for e in people if e}
    ranked = sorted(people, key=lambda e: (e not in is_team, e))  # IS team first
    return JsonResponse({"results": ranked[:20]})


def ci_info(request):
    """CIA people impacted by a configuration item (juju model)."""
    ttype = request.GET.get("type", "")
    ref = (request.GET.get("ref") or "").strip()
    data = {"cia_owner": "", "cia_risk_owner": "", "cia_custodian": "", "consumer_team": ""}
    if ttype == ChangeTarget.TargetType.JUJU_MODEL and ref:
        e = Environment.objects.filter(name=ref).first()
        if e:
            data = {
                "cia_owner": e.cia_owner or "",
                "cia_risk_owner": e.cia_risk_owner or "",
                "cia_custodian": e.cia_custodian or "",
                "consumer_team": e.consumed_by or "",
            }
    return JsonResponse(data)


def _aware(raw: str):
    """Parse a datetime-local string into an aware datetime, or None."""
    if not raw:
        return None
    dt = parse_datetime(raw)
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def change_create(request):
    """Raise a new CR. POST creates the draft (+ target, +MW) and optionally submits.

    Unlike the rest of the read-only CMDB, the CAB is a system of record: proposers
    raise change requests here. Identity (Canonical IDP) is future work, so the
    proposer/executer are free-text for now.
    """
    if request.method != "POST":
        return render(request, "changes/form.html", {
            "type_choices": ChangeType.choices,
            "temperature_choices": Temperature.choices,
            "target_types": ChangeTarget.TargetType.choices,
            "templates": ChangeTemplate.objects.all(),
            "form": {},
        })

    p = request.POST
    action = p.get("action", "draft")
    errors: list[str] = []

    target_type = p.get("target_type") or ""
    target_ref = (p.get("target_ref") or "").strip()
    env = node = None
    if target_type == ChangeTarget.TargetType.JUJU_MODEL:
        env = Environment.objects.filter(name=target_ref).first()
        if target_ref and not env:
            errors.append(f"No environment named '{target_ref}'.")
    elif target_type == ChangeTarget.TargetType.NODE:
        node = Node.objects.filter(hostname=target_ref).first()
        if target_ref and not node:
            errors.append(f"No node with hostname '{target_ref}'.")

    if not (p.get("title") or "").strip():
        errors.append("Title is required.")
    if not target_type or not target_ref:
        errors.append("A target type and identifier are required.")

    mw_start, mw_end = _aware(p.get("mw_start")), _aware(p.get("mw_end"))
    if (mw_start and not mw_end) or (mw_end and not mw_start):
        errors.append("A maintenance window needs both a start and an end.")
    if mw_start and mw_end and mw_end < mw_start + timedelta(hours=1):
        errors.append("The maintenance window must end at least 1 hour after it starts.")
    if (mw_start and mw_start < timezone.now()
            and (p.get("change_type") or ChangeType.NORMAL) != ChangeType.EMERGENCY):
        errors.append("Only emergency changes may use a maintenance window in the past.")

    change_type = p.get("change_type") or ChangeType.NORMAL
    temperature = p.get("temperature") or Temperature.COLD
    staging_notes = (p.get("staging_notes") or "").strip()
    if staging_notes and temperature == Temperature.HOT:
        errors.append("Staging notes apply to outage (downtime) changes only.")
    notify_on_approval = bool(p.get("notify_on_approval"))

    # "Create & submit" must satisfy the completeness gate up front, so the user
    # gets the full list of what's missing rather than a silent no-op.
    if action == "submit":
        if not (p.get("executer") or "").strip():
            errors.append("Executer is required to submit.")
        for fld, lbl in (("precheck_commands", "Precheck"), ("execute_commands", "Execute"),
                         ("verify_commands", "Verify"), ("rollback_commands", "Rollback")):
            if not (p.get(fld) or "").strip():
                errors.append(f"{lbl} command is required to submit.")
        if not (mw_start and mw_end):
            errors.append("A maintenance window (start and end) is required to submit.")
        if (change_type == ChangeType.NORMAL and mw_start
                and mw_start < timezone.now() + timedelta(days=services.NORMAL_LEAD_DAYS)):
            errors.append(
                f"A normal change must be requested at least {services.NORMAL_LEAD_DAYS} days ahead.")

    if errors:
        for e in errors:
            messages.error(request, e)
        return render(request, "changes/form.html", {
            "type_choices": ChangeType.choices,
            "temperature_choices": Temperature.choices,
            "target_types": ChangeTarget.TargetType.choices,
            "templates": ChangeTemplate.objects.all(),
            "form": p,
        })

    try:
        with transaction.atomic():
            mw = None
            if mw_start and mw_end:
                scope = {}
                if env:
                    scope["environment"] = env
                elif node:
                    scope["node"] = node
                elif target_type == ChangeTarget.TargetType.CLOUD:
                    scope["cloud"] = target_ref
                mw = MaintenanceWindow.objects.create(
                    starts_at=mw_start, ends_at=mw_end, created_by="cab-ui",
                    reason=f"CAB: {p.get('title')}", **scope,
                )

            tpl = None
            if p.get("template"):
                tpl = ChangeTemplate.objects.filter(pk=p["template"]).first()

            change = Change.objects.create(
                title=p.get("title").strip(),
                description=p.get("description", "").strip(),
                change_type=change_type,
                temperature=temperature,
                proposer=p.get("proposer", "").strip(),
                executer=p.get("executer", "").strip(),
                template=tpl,
                maintenance_window=mw,
                precheck_commands=p.get("precheck_commands", "").strip(),
                execute_commands=p.get("execute_commands", "").strip(),
                verify_commands=p.get("verify_commands", "").strip(),
                rollback_commands=p.get("rollback_commands", "").strip(),
                staging_notes=staging_notes,
                notify_on_approval=notify_on_approval,
            )
            ChangeTarget.objects.create(
                change=change, target_type=target_type, environment=env, node=node,
                cloud=target_ref if target_type == ChangeTarget.TargetType.CLOUD else "",
                switch_hostname=target_ref if target_type == ChangeTarget.TargetType.SWITCH else "",
            )
            if action == "submit":
                services.submit_change(change)
    except ValidationError as exc:
        for e in (exc.messages if hasattr(exc, "messages") else [str(exc)]):
            messages.error(request, e)
        return render(request, "changes/form.html", {
            "type_choices": ChangeType.choices,
            "temperature_choices": Temperature.choices,
            "target_types": ChangeTarget.TargetType.choices,
            "templates": ChangeTemplate.objects.all(),
            "form": p,
        })

    messages.success(request, f"{change.reference or 'Draft'} created ({change.get_status_display()}).")
    return redirect("changes:detail", reference=change.reference or change.pk)


# TEMP: dev convenience to load placement data from the juju fixtures via the UI.
# Remove this view, its URL, and the nav button when no longer needed.
@require_POST
def load_demo(request):
    """TEMPORARY — load ps5/ps6/ps7 placement from tests/fixtures/juju (runs seed_placement)."""
    written = call_command("seed_placement")
    messages.success(
        request,
        f"Loaded ps5/ps6/ps7 placement data from fixtures — {written} environments updated.")
    return redirect("environment-list")


def standard_windows(request):
    """Read-only view of the seeded regional standard maintenance windows (§7)."""
    windows = list(StandardMaintenanceWindow.objects.all())
    for w in windows:
        w.next_start = w.next_occurrence()
        w.next_finish = w.next_start + w.duration
    return render(request, "changes/standard_windows.html", {"windows": windows})


def change_detail(request, reference):
    qs = Change.objects.prefetch_related("targets", "affected", "notifications")
    c = qs.filter(reference=reference).first()
    if c is None:  # drafts have no reference yet -> fall back to the UUID pk
        try:
            c = qs.get(pk=reference)
        except (Change.DoesNotExist, ValidationError, ValueError):
            raise Http404("No change request found.")
    _decorate(c)
    approvals = list(c.approvals.filter(version=c.version).order_by("level", "party"))
    for a in approvals:
        a.badge = DECISION_BADGE.get(a.decision, "secondary")
    context = {
        "c": c,
        "targets": c.targets.all(),
        "affected": c.affected.all(),
        "approvals": approvals,
        "superseded": c.approvals.exclude(version=c.version).order_by("version", "level"),
        "notifications": c.notifications.all(),
    }
    return render(request, "changes/detail.html", context)
