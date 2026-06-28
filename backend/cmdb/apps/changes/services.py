"""
CAB service layer: impact engine, risk scoring, approval-chain generation, and
the status state machine (docs/cab-design.md §8–§9, §11).

Design invariants honoured here:
- Blast radius is a recursive CTE, never application-side traversal (CLAUDE.md).
- Impact + stakeholders are computed, never hand-entered (§2.1).
- Unknown resilience is treated as non-resilient — fail cautious (§2.3).
- The CMDB records; it does not execute (§13). Transitions only record state.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from cmdb import redis_client
from cmdb.apps.environments.models import Environment

from .models import (
    Change,
    ChangeAffectedEnvironment,
    ChangeApproval,
    ChangeNotification,
    ChangeOutcome,
    ChangeStatus,
    ChangeTarget,
    ChangeType,
    RiskTier,
    Temperature,
)

logger = logging.getLogger(__name__)

# Approval level -> the awaiting_* status that means "waiting for this level".
LEVEL_STATUS = {
    1: ChangeStatus.AWAITING_L1,
    2: ChangeStatus.AWAITING_L2,
    3: ChangeStatus.AWAITING_L3,
    4: ChangeStatus.AWAITING_L4,
}
AWAITING = set(LEVEL_STATUS.values())
COMPLETENESS_RUNBOOK = ('precheck_commands', 'execute_commands', 'verify_commands', 'rollback_commands')
NORMAL_LEAD_DAYS = 7  # a normal change must be requested at least this far ahead


# --------------------------------------------------------------------------- #
# Impact engine                                                               #
# --------------------------------------------------------------------------- #
def _direct_environments(change: Change) -> dict[str, Environment]:
    """``{name: Environment}`` directly hit by the CR's targets (§9.1)."""
    out: dict[str, Environment] = {}
    for t in change.targets.all():
        if t.target_type == ChangeTarget.TargetType.JUJU_MODEL and t.environment_id:
            out[t.environment.name] = t.environment
        elif t.target_type == ChangeTarget.TargetType.NODE and t.node_id:
            for e in Environment.objects.filter(
                Q(primary_node=t.node) | Q(secondary_node=t.node)
            ):
                out[e.name] = e
        elif t.target_type == ChangeTarget.TargetType.CLOUD and t.cloud:
            for e in Environment.objects.filter(cloud=t.cloud):
                out[e.name] = e
        # switch: the netbox switch graph (#39/#40) is empty, so no direct envs.
    return out


def _downstream(seed_names: list[str]) -> dict[str, int]:
    """``{env_name: min_depth}`` of all downstream dependents of the seeds.

    Recursive CTE over ``environment_dependencies`` — CLAUDE.md invariant: blast
    radius is a recursive CTE, not application-side traversal.
    """
    if not seed_names:
        return {}
    with connection.cursor() as cur:
        cur.execute(
            """
            WITH RECURSIVE blast_radius AS (
                SELECT ed.environment_name, ed.depends_on_name, 1 AS depth
                FROM environment_dependencies ed
                WHERE ed.depends_on_name = ANY(%s)
                UNION
                SELECT ed.environment_name, ed.depends_on_name, br.depth + 1
                FROM environment_dependencies ed
                INNER JOIN blast_radius br
                    ON ed.depends_on_name = br.environment_name
                WHERE br.depth < 10
            )
            SELECT environment_name, MIN(depth)
            FROM blast_radius
            GROUP BY environment_name
            """,
            [list(seed_names)],
        )
        return {name: depth for name, depth in cur.fetchall()}


