"""Standup dashboard state, ported into the unified windu schema.

Faithful copy of standup-dashboard's tables EXCEPT ``alert`` and ``incident``:
those duplicate PagerDuty data, so the Standup tab reads the canonical
``pd_*`` tables (cmdb.apps.pagerduty) instead — the de-duplication the user
called out.

Timestamps were stored as ISO-8601 TEXT in the source (SQLite-portable), so we
keep TextField here for a 1:1 ETL copy; they can be converted to timestamptz in
a later pass. The source ``ticket.id`` (the Jira key) is mapped to ``ticket_key``
to avoid clashing with Django's surrogate ``id`` primary key.
"""
from django.db import models


class FetchSnapshot(models.Model):
    """One refresh cycle. Other tables reference it by ``fetch_id`` (kept stable)."""
    id = models.BigIntegerField(primary_key=True)
    fetched_at = models.TextField()
    jira_ok = models.IntegerField(null=True, blank=True)
    pagerduty_ok = models.IntegerField(null=True, blank=True)
    ical_ok = models.IntegerField(null=True, blank=True)
    raw_path = models.TextField()
    partial = models.IntegerField()

    class Meta:
        db_table = 'standup_fetch_snapshot'


class StandupTicket(models.Model):
    """Sprint issue snapshot per fetch (ISDB + ISReq). ``ticket_key`` = Jira key."""
    fetch_id = models.BigIntegerField()
    ticket_key = models.TextField()  # source column: id
    project_key = models.TextField()
    title = models.TextField()
    status = models.TextField()
    priority = models.TextField(null=True, blank=True)
    labels_json = models.TextField()
    assignee_email = models.TextField(null=True, blank=True)
    sprint_id = models.BigIntegerField(null=True, blank=True)
    is_done_date = models.TextField(null=True, blank=True)
    created = models.TextField(null=True, blank=True)
    status_category = models.TextField(null=True, blank=True)
    reporter_email = models.TextField(null=True, blank=True)
    wip_since = models.TextField(null=True, blank=True)
    estimate_seconds = models.IntegerField(null=True, blank=True)
    spent_seconds = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = 'standup_ticket'
        constraints = [
            models.UniqueConstraint(fields=['fetch_id', 'ticket_key'],
                                    name='uq_standup_ticket'),
        ]
        indexes = [models.Index(fields=['ticket_key'], name='ix_standup_ticket_key')]


class TouchEvent(models.Model):
    """An engineer's interaction with a ticket (status/comment/assignment/worklog/link)."""
    fetch_id = models.BigIntegerField()
    ticket_id = models.TextField()  # Jira key the touch is on
    engineer_email = models.TextField()
    kind = models.TextField()
    at = models.TextField()
    seconds = models.IntegerField()

    class Meta:
        db_table = 'standup_touch_event'
        constraints = [
            models.UniqueConstraint(
                fields=['fetch_id', 'ticket_id', 'engineer_email', 'kind', 'at'],
                name='uq_standup_touch_event'),
        ]
        indexes = [
            models.Index(fields=['engineer_email', 'at'], name='ix_standup_touch_eng'),
        ]


class GithubPr(models.Model):
    """Per-engineer GitHub PR counts for a fetch (pulse / 24h / today windows)."""
    fetch_id = models.BigIntegerField()
    engineer_email = models.TextField()
    created = models.IntegerField()
    merged = models.IntegerField()
    updated = models.IntegerField()
    reviewed = models.IntegerField()
    created_24h = models.IntegerField()
    merged_24h = models.IntegerField()
    updated_24h = models.IntegerField()
    reviewed_24h = models.IntegerField()
    created_today = models.IntegerField()
    merged_today = models.IntegerField()
    updated_today = models.IntegerField()
    reviewed_today = models.IntegerField()

    class Meta:
        db_table = 'standup_github_pr'
        constraints = [
            models.UniqueConstraint(fields=['fetch_id', 'engineer_email'],
                                    name='uq_standup_github_pr'),
        ]


class CalendarAvail(models.Model):
    fetch_id = models.BigIntegerField()
    engineer_email = models.TextField()
    busy_seconds = models.IntegerField()
    open_seconds = models.IntegerField()
    pto_seconds = models.IntegerField()
    sd_days = models.TextField()
    busy_today = models.IntegerField()
    open_today = models.IntegerField()
    busy_24h = models.IntegerField()
    open_24h = models.IntegerField()
    pto_days = models.TextField()

    class Meta:
        db_table = 'standup_calendar_avail'
        constraints = [
            models.UniqueConstraint(fields=['fetch_id', 'engineer_email'],
                                    name='uq_standup_calendar_avail'),
        ]


