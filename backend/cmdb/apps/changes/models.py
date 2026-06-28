"""
Change management (CAB) models for IS-CMDB.

A ``Change`` (CR) is a thin record over a ``maintenance.MaintenanceWindow`` plus a
*computed* impact set. The CMDB **records and coordinates** changes; it never
executes them (docs/cab-design.md §13). The status enum and lifecycle are
docs/cab-design.md §8; roles/levels are §5/§9.

Identity is plain slugs for now — the Canonical IDP integration (§10) is future
work, so ``proposer`` / ``executer`` / approval ``party`` are CharFields, not FKs
to an auth model.
"""
import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone


class ChangeType(models.TextChoices):
    STANDARD = 'standard', 'Standard'
    NORMAL = 'normal', 'Normal'
    EMERGENCY = 'emergency', 'Emergency'


class ChangeStatus(models.TextChoices):
    """Canonical CR status enum — docs/cab-design.md §8.1.

    In-review statuses are named ``awaiting_l<n>`` ("waiting *for* level n to
    sign"), never "pending Lx", which is ambiguous. A single terminal
    ``approved`` is reached once the whole chain clears.
    """
    DRAFT = 'draft', 'Draft'
    SUBMITTED = 'submitted', 'Submitted'
    AWAITING_L1 = 'awaiting_l1', 'Awaiting L1 — Peer'
    AWAITING_L2 = 'awaiting_l2', 'Awaiting L2 — Tech Lead'
    AWAITING_L3 = 'awaiting_l3', 'Awaiting L3 — Change Manager'
    AWAITING_L4 = 'awaiting_l4', 'Awaiting L4 — Consumer'
    APPROVED = 'approved', 'Approved'
    SCHEDULED = 'scheduled', 'Scheduled'
    IN_PROGRESS = 'in_progress', 'In Progress'
    VERIFYING = 'verifying', 'Verifying'
    APPLIED = 'applied', 'Applied'
    CLOSED = 'closed', 'Closed'
    REJECTED = 'rejected', 'Rejected'
    CANCELLED = 'cancelled', 'Cancelled'
    BLOCKED = 'blocked', 'Blocked'
    EXPIRED = 'expired', 'Expired'
    ROLLED_BACK = 'rolled_back', 'Rolled Back'


class ChangeOutcome(models.TextChoices):
    NA = 'n/a', 'N/A'
    SUCCESS = 'success', 'Success'
    ROLLED_BACK = 'rolled_back', 'Rolled Back'
    FAILED = 'failed', 'Failed'
    PARTIAL = 'partial', 'Partial'


class RiskTier(models.TextChoices):
    LOW = 'low', 'Low'
    MEDIUM = 'medium', 'Medium'
    HIGH = 'high', 'High'


class Temperature(models.TextChoices):
    """Service impact — does the change cause downtime?

    Stored as cold/hot for brevity, surfaced as "Service impact" in the UI:
    COLD = the service is taken offline ("Outage (downtime)"); HOT = applied live
    ("Live (no downtime)"). Only outage (cold) changes can be staged ahead of time.
    """
    COLD = 'cold', 'Outage (downtime)'
    HOT = 'hot', 'Live (no downtime)'