def _affected_row(change, name, env, impact_type, depth, resilient_set):
    is_res = name in resilient_set
    basis = (
        "gitops + >3 VMs across >1 node (coarse signal)" if is_res
        else "not resilient by the coarse signal / unknown — fail cautious"
    )
    return ChangeAffectedEnvironment(
        change=change,
        environment=env,
        environment_name=name,
        impact_type=impact_type,
        dependency_depth=depth,
        resilient=is_res,
        resilience_basis=basis,
        consumer_team=(env.consumed_by or '') if env else '',
        cia_owner=(env.cia_owner or '') if env else '',
        cia_risk_owner=(env.cia_risk_owner or '') if env else '',
        criticality_tier=env.criticality_tier if env else None,
        env_type=(env.env_type or '') if env else '',
    )


@transaction.atomic
def compute_impact(change: Change) -> list[ChangeAffectedEnvironment]:
    """(Re)compute and snapshot the impact set. Direct beats dependency."""
    direct = _direct_environments(change)
    downstream = _downstream(list(direct.keys()))
    # resilient_env_names() returns an empty set if Redis is unreachable, which
    # makes everything non-resilient — the fail-cautious default we want (§2.3).
    resilient = redis_client.resilient_env_names()

    change.affected.all().delete()
    rows = [
        _affected_row(change, name, env, 'direct', 0, resilient)
        for name, env in direct.items()
    ]
    dep_envs = {
        e.name: e
        for e in Environment.objects.filter(name__in=list(downstream.keys()))
    }
    for name, depth in downstream.items():
        if name in direct:
            continue
        rows.append(
            _affected_row(change, name, dep_envs.get(name), 'dependency', depth, resilient)
        )
    ChangeAffectedEnvironment.objects.bulk_create(rows)
    return rows


# --------------------------------------------------------------------------- #
# Risk + region                                                               #
# --------------------------------------------------------------------------- #
def compute_risk(change: Change) -> tuple[int, str]:
    """Risk score + tier from the impact snapshot (§9.2). Tunable thresholds."""
    affected = list(change.affected.all())
    score = 0
    if any(a.env_type == 'prod' for a in affected):
        score += 3
    crits = [a.criticality_tier for a in affected if a.criticality_tier]
    if crits:
        score += {1: 4, 2: 2, 3: 1}.get(min(crits), 0)  # tier 1 = most critical
    non_resilient = sum(1 for a in affected if not a.resilient)
    score += min(non_resilient * 2, 6)
    score += max((a.dependency_depth for a in affected), default=0)
    n = len(affected)
    score += 0 if n <= 1 else (1 if n <= 5 else 2)
    if change.targets.filter(target_type__in=['cloud', 'switch']).exists():
        score += 3

    tier = (
        RiskTier.LOW if score < 4
        else RiskTier.MEDIUM if score <= 8
        else RiskTier.HIGH
    )
    change.risk_score = score
    change.risk_tier = tier
    return score, tier


def derive_region(change: Change) -> Optional[str]:
    """Single region from the targets' environments; cross-region is rejected."""
    regions = {e.region for e in _direct_environments(change).values() if e.region}
    if len(regions) > 1:
        raise ValidationError(
            f"Cross-region CR rejected (regions: {sorted(regions)}); split per region (§6.2)."
        )
    return next(iter(regions)) if regions else None


# --------------------------------------------------------------------------- #
# Approval chain                                                              #
# --------------------------------------------------------------------------- #
def generate_approval_chain(change: Change) -> list[ChangeApproval]:
    """Generate the ordered chain for the CR's current version (§9.3).

    standard  -> no chain (peer-ack; auto-approved when the template allows).
    emergency -> single L2 tech-lead (pre-exec).
    normal    -> L1 peer, L2 tech-lead, [L3 change-manager if high], L4 consumer
                 (one per impacted consuming team; always last).
    """
    change.approvals.filter(version=change.version).delete()
    rows: list[ChangeApproval] = []
    v = change.version

    def add(level, role, party):
        rows.append(ChangeApproval(change=change, version=v, level=level, role=role, party=party))

    if change.change_type == ChangeType.STANDARD:
        pass
    elif change.change_type == ChangeType.EMERGENCY:
        add(2, ChangeApproval.Role.TECH_LEAD, 'cab-tech-leads')
    else:  # normal
        add(1, ChangeApproval.Role.PEER, 'sre')
        add(2, ChangeApproval.Role.TECH_LEAD, 'cab-tech-leads')
        if change.risk_tier == RiskTier.HIGH:
            add(3, ChangeApproval.Role.CHANGE_MANAGER, f"sre-manager-{change.region or 'unknown'}")
        consumer_teams = sorted(
            {a.consumer_team for a in change.affected.all() if a.consumer_team}
        )
        for team in consumer_teams:
            add(4, ChangeApproval.Role.CONSUMER, team)

    ChangeApproval.objects.bulk_create(rows)
    return rows


