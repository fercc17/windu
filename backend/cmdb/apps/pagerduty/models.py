"""Canonical PagerDuty store for windu.

This is the single source of PagerDuty truth. The Standup tab's per-engineer
alert counts (derived from ``pd_log_entry``) and DORA's incident metrics both
read from here instead of keeping their own copies (the de-duplication the
schema merge is about). Table names mirror jira-analysis' ``pd`` schema so its
metrics modules re-point to windu by connection only.
"""
from django.db import models


class PdUser(models.Model):
    """A PagerDuty user (an SRE). ``region`` from the user->region map."""
    id = models.TextField(primary_key=True)
    name = models.TextField(null=True, blank=True)
    email = models.TextField(null=True, blank=True)
    region = models.TextField(null=True, blank=True)  # AMER/EMEA/APAC/Unknown

    class Meta:
        db_table = 'pd_user'


class PdTeam(models.Model):
    id = models.TextField(primary_key=True)
    name = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'pd_team'


class PdEscalationPolicy(models.Model):
    id = models.TextField(primary_key=True)
    name = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'pd_escalation_policy'


class PdService(models.Model):
    id = models.TextField(primary_key=True)
    name = models.TextField(null=True, blank=True)
    team_id = models.TextField(null=True, blank=True)
    escalation_policy_id = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'pd_service'


class PdIncident(models.Model):
    """One PagerDuty incident; ack/resolve times and on-call SRE are here."""
    id = models.TextField(primary_key=True)
    incident_number = models.IntegerField(null=True, blank=True)
    title = models.TextField(null=True, blank=True)
    urgency = models.TextField(null=True, blank=True)
    status = models.TextField(null=True, blank=True)
    service_id = models.TextField(null=True, blank=True)
    escalation_policy_id = models.TextField(null=True, blank=True)
    team_id = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField()  # trigger time
    acknowledged_at = models.DateTimeField(null=True, blank=True)  # first ack
    resolved_at = models.DateTimeField(null=True, blank=True)
    assigned_user_id = models.TextField(null=True, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'pd_incident'
        indexes = [
            models.Index(fields=['created_at'], name='ix_pd_incident_created'),
            models.Index(fields=['service_id'], name='ix_pd_incident_service'),
            models.Index(fields=['assigned_user_id'], name='ix_pd_incident_assigned'),
        ]


class PdAlert(models.Model):
    """One alert under an incident; carries derived cloud/model/charm + raw payload."""
    id = models.TextField(primary_key=True)
    incident_id = models.TextField()
    summary = models.TextField(null=True, blank=True)
    alertname = models.TextField(null=True, blank=True)  # normalized alert type
    cloud = models.TextField(null=True, blank=True)
    juju_model = models.TextField(null=True, blank=True)
    juju_model_uuid = models.TextField(null=True, blank=True)
    charm = models.TextField(null=True, blank=True)  # juju application
    juju_unit = models.TextField(null=True, blank=True)
    severity = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField()
    raw_details = models.JSONField(null=True, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'pd_alert'
        indexes = [
            models.Index(fields=['incident_id'], name='ix_pd_alert_incident'),
            models.Index(fields=['alertname'], name='ix_pd_alert_alertname'),
            models.Index(fields=['cloud'], name='ix_pd_alert_cloud'),
            models.Index(fields=['juju_model'], name='ix_pd_alert_model'),
            models.Index(fields=['charm'], name='ix_pd_alert_charm'),
            models.Index(fields=['created_at'], name='ix_pd_alert_created'),
        ]


class PdLogEntry(models.Model):
    """Incident timeline event (trigger/acknowledge/escalate/resolve/assign).

    Feeds MTTA/MTTR and the who-handled-what SRE measures — including Standup's
    per-engineer "alerts acked/resolved in the last 24h".
    """
    id = models.TextField(primary_key=True)
    incident_id = models.TextField()
    type = models.TextField(null=True, blank=True)
    at = models.DateTimeField()
    agent_user_id = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'pd_log_entry'
        indexes = [
            models.Index(fields=['incident_id', 'type', 'at'], name='ix_pd_log_ita'),
            models.Index(fields=['agent_user_id'], name='ix_pd_log_agent'),
        ]


class PdSyncState(models.Model):
    resource = models.TextField(primary_key=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_full_sync_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'pd_sync_state'