class Change(models.Model):
    """A Change Request (CR). Lifecycle in docs/cab-design.md §8."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(
        max_length=32, unique=True, blank=True, null=True, db_index=True,
        help_text="CHG-YYYY-NNNN; assigned on submit.",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    change_type = models.CharField(
        max_length=20, choices=ChangeType.choices, default=ChangeType.NORMAL,
        db_index=True,
    )
    temperature = models.CharField(
        max_length=10, choices=Temperature.choices, default=Temperature.COLD,
        db_index=True,
        help_text="Cold = offline/restarted for the change; hot = applied live. "
                  "Independent of change_type; staging applies to cold only.",
    )
    status = models.CharField(
        max_length=20, choices=ChangeStatus.choices, default=ChangeStatus.DRAFT,
        db_index=True,
    )
    region = models.CharField(
        max_length=20, blank=True, null=True, db_index=True,
        help_text="amer | emea | apac — derived from targets.",
    )
    version = models.PositiveIntegerField(
        default=1,
        help_text="Bumps on any post-submit edit; resets the approval chain (§8.3).",
    )

    template = models.ForeignKey(
        'ChangeTemplate', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='changes',
    )
    maintenance_window = models.ForeignKey(
        'maintenance.MaintenanceWindow', on_delete=models.SET_NULL, null=True,
        blank=True, related_name='changes',
        help_text="Required to submit (completeness gate, §2.7).",
    )

    # People — plain slugs; Canonical IDP identity is future work (§10).
    proposer = models.CharField(max_length=255, blank=True)
    executer = models.CharField(
        max_length=255, blank=True,
        help_text="Regional SRE; required to submit.",
    )
    peer_reviewer = models.CharField(max_length=255, blank=True)

    # Runbook — stored, never executed (§13).
    precheck_commands = models.TextField(blank=True)
    execute_commands = models.TextField(blank=True, help_text="Required to submit.")
    verify_commands = models.TextField(blank=True, help_text="Required to submit.")
    rollback_commands = models.TextField(blank=True, help_text="Required to submit.")
    staging_notes = models.TextField(
        blank=True,
        help_text="Optional. Configuration that can be staged ahead of time without "
                  "applying the change. Cold changes only.",
    )
    notify_on_approval = models.BooleanField(
        default=False,
        help_text="Notify the impacted CIA owners (asset + risk owner) when the CR is approved.",
    )

    # Risk — computed (§9.2).
    risk_score = models.IntegerField(default=0)
    risk_tier = models.CharField(max_length=10, choices=RiskTier.choices, blank=True)

    # Timing.
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(blank=True, null=True)
    approved_at = models.DateTimeField(blank=True, null=True)
    scheduled_at = models.DateTimeField(blank=True, null=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    estimated_duration = models.DurationField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Outcome / PIR.
    outcome = models.CharField(
        max_length=12, choices=ChangeOutcome.choices, default=ChangeOutcome.NA,
    )
    pir_notes = models.TextField(blank=True)

    # Calendar + freeze.
    gcal_event_id = models.CharField(max_length=255, blank=True)
    freeze_override = models.BooleanField(default=False)
    freeze_override_justification = models.TextField(blank=True)

    class Meta:
        db_table = 'changes'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'region']),
            models.Index(fields=['change_type', 'risk_tier']),
        ]

    def __str__(self) -> str:
        return f"{self.reference or '(draft)'}: {self.title}"

    @property
    def requires_pir(self) -> bool:
        """PIR is mandatory for emergency / rolled-back / failed (§14)."""
        return (
            self.change_type == ChangeType.EMERGENCY
            or self.outcome in (ChangeOutcome.ROLLED_BACK, ChangeOutcome.FAILED)
        )


class ChangeTarget(models.Model):
    """One target of a CR. A CR has 1..N targets (§6.2)."""

    class TargetType(models.TextChoices):
        JUJU_MODEL = 'juju_model', 'Juju model'
        NODE = 'node', 'Node'
        SWITCH = 'switch', 'Switch'
        CLOUD = 'cloud', 'Cloud'

    change = models.ForeignKey(Change, on_delete=models.CASCADE, related_name='targets')
    target_type = models.CharField(max_length=20, choices=TargetType.choices)
    environment = models.ForeignKey(
        'environments.Environment', on_delete=models.CASCADE, null=True, blank=True,
        related_name='change_targets',
    )
    node = models.ForeignKey(
        'netbox.Node', on_delete=models.CASCADE, null=True, blank=True,
        related_name='change_targets',
    )
    switch_hostname = models.CharField(max_length=255, blank=True)
    cloud = models.CharField(max_length=50, blank=True)

    class Meta:
        db_table = 'change_targets'

    def __str__(self) -> str:
        return f"{self.target_type}:{self.label}"

    @property
    def label(self) -> str:
        if self.target_type == self.TargetType.JUJU_MODEL and self.environment_id:
            return self.environment.name
        if self.target_type == self.TargetType.NODE and self.node_id:
            return self.node.hostname
        if self.target_type == self.TargetType.SWITCH:
            return self.switch_hostname or '—'
        if self.target_type == self.TargetType.CLOUD:
            return self.cloud or '—'
        return '—'


class ChangeAffectedEnvironment(models.Model):
    """Computed impact set — a snapshot taken on submit (§6.3, §9.1)."""

    class ImpactType(models.TextChoices):
        DIRECT = 'direct', 'Direct'
        DEPENDENCY = 'dependency', 'Dependency'

    change = models.ForeignKey(Change, on_delete=models.CASCADE, related_name='affected')
    environment = models.ForeignKey(
        'environments.Environment', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='affected_by_changes',
    )
    environment_name = models.CharField(max_length=255, db_index=True)
    impact_type = models.CharField(max_length=12, choices=ImpactType.choices)
    dependency_depth = models.PositiveIntegerField(default=0)

    # Resilience snapshot. Coarse boolean today; the tiered, per-fault-domain
    # model (§9.4) is future work. Unknown => non-resilient (fail cautious, §2.3).
    resilient = models.BooleanField(default=False)
    resilience_basis = models.CharField(max_length=255, blank=True)

    # Snapshots used by risk (§9.2) and comms (§11.1).
    consumer_team = models.CharField(max_length=255, blank=True)
    cia_owner = models.CharField(max_length=255, blank=True)
    cia_risk_owner = models.CharField(max_length=255, blank=True)
    criticality_tier = models.IntegerField(blank=True, null=True)
    env_type = models.CharField(max_length=20, blank=True)

    notified_at = models.DateTimeField(blank=True, null=True)
    acknowledged_at = models.DateTimeField(blank=True, null=True)
    ack_by = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'change_affected_environments'
        unique_together = ('change', 'environment_name')
        ordering = ['dependency_depth', 'environment_name']

    def __str__(self) -> str:
        return f"{self.environment_name} ({self.impact_type})"


class ChangeApproval(models.Model):
    """One required approval, ordered by level (§6.4, §9.3)."""

    class Role(models.TextChoices):
        PEER = 'peer', 'Peer'
        TECH_LEAD = 'tech_lead', 'Tech Lead'
        CHANGE_MANAGER = 'change_manager', 'Change Manager'
        CONSUMER = 'consumer', 'Consumer'

    class Decision(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'
        BLOCKED = 'blocked', 'Blocked (date)'
        ACKNOWLEDGED = 'acknowledged', 'Acknowledged'

    change = models.ForeignKey(Change, on_delete=models.CASCADE, related_name='approvals')
    version = models.PositiveIntegerField(
        default=1, help_text="The CR version this approval is for (§8.3).",
    )
    level = models.PositiveSmallIntegerField(
        help_text="L1=peer, L2=tech_lead, L3=change_manager (high-risk only), "
                  "L4=consumer (always last).",
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    party = models.CharField(
        max_length=255, blank=True, help_text="IDP group / consuming-team slug.",
    )
    decision = models.CharField(
        max_length=12, choices=Decision.choices, default=Decision.PENDING,
    )
    decided_by = models.CharField(max_length=255, blank=True)
    decided_at = models.DateTimeField(blank=True, null=True)
    comment = models.TextField(blank=True)
    proposed_alternative_date = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'change_approvals'
        ordering = ['version', 'level', 'party']

    def __str__(self) -> str:
        return f"L{self.level} {self.role} [{self.decision}]"


class ChangeNotification(models.Model):
    """A recorded stakeholder notification / calendar event (§6.5, §11)."""

    class Channel(models.TextChoices):
        PAGERDUTY = 'pagerduty', 'PagerDuty'
        MATTERMOST = 'mattermost', 'Mattermost'
        EMAIL = 'email', 'Email'
        COS = 'cos', 'COS / Alertmanager'
        GCAL = 'gcal', 'Google Calendar'

    class Variant(models.TextChoices):
        RESILIENT_BLIP = 'resilient_blip', 'Resilient blip'
        NON_RESILIENT_ENGINEER = 'non_resilient_engineer', 'Non-resilient — engineer'
        INFO = 'info', 'Info'
        CALENDAR_INVITE = 'calendar_invite', 'Calendar invite'

    change = models.ForeignKey(Change, on_delete=models.CASCADE, related_name='notifications')
    channel = models.CharField(max_length=20, choices=Channel.choices)
    recipient = models.CharField(max_length=255, blank=True)
    variant = models.CharField(max_length=24, choices=Variant.choices, default=Variant.INFO)
    body = models.TextField(blank=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    success = models.BooleanField(default=False)
    error_message = models.TextField(blank=True)

    class Meta:
        db_table = 'change_notifications'
        ordering = ['-sent_at']

    def __str__(self) -> str:
        return f"{self.channel}/{self.variant} -> {self.recipient}"


class ChangeTemplate(models.Model):
    """A standard-change template with guardrails (§6.6). Owned by IS management."""

    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    auto_approve = models.BooleanField(
        default=False, help_text="Skip the CAB gate (peer-ack) when guardrails hold.",
    )

    # Guardrails — if any is violated the standard CR auto-upgrades to normal (§4).
    requires_all_resilient = models.BooleanField(default=True)
    max_nodes = models.PositiveIntegerField(blank=True, null=True)
    allowed_target_types = models.JSONField(default=list, blank=True)
    allowed_env_types = models.JSONField(default=list, blank=True)
    allowed_clouds = models.JSONField(default=list, blank=True)

    default_precheck_commands = models.TextField(blank=True)
    default_execute_commands = models.TextField(blank=True)
    default_verify_commands = models.TextField(blank=True)
    default_rollback_commands = models.TextField(blank=True)

    owned_by = models.CharField(max_length=255, blank=True, default='is-management')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'change_templates'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class StandardMaintenanceWindow(models.Model):
    """A standing, recurring **regional** maintenance window (docs/cab-design.md §7).

    Seeded one-per-region (AMER/EMEA/APAC). Read-only in the UI; editable by IS
    management via admin. A standard change attaches to its region's next
    occurrence — the attach wiring itself is future work.
    """
    REGION_CHOICES = [('amer', 'AMER'), ('emea', 'EMEA'), ('apac', 'APAC')]
    WEEKDAYS = [
        (0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'), (3, 'Thursday'),
        (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday'),
    ]

    region = models.CharField(max_length=10, choices=REGION_CHOICES, unique=True)
    weekday = models.PositiveSmallIntegerField(
        choices=WEEKDAYS, default=1, help_text="Recurring day of week (Mon=0).")
    start_time = models.TimeField(help_text="Local start time within `timezone`.")
    duration = models.DurationField(default=timedelta(hours=4))
    timezone = models.CharField(
        max_length=64, default='UTC', help_text="IANA tz name, e.g. Europe/London.")
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'standard_maintenance_windows'
        ordering = ['region']

    def __str__(self) -> str:
        return f"{self.get_region_display()} standard window"

    def next_occurrence(self, after=None):
        """Next start datetime (aware, in this window's tz) at/after ``after`` (now)."""
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(self.timezone)
        now = (after or timezone.now()).astimezone(tz)
        days_ahead = (self.weekday - now.weekday()) % 7
        cand = now.replace(
            hour=self.start_time.hour, minute=self.start_time.minute,
            second=0, microsecond=0,
        ) + timedelta(days=days_ahead)
        if cand <= now:
            cand += timedelta(days=7)
        return cand

    @property
    def next_end(self):
        return self.next_occurrence() + self.duration