def _violates_guardrails(change: Change) -> bool:
    """True if a standard CR breaches its template's guardrails (§4)."""
    tpl = change.template
    if tpl is None:
        return True  # a standard change must reference a template to be pre-approved
    affected = list(change.affected.all())
    if tpl.requires_all_resilient and any(not a.resilient for a in affected):
        return True
    if tpl.max_nodes is not None:
        if change.targets.filter(target_type='node').count() > tpl.max_nodes:
            return True
    if tpl.allowed_target_types:
        types = set(change.targets.values_list('target_type', flat=True))
        if not types.issubset(set(tpl.allowed_target_types)):
            return True
    if tpl.allowed_env_types:
        etypes = {a.env_type for a in affected if a.env_type}
        if not etypes.issubset(set(tpl.allowed_env_types)):
            return True
    if tpl.allowed_clouds:
        clouds = set(change.targets.filter(target_type='cloud').values_list('cloud', flat=True))
        if clouds and not clouds.issubset(set(tpl.allowed_clouds)):
            return True
    return False


def _status_after_chain(change: Change) -> str:
    """awaiting_* for the lowest still-pending level, else approved."""
    pending = (
        change.approvals.filter(version=change.version, decision=ChangeApproval.Decision.PENDING)
        .order_by('level')
        .first()
    )
    return LEVEL_STATUS[pending.level] if pending else ChangeStatus.APPROVED


def _next_reference() -> str:
    year = timezone.now().year
    prefix = f"CHG-{year}-"
    n = Change.objects.filter(reference__startswith=prefix).count() + 1
    return f"{prefix}{n:04d}"


CONFLICT_EXCLUDED_STATUSES = {
    ChangeStatus.DRAFT, ChangeStatus.APPLIED, ChangeStatus.CLOSED,
    ChangeStatus.ROLLED_BACK, ChangeStatus.CANCELLED, ChangeStatus.REJECTED,
    ChangeStatus.EXPIRED,
}


def _check_conflicts(change: Change) -> None:
    """Block a CR whose window overlaps another *live* CR on the same CI (§7).

    "Same configuration item" = a shared directly-targeted env / node / cloud /
    switch. "Overlapping" = the maintenance windows intersect in time. Drafts and
    finished/cancelled CRs are ignored.
    """
    mw = change.maintenance_window
    if not mw:
        return
    T = ChangeTarget.TargetType
    env_ids = set(change.targets.filter(target_type=T.JUJU_MODEL).values_list('environment_id', flat=True))
    node_ids = set(change.targets.filter(target_type=T.NODE).values_list('node_id', flat=True))
    clouds = set(change.targets.filter(target_type=T.CLOUD).values_list('cloud', flat=True))
    switches = set(change.targets.filter(target_type=T.SWITCH).values_list('switch_hostname', flat=True))

    others = (Change.objects.exclude(pk=change.pk)
              .exclude(status__in=CONFLICT_EXCLUDED_STATUSES)
              .filter(maintenance_window__isnull=False,
                      maintenance_window__starts_at__lt=mw.ends_at,
                      maintenance_window__ends_at__gt=mw.starts_at)
              .prefetch_related('targets'))
    hits = []
    for other in others:
        for t in other.targets.all():
            if ((t.target_type == T.JUJU_MODEL and t.environment_id in env_ids)
                    or (t.target_type == T.NODE and t.node_id in node_ids)
                    or (t.target_type == T.CLOUD and t.cloud in clouds)
                    or (t.target_type == T.SWITCH and t.switch_hostname in switches)):
                hits.append(f"{other.reference or other.pk} ({t.label})")
                break
    if hits:
        raise ValidationError(
            "Maintenance window conflicts with an existing change on the same configuration "
            f"item: {', '.join(hits)}. Pick a non-overlapping window."
        )


