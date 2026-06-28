"""Admin for the CAB. Read-mostly: the CMDB is not an execution surface (§13)."""
from django.contrib import admin

from .models import (
    Change,
    ChangeAffectedEnvironment,
    ChangeApproval,
    ChangeNotification,
    ChangeTarget,
    ChangeTemplate,
    StandardMaintenanceWindow,
)


class ChangeTargetInline(admin.TabularInline):
    model = ChangeTarget
    extra = 0


class ChangeApprovalInline(admin.TabularInline):
    model = ChangeApproval
    extra = 0
    readonly_fields = ('version', 'level', 'role', 'party', 'decision',
                       'decided_by', 'decided_at')


class ChangeAffectedInline(admin.TabularInline):
    model = ChangeAffectedEnvironment
    extra = 0
    readonly_fields = ('environment_name', 'impact_type', 'dependency_depth',
                       'resilient', 'consumer_team', 'criticality_tier', 'env_type')


@admin.register(Change)
class ChangeAdmin(admin.ModelAdmin):
    list_display = ('reference', 'title', 'change_type', 'status', 'risk_tier',
                    'risk_score', 'region', 'version', 'created_at')
    list_filter = ('change_type', 'status', 'risk_tier', 'region')
    search_fields = ('reference', 'title', 'executer', 'proposer')
    readonly_fields = ('id', 'reference', 'risk_score', 'risk_tier', 'region',
                       'submitted_at', 'approved_at', 'scheduled_at', 'started_at',
                       'completed_at', 'created_at', 'updated_at', 'gcal_event_id')
    inlines = (ChangeTargetInline, ChangeAffectedInline, ChangeApprovalInline)


@admin.register(ChangeTemplate)
class ChangeTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'auto_approve', 'requires_all_resilient', 'max_nodes', 'owned_by')
    search_fields = ('name',)


@admin.register(ChangeNotification)
class ChangeNotificationAdmin(admin.ModelAdmin):
    list_display = ('change', 'channel', 'variant', 'recipient', 'success', 'sent_at')
    list_filter = ('channel', 'variant', 'success')


@admin.register(StandardMaintenanceWindow)
class StandardMaintenanceWindowAdmin(admin.ModelAdmin):
    list_display = ('region', 'weekday', 'start_time', 'duration', 'timezone', 'active')
    list_filter = ('region', 'active')
