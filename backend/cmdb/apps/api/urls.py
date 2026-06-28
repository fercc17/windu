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
    path('teams/resource-utilization/', views.team_resource_utilization, name='team-resource-utilization'),
    path('webhooks/netbox/', netbox_webhook, name='netbox-webhook'),
    path('schema/', SpectacularAPIView.as_view(), name='api-schema'),
    path('docs/', SpectacularSwaggerView.as_view(url_name='api-schema'), name='api-docs'),
]