def _check_lead_time(change: Change) -> None:
    """Per-type scheduling lead time on the MW start (§7).

    emergency = any time (retroactive ok); standard = same-day ok but not past;
    normal = at least NORMAL_LEAD_DAYS ahead.
    """
    start = change.maintenance_window.starts_at
    now = timezone.now()
    if change.change_type == ChangeType.EMERGENCY:
        return
    if change.change_type == ChangeType.STANDARD:
        if start < now:
            raise ValidationError("A standard change must schedule a future maintenance window.")
        return
    if start < now + timedelta(days=NORMAL_LEAD_DAYS):
        raise ValidationError(
            f"A normal change must be requested at least {NORMAL_LEAD_DAYS} days ahead "
            "of the maintenance window."
        )


# --------------------------------------------------------------------------- #
# State machine                                                               #
# --------------------------------------------------------------------------- #
@transaction.atomic
def submit_change(change: Change) -> Change:
    """draft -> submitted -> awaiting_l1 (or approved). Completeness gate + impact."""
    if change.status != ChangeStatus.DRAFT:
        raise ValidationError(f"Only drafts can be submitted (status={change.status}).")

    missing = []
    if not change.maintenance_window_id:
        missing.append('maintenance_window')
    if not (change.executer or '').strip():
        missing.append('executer')
    missing += [f for f in COMPLETENESS_RUNBOOK if not (getattr(change, f) or '').strip()]
    if not change.targets.exists():
        missing.append('at least one target')
    if missing:
        raise ValidationError(f"Completeness gate failed — missing: {', '.join(missing)}.")

    if change.temperature == Temperature.HOT and (change.staging_notes or '').strip():
        raise ValidationError("Staging notes apply to outage (downtime) changes only.")

    change.region = derive_region(change)
    compute_impact(change)
    compute_risk(change)

    if change.change_type == ChangeType.STANDARD and _violates_guardrails(change):
        logger.info("CR %s auto-upgraded standard->normal (guardrails breached).",
                    change.reference or change.pk)
        change.change_type = ChangeType.NORMAL

    # Scheduling lead time (checked after any auto-upgrade so it uses the final type):
    # emergency = any time (retroactive ok); standard = same-day ok but not past;
    # normal = at least a week ahead.
    _check_lead_time(change)
    _check_conflicts(change)

    generate_approval_chain(change)

    if not change.reference:
        change.reference = _next_reference()
    change.submitted_at = timezone.now()

    if (change.change_type == ChangeType.STANDARD and change.template
            and change.template.auto_approve):
        change.status = ChangeStatus.APPROVED
        change.approved_at = timezone.now()
    else:
        change.status = _status_after_chain(change)
        if change.status == ChangeStatus.APPROVED:
            change.approved_at = timezone.now()

    change.save()
    return change


