"""
Maintenance window models.

A ``MaintenanceWindow`` covers one physical ``Node`` for a time range; the
environments placed on that node are recorded via ``MaintenanceWindowEnvironment``
(blast radius) and each notification attempt via ``MaintenanceNotificationChannel``.
"""
from django.db import models


class MaintenanceWindow(models.Model):
    """A maintenance/alert-silence window over one of three scopes.

    Exactly one of ``node`` / ``cloud`` / ``environment`` identifies the target:
    - node        -> silences the node's cloud PagerDuty service (affects all
                     juju models placed on that node).
    - cloud       -> silences that cloud's PagerDuty service.
    - environment -> silences a single juju model on the COS/Alertmanager side
                     (PagerDuty windows are service-scoped, so they cannot target
                     one model).
    """

    SCOPE_NODE = 'node'
    SCOPE_CLOUD = 'cloud'
    SCOPE_ENVIRONMENT = 'environment'

    # node is now nullable because a window may instead target a cloud or env.
    node = models.ForeignKey(
        'netbox.Node', on_delete=models.CASCADE, related_name='maintenance_windows',
        null=True, blank=True,
    )
    cloud = models.CharField(max_length=50, blank=True, null=True, db_index=True,
                             help_text="Cloud slug for cloud-scoped windows.")
    environment = models.ForeignKey(
        'environments.Environment', on_delete=models.CASCADE,
        related_name='maintenance_windows', null=True, blank=True,
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reason = models.TextField()

    STATUS_CHOICES = [
        ('scheduled', 'Scheduled'),
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='scheduled',
                              db_index=True)
    pagerduty_window_id = models.CharField(max_length=100, blank=True, null=True)
    cos_silence_id = models.CharField(max_length=100, blank=True, null=True,
                                      help_text="Alertmanager/COS silence id (env scope).")
    created_by = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'maintenance_windows'
        ordering = ['-starts_at']
        indexes = [
            models.Index(fields=['node', 'status']),
            models.Index(fields=['status', 'starts_at']),
            models.Index(fields=['cloud', 'status']),
            models.Index(fields=['environment', 'status']),
        ]

    @property
    def scope(self) -> str:
        if self.environment_id:
            return self.SCOPE_ENVIRONMENT
        if self.cloud:
            return self.SCOPE_CLOUD
        return self.SCOPE_NODE

    @property
    def resolved_cloud(self):
        """The cloud a PagerDuty service-silence would apply to, per scope."""
        if self.environment_id:
            return self.environment.cloud
        if self.cloud:
            return self.cloud
        return self.node.cloud if self.node_id else None

    @property
    def target_label(self) -> str:
        if self.environment_id:
            return self.environment.name
        if self.cloud:
            return self.cloud
        return self.node.hostname if self.node_id else "—"

    def __str__(self) -> str:
        return f"{self.scope}:{self.target_label}:{self.starts_at:%Y-%m-%d %H:%M} ({self.status})"


class MaintenanceNotificationChannel(models.Model):
    window = models.ForeignKey(
        MaintenanceWindow, on_delete=models.CASCADE, related_name='channels'
    )

    CHANNEL_CHOICES = [
        ('pagerduty', 'PagerDuty'),
        ('cos', 'COS / Alertmanager'),
        ('mattermost', 'Mattermost'),
        ('email', 'Email'),
    ]
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES)
    sent_at = models.DateTimeField(blank=True, null=True)
    success = models.BooleanField(default=False)
    error_message = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'maintenance_notification_channels'
        ordering = ['-sent_at']

    def __str__(self) -> str:
        return f"{self.channel} for window {self.window_id} ({'ok' if self.success else 'fail'})"


class MaintenanceWindowEnvironment(models.Model):
    window = models.ForeignKey(MaintenanceWindow, on_delete=models.CASCADE)
    environment = models.ForeignKey('environments.Environment', on_delete=models.CASCADE)

    class Meta:
        db_table = 'maintenance_window_environments'
        unique_together = ('window', 'environment')

    def __str__(self) -> str:
        return f"{self.window_id} -> {self.environment_id}"
