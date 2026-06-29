"""API URL routing."""
from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from cmdb.apps.netbox.webhook import netbox_webhook

from . import views
from . import pages_views

urlpatterns = [
    path('health/', views.health, name='api-health'),
    path('me/', pages_views.me, name='api-me'),
    path('pages/<str:page_id>/', pages_views.page, name='api-page'),
    path('refresh/<str:source>/', pages_views.refresh, name='api-refresh'),
    path('standup/schedule/', pages_views.standup_schedule, name='api-standup-schedule'),
    path('standup/role/', pages_views.standup_role, name='api-standup-role'),
    path('standup/note/', pages_views.standup_note, name='api-standup-note'),
    path('standup/paste/', pages_views.standup_paste, name='api-standup-paste'),
    path('standup/offenders/', pages_views.standup_offenders, name='api-standup-offenders'),
    path('standup/aging-wip/', pages_views.standup_aging, name='api-standup-aging'),
    path('standup/focus/', pages_views.standup_focus, name='api-standup-focus'),
    path('standup/pulse-counts/', pages_views.standup_pulse_counts, name='api-standup-pulse-counts'),
    path('teams/resource-utilization/', views.team_resource_utilization, name='team-resource-utilization'),
    path('webhooks/netbox/', netbox_webhook, name='netbox-webhook'),
    path('schema/', SpectacularAPIView.as_view(), name='api-schema'),
    path('docs/', SpectacularSwaggerView.as_view(url_name='api-schema'), name='api-docs'),
]