@transaction.atomic
def record_decision(
    change: Change,
    level: int,
    decision: str,
    by: str = '',
    comment: str = '',
    party: Optional[str] = None,
    proposed_alternative_date: Optional[datetime] = None,
) -> Change:
    """Record an approval decision; advance the chain sequentially (consumer last)."""
    if change.status not in AWAITING:
        raise ValidationError(f"CR {change.reference} is not in an approval state (status={change.status}).")
    if change.approvals.filter(
        version=change.version, decision=ChangeApproval.Decision.PENDING, level__lt=level
    ).exists():
        raise ValidationError(f"Cannot decide L{level}: a lower level is still pending (sequential, §9.3).")

    rows = change.approvals.filter(
        version=change.version, level=level, decision=ChangeApproval.Decision.PENDING
    )
    if party is not None:
        rows = rows.filter(party=party)
    if not rows.exists():
        raise ValidationError(f"No pending L{level} approval for version {change.version}.")

    now = timezone.now()
    if decision == ChangeApproval.Decision.APPROVED:
        rows.update(decision=decision, decided_by=by, decided_at=now, comment=comment)
        change.status = _status_after_chain(change)
        if change.status == ChangeStatus.APPROVED:
            change.approved_at = now
    elif decision == ChangeApproval.Decision.REJECTED:
        rows.update(decision=decision, decided_by=by, decided_at=now, comment=comment)
        change.status = ChangeStatus.REJECTED
    elif decision == ChangeApproval.Decision.BLOCKED:
        if proposed_alternative_date is None:
            raise ValidationError("A consumer date-block must propose an alternative date (§11.1).")
        rows.update(decision=decision, decided_by=by, decided_at=now, comment=comment,
                    proposed_alternative_date=proposed_alternative_date)
        change.status = ChangeStatus.BLOCKED
    else:
        raise ValidationError(f"Unknown decision: {decision!r}.")

    change.save()
    return change


@transaction.atomic
def revise_change(change: Change, **fields) -> Change:
    """Any post-submit edit bumps version, invalidates approvals, resets chain (§8.3)."""
    if change.status in (ChangeStatus.CLOSED, ChangeStatus.CANCELLED):
        raise ValidationError(f"Cannot revise a {change.status} CR.")
    change.version += 1
    for key, value in fields.items():
        setattr(change, key, value)
    change.region = derive_region(change)
    compute_impact(change)
    compute_risk(change)
    _check_conflicts(change)
    generate_approval_chain(change)  # rows are tagged with the new version
    change.status = _status_after_chain(change)
    change.approved_at = None
    change.save()
    return change


def _set_status(change, new_status, **extra):
    change.status = new_status
    for k, v in extra.items():
        setattr(change, k, v)
    change.save()
    return change


@transaction.atomic
def schedule_change(change: Change, notify: bool = True) -> Change:
    """approved -> scheduled. Creates the calendar event + stakeholder notices (§11)."""
    if change.status != ChangeStatus.APPROVED:
        raise ValidationError(f"Only approved CRs can be scheduled (status={change.status}).")
    _set_status(change, ChangeStatus.SCHEDULED, scheduled_at=timezone.now())
    if notify:
        create_calendar_invite(change)
        notify_stakeholders(change)
    return change


def start_change(change: Change) -> Change:
    if change.status != ChangeStatus.SCHEDULED:
        raise ValidationError(f"Only scheduled CRs can start (status={change.status}).")
    return _set_status(change, ChangeStatus.IN_PROGRESS, started_at=timezone.now())


def begin_verify(change: Change) -> Change:
    if change.status != ChangeStatus.IN_PROGRESS:
        raise ValidationError(f"Only in-progress CRs can verify (status={change.status}).")
    return _set_status(change, ChangeStatus.VERIFYING)


def complete_change(change: Change, passed: bool) -> Change:
    """verify pass -> applied (success); verify fail -> rolled_back (DORA change-failure)."""
    if change.status not in (ChangeStatus.IN_PROGRESS, ChangeStatus.VERIFYING):
        raise ValidationError(f"CR must be in progress / verifying to complete (status={change.status}).")
    if passed:
        return _set_status(change, ChangeStatus.APPLIED,
                           outcome=ChangeOutcome.SUCCESS, completed_at=timezone.now())
    return _set_status(change, ChangeStatus.ROLLED_BACK,
                       outcome=ChangeOutcome.ROLLED_BACK, completed_at=timezone.now())