class Pulse(models.Model):
    fetch_id = models.BigIntegerField()
    project_key = models.TextField()
    sprint_id = models.BigIntegerField()
    name = models.TextField()
    start = models.TextField()
    end = models.TextField()
    state = models.TextField()

    class Meta:
        db_table = 'standup_pulse'
        constraints = [
            models.UniqueConstraint(fields=['fetch_id', 'sprint_id'],
                                    name='uq_standup_pulse'),
        ]


class PulseSummary(models.Model):
    """Pre-computed per-pulse, per-region aggregates."""
    pulse_number = models.IntegerField()
    region = models.TextField()
    new_highest = models.IntegerField()
    new_pr_mp = models.IntegerField()
    new_ps5 = models.IntegerField()
    new_regular = models.IntegerField()
    new_total = models.IntegerField()
    closed_highest = models.IntegerField()
    closed_pr_mp = models.IntegerField()
    closed_ps5 = models.IntegerField()
    closed_total = models.IntegerField()
    isdb_closed = models.IntegerField()
    alerts_triggered = models.IntegerField()
    alerts_ack = models.IntegerField()
    alerts_resolved = models.IntegerField()
    alerts_total = models.IntegerField()
    alert_mttr_sum = models.IntegerField()
    alert_mttr_n = models.IntegerField()
    alert_mtta_sum = models.IntegerField()
    alert_mtta_n = models.IntegerField()
    ticket_cycle_sum = models.IntegerField()
    ticket_cycle_n = models.IntegerField()
    breakdowns_json = models.TextField(null=True, blank=True)
    updated_at = models.TextField()

    class Meta:
        db_table = 'standup_pulse_summary'
        constraints = [
            models.UniqueConstraint(fields=['pulse_number', 'region'],
                                    name='uq_standup_pulse_summary'),
        ]


class OpenSummaryCount(models.Model):
    fetch_id = models.BigIntegerField(primary_key=True)
    highest = models.IntegerField(null=True, blank=True)
    ps5 = models.IntegerField(null=True, blank=True)
    ps5_highest = models.IntegerField(null=True, blank=True)
    pr_mp = models.IntegerField(null=True, blank=True)
    escalated = models.IntegerField(null=True, blank=True)
    ongoing_alerts = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = 'standup_open_summary_count'


class WeekendOncall(models.Model):
    fetch_id = models.BigIntegerField()
    engineer_email = models.TextField()
    weekend_start = models.TextField()
    weekend_end = models.TextField()

    class Meta:
        db_table = 'standup_weekend_oncall'
        constraints = [
            models.UniqueConstraint(fields=['fetch_id', 'engineer_email', 'weekend_start'],
                                    name='uq_standup_weekend_oncall'),
        ]


class RawSnapshot(models.Model):
    """Append-only raw Jira/PagerDuty/iCal payloads (FR-028)."""
    fetch_id = models.BigIntegerField()
    name = models.TextField()
    payload = models.JSONField()

    class Meta:
        db_table = 'standup_raw_snapshot'
        constraints = [
            models.UniqueConstraint(fields=['fetch_id', 'name'],
                                    name='uq_standup_raw_snapshot'),
        ]


# --- State/config tables (row-versioned: latest row wins; no source PK) ---

class RoleSchedule(models.Model):
    engineer_email = models.TextField()
    weekday = models.TextField()
    role = models.TextField()
    updated_at = models.TextField()

    class Meta:
        db_table = 'standup_role_schedule'
        indexes = [models.Index(fields=['engineer_email', 'weekday'],
                                name='ix_standup_role_sched')]


class RoleOverride(models.Model):
    engineer_email = models.TextField()
    role = models.TextField()
    effective_date = models.TextField()
    expires_at = models.TextField()
    created_at = models.TextField()

    class Meta:
        db_table = 'standup_role_override'
        indexes = [models.Index(fields=['engineer_email'], name='ix_standup_role_ovr')]


class DayNote(models.Model):
    engineer_email = models.TextField()
    weekday = models.TextField()
    note = models.TextField()
    updated_at = models.TextField()
    note_date = models.TextField()

    class Meta:
        db_table = 'standup_day_note'
        indexes = [models.Index(fields=['engineer_email', 'note_date'],
                                name='ix_standup_day_note')]


class RosterAddition(models.Model):
    email = models.TextField()
    name = models.TextField()
    region = models.TextField()
    github_login = models.TextField()
    created_at = models.TextField()

    class Meta:
        db_table = 'standup_roster_addition'


class RegionOverride(models.Model):
    email = models.TextField()
    region = models.TextField()
    updated_at = models.TextField()

    class Meta:
        db_table = 'standup_region_override'


class UiState(models.Model):
    key = models.TextField()
    value = models.TextField()
    updated_at = models.TextField()

    class Meta:
        db_table = 'standup_ui_state'


class JiraAccount(models.Model):
    """Reverse map for private-email engineers (accountId <-> email)."""
    email = models.TextField(primary_key=True)
    account_id = models.TextField()

    class Meta:
        db_table = 'standup_jira_account'
