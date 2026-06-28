"""Unified Jira models (covers both the ISReq and ISDB Jira projects).

Ported from jira-analysis' ``isreq`` SQLAlchemy schema into the one windu
``public`` schema. Django owns the DDL here; the reused isreq metrics read the
same physical tables. Point-in-time questions must read the reconstructed
``*_interval`` tables, never ``current_priority``/``current_status`` (Art. VII).
"""
from django.db import models


class JiraUser(models.Model):
    """A Jira user. ``region`` and ``is_external`` drive reporter attribution."""
    account_id = models.TextField(primary_key=True)
    display_name = models.TextField(null=True, blank=True)
    region = models.CharField(max_length=8)  # AMER/EMEA/APAC/Unknown
    is_external = models.BooleanField(default=False)

    class Meta:
        db_table = 'jira_user'


class JiraIssue(models.Model):
    """A Jira issue, deduped by key. ``current_*`` are display-only (Art. VII)."""
    key = models.TextField(primary_key=True)  # e.g. ISREQ-123 / ISDB-456
    jira_id = models.BigIntegerField(null=True, blank=True)
    title = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    current_status = models.TextField(null=True, blank=True)
    current_priority = models.TextField(null=True, blank=True)
    assignee_account_id = models.TextField(null=True, blank=True)
    assignee_name = models.TextField(null=True, blank=True)
    reporter_account_id = models.TextField(null=True, blank=True)
    reporter_name = models.TextField(null=True, blank=True)
    area = models.TextField(null=True, blank=True)
    sub_area = models.TextField(null=True, blank=True)
    pulse = models.TextField(null=True, blank=True)
    is_pr_mp = models.BooleanField(default=False)
    labels = models.JSONField(null=True, blank=True)
    jira_updated_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'jira_issue'
        indexes = [
            models.Index(fields=['created_at'], name='ix_jira_issue_created'),
            models.Index(fields=['current_status'], name='ix_jira_issue_status'),
            models.Index(fields=['area', 'sub_area'], name='ix_jira_issue_area'),
            models.Index(fields=['is_pr_mp'], name='ix_jira_issue_prmp'),
        ]


class JiraIssueLabel(models.Model):
    issue_key = models.TextField()
    label = models.TextField()

    class Meta:
        db_table = 'jira_issue_label'
        constraints = [
            models.UniqueConstraint(fields=['issue_key', 'label'],
                                    name='uq_jira_issue_label'),
        ]
        indexes = [models.Index(fields=['label'], name='ix_jira_label')]


class JiraChangelog(models.Model):
    """Jira changelog row. ``id`` is the stable Jira id (not autoincrement)."""
    id = models.BigIntegerField(primary_key=True)
    issue_key = models.TextField()
    field = models.TextField()
    from_value = models.TextField(null=True, blank=True)
    to_value = models.TextField(null=True, blank=True)
    changed_at = models.DateTimeField()
    author_account_id = models.TextField(null=True, blank=True)
    author_name = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'jira_changelog'
        indexes = [
            models.Index(fields=['issue_key', 'field', 'changed_at'],
                         name='ix_jira_changelog_ifa'),
        ]


class JiraWorklog(models.Model):
    """Time entry. ``id`` is the stable Jira worklog id. No author (FR-018)."""
    id = models.BigIntegerField(primary_key=True)
    issue_key = models.TextField()
    time_spent_seconds = models.IntegerField()
    started_at = models.DateTimeField()
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'jira_worklog'
        indexes = [
            models.Index(fields=['started_at'], name='ix_jira_worklog_started'),
            models.Index(fields=['issue_key'], name='ix_jira_worklog_issue'),
        ]


class JiraPriorityInterval(models.Model):
    """Reconstructed priority timeline (one row per priority spell)."""
    id = models.BigAutoField(primary_key=True)
    issue_key = models.TextField()
    priority = models.TextField(null=True, blank=True)
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'jira_priority_interval'
        indexes = [
            models.Index(fields=['issue_key', 'valid_from'], name='ix_jira_pri_if'),
            models.Index(fields=['priority', 'valid_from', 'valid_to'],
                         name='ix_jira_pri_span'),
        ]


class JiraStatusInterval(models.Model):
    """Reconstructed status timeline (one row per status spell)."""
    id = models.BigAutoField(primary_key=True)
    issue_key = models.TextField()
    status = models.TextField(null=True, blank=True)
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'jira_status_interval'
        indexes = [
            models.Index(fields=['issue_key', 'valid_from'], name='ix_jira_stat_if'),
            models.Index(fields=['status', 'valid_from', 'valid_to'],
                         name='ix_jira_stat_span'),
        ]


class JiraSyncState(models.Model):
    resource = models.TextField(primary_key=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_full_sync_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'jira_sync_state'