@transaction.atomic
def close_change(change: Change, pir_notes: str = '') -> Change:
    """applied / rolled_back -> closed. PIR is mandatory for emergency / rolled-back / failed."""
    if change.status not in (ChangeStatus.APPLIED, ChangeStatus.ROLLED_BACK):
        raise ValidationError(f"Only applied / rolled-back CRs can be closed (status={change.status}).")
    if change.requires_pir and not (pir_notes or change.pir_notes).strip():
        raise ValidationError("PIR notes are mandatory for emergency / rolled-back / failed changes (§14).")
    if pir_notes:
        change.pir_notes = pir_notes
    return _set_status(change, ChangeStatus.CLOSED)


def cancel_change(change: Change) -> Change:
    """Proposer withdraws — distinct from rejected (an approver declines)."""
    allowed = {ChangeStatus.DRAFT, ChangeStatus.SUBMITTED} | AWAITING
    if change.status not in allowed:
        raise ValidationError(f"Cannot cancel a {change.status} CR.")
    return _set_status(change, ChangeStatus.CANCELLED)


def expire_change(change: Change) -> Change:
    if change.status != ChangeStatus.SCHEDULED:
        raise ValidationError(f"Only scheduled CRs can expire (status={change.status}).")
    return _set_status(change, ChangeStatus.EXPIRED)


# --------------------------------------------------------------------------- #
# Notifications (recorded, not really sent here — §11)                         #
# --------------------------------------------------------------------------- #
def create_calendar_invite(change: Change) -> ChangeNotification:
    """Record a Google Calendar event for the MW (§11.2). Real send is future work."""
    change.gcal_event_id = change.gcal_event_id or f"cab-{change.reference}"
    change.save(update_fields=['gcal_event_id', 'updated_at'])
    return ChangeNotification.objects.create(
        change=change,
        channel=ChangeNotification.Channel.GCAL,
        recipient=change.executer,
        variant=ChangeNotification.Variant.CALENDAR_INVITE,
        body=f"{change.reference}: {change.title} — MW scheduled.",
        sent_at=timezone.now(),
        success=True,
    )


def notify_stakeholders(change: Change) -> list[ChangeNotification]:
    """One notice per affected consuming team, forked on resilience (§11.1)."""
    notes: list[ChangeNotification] = []
    now = timezone.now()
    seen: set[str] = set()
    for a in change.affected.all():
        team = a.consumer_team
        if not team or team in seen:
            continue
        seen.add(team)
        if a.resilient:
            variant = ChangeNotification.Variant.RESILIENT_BLIP
            body = (f"Brief blip during {change.reference}; auto-recovers. "
                    f"Basis: {a.resilience_basis}.")
        else:
            variant = ChangeNotification.Variant.NON_RESILIENT_ENGINEER
            body = (f"{a.environment_name} will go down during {change.reference} and "
                    f"won't auto-recover — have an engineer ready.")
        notes.append(ChangeNotification(
            change=change, channel=ChangeNotification.Channel.MATTERMOST,
            recipient=team, variant=variant, body=body, sent_at=now, success=True,
        ))
    ChangeNotification.objects.bulk_create(notes)
    change.affected.filter(consumer_team__in=list(seen)).update(notified_at=now)

    # When requested, also notify the impacted CIA owners (asset + risk owner).
    if change.notify_on_approval:
        emails = sorted({e for a in change.affected.all()
                         for e in (a.cia_owner, a.cia_risk_owner) if e})
        cia_notes = [ChangeNotification(
            change=change, channel=ChangeNotification.Channel.EMAIL, recipient=email,
            variant=ChangeNotification.Variant.INFO,
            body=f"You are a CIA owner of a service impacted by {change.reference}.",
            sent_at=now, success=True,
        ) for email in emails]
        ChangeNotification.objects.bulk_create(cia_notes)
        notes += cia_notes

    return notes
