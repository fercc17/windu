"""
DORA-metrics data models.

Two append-only event tables feed the four DORA metrics:

- ``Incident``        — PagerDuty incidents (ingested now). Feeds **MTTR** and the
                        incident side of **change failure rate**.
- ``DeploymentEvent`` — Flux reconciliations (ingested *later*; see
                        ``cmdb/integrations/flux.py``). Feeds **deployment
                        frequency**, **lead time for changes**, and the deploy
                        side of **change failure rate**.

Both are append-only and keyed on the upstream id so ingestion is idempotent
(running an ingest twice over the same window produces identical rows).

Cloud/team/environment attribution is resolved at ingest time via the
``Environment`` join (PD service id → ``Environment.oncall_handle``; or a cloud
slug parsed from the title), **never** via which Flux/PD instance reported the
event. That keeps the model identical when the single shared Flux is split into
one-Flux-per-cloud.
"""
import uuid

from django.db import models


class Incident(models.Model):
    """A PagerDuty incident, mirrored read-only for MTTR / change-failure-rate.

    Idempotent on ``pd_id``: re-ingesting refreshes ``status``/``resolved_at`` in
    place rather than creating a duplicate.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Upstream identity
    pd_id = models.CharField(max_length=64, unique=True, db_index=True,
                             help_text="PagerDuty incident id, e.g. PT4KHLK.")
    incident_number = models.IntegerField(blank=True, null=True)
    title = models.CharField(max_length=500, blank=True, null=True)

    STATUS_CHOICES = [
        ('triggered', 'Triggered'),
        ('acknowledged', 'Acknowledged'),
        ('resolved', 'Resolved'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, db_index=True)

    URGENCY_CHOICES = [('high', 'High'), ('low', 'Low')]
    urgency = models.CharField(max_length=10, choices=URGENCY_CHOICES,
                               blank=True, null=True)

    # PagerDuty service the incident fired on
    service_id = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    service_name = models.CharField(max_length=255, blank=True, null=True)

    # Timing — MTTR = resolved_at - created_at
    created_at = models.DateTimeField(db_index=True,
                                      help_text="Incident.created_at from PagerDuty.")
    resolved_at = models.DateTimeField(blank=True, null=True, db_index=True,
                                       help_text="last_status_change_at when status=resolved.")
    html_url = models.URLField(blank=True, null=True)

    # Best-effort attribution (resolved at ingest via the Environment join).
    environment = models.ForeignKey(
        'environments.Environment', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='incidents',
        help_text="Matched via Environment.oncall_handle == service_id, if any.",
    )
    cloud = models.CharField(max_length=50, blank=True, null=True, db_index=True,
                             help_text="Cloud slug parsed from title or the matched env.")
    team = models.CharField(max_length=255, blank=True, null=True, db_index=True,
                            help_text="Owning team of the matched env, or PD team summary.")

    raw = models.JSONField(default=dict, blank=True,
                           help_text="Trimmed raw PagerDuty payload for forensics.")
    ingested_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'dora_incidents'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['cloud', '-created_at']),
            models.Index(fields=['team', '-created_at']),
            models.Index(fields=['status', '-created_at']),
        ]

    def __str__(self) -> str:
        return f"{self.pd_id}:{self.title or ''} ({self.status})"

    @property
    def resolution_seconds(self) -> float | None:
        """Seconds from trigger to resolution, or None if still open."""
        if self.resolved_at and self.created_at:
            return max(0.0, (self.resolved_at - self.created_at).total_seconds())
        return None


class DeploymentEvent(models.Model):
    """A Flux reconciliation of a GitOps-managed environment.

    DEFERRED: no ingestion writes these yet — Flux wiring is open (see
    ``cmdb/integrations/flux.py``). The table exists now so the metrics layer and
    ``/dora/`` view can be built against a stable schema and light up the moment
    deploys start flowing. Until then deploy-sourced metrics render as
    "pending Flux".

    Idempotent on ``(commit_sha, kustomization)`` so re-ingesting a reconcile
    window does not duplicate.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    environment = models.ForeignKey(
        'environments.Environment', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='deployment_events',
    )
    cloud = models.CharField(max_length=50, blank=True, null=True, db_index=True)

    commit_sha = models.CharField(max_length=40, db_index=True,
                                  help_text="Git SHA Flux reconciled to.")
    kustomization = models.CharField(
        max_length=512, blank=True, null=True,
        help_text="Flux Kustomization name/path; join key to Environment.gitops_path.",
    )

    # Timing — lead time = reconciled_at - committed_at
    committed_at = models.DateTimeField(blank=True, null=True,
                                        help_text="Author/commit timestamp of the SHA.")
    reconciled_at = models.DateTimeField(db_index=True,
                                         help_text="When Flux applied this revision.")
    succeeded = models.BooleanField(default=True,
                                    help_text="False = reconcile failed (change-failure signal).")

    source = models.CharField(max_length=20, default='flux')
    raw = models.JSONField(default=dict, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'dora_deployment_events'
        ordering = ['-reconciled_at']
        unique_together = ('commit_sha', 'kustomization')
        indexes = [
            models.Index(fields=['cloud', '-reconciled_at']),
            models.Index(fields=['environment', '-reconciled_at']),
        ]

    def __str__(self) -> str:
        return f"{self.kustomization or self.commit_sha} @ {self.reconciled_at}"

    @property
    def lead_time_seconds(self) -> float | None:
        """Seconds from commit to reconcile, or None if commit time unknown."""
        if self.committed_at and self.reconciled_at:
            return max(0.0, (self.reconciled_at - self.committed_at).total_seconds())
        return None
