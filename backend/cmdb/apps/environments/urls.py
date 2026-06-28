"""URL routing for environments app."""
from django.urls import path
from django.views.generic.base import RedirectView

from . import views

urlpatterns = [
    path('', views.EnvironmentListView.as_view(), name='environment-list'),
    path('export/csv/', views.export_csv, name='environment-export-csv'),
    path('export/xls/', views.export_xls, name='environment-export-xls'),

    # Aggregation views
    path('teams/', views.team_aggregation, name='team-aggregation'),
    path('charms/', views.charm_statistics, name='charm-statistics'),
    path('charms/outdated/', views.charm_outdated, name='charm-outdated'),
    path('cia/', views.cia_assessment, name='cia-assessment'),
    path('cloud-regions/', views.cloud_region_capacity, name='cloud-region-capacity'),
    path('controllers/', views.controller_health, name='controller-health'),

    # Service-category dashboards
    path('services/', views.services_overview, name='services-overview'),
    path('services/juju/', views.juju_controllers, name='juju-controllers'),
    path('k8s/', views.k8s_clusters, name='k8s-clusters'),
    path('k8s/<str:name>/', views.k8s_cluster_detail, name='k8s-cluster-detail'),
    path('dbaas/', views.dbaas_view, name='dbaas'),
    path('ck8s-aas/', views.ck8s_aas_view, name='ck8s-aas'),
    path('jenkins-aas/', views.jenkins_aas_view, name='jenkins-aas'),
    path('builders/', views.builders_view, name='builders'),
    path('dependencies/hotspots/', views.dependency_hotspots, name='dependency-hotspots'),
    path('versions/', views.version_compliance, name='version-compliance'),
    path('owners/<str:owner>/', views.owner_dashboard, name='owner-dashboard'),
    path('owners/', views.owner_dashboard, name='owner-dashboard-select'),
    path('lifecycle/', views.lifecycle_timeline, name='lifecycle-timeline'),
    path('risk-heatmap/', views.risk_heatmap, name='risk-heatmap'),
    path('service-primitives/', views.service_primitives_inventory, name='service-primitives'),
    path('gitops/', views.gitops_overview, name='gitops-overview'),
    path('gitops/teams/', views.gitops_teams, name='gitops-teams'),

    # API endpoints
    path('api/autocomplete/', views.autocomplete, name='autocomplete'),
    path('api/environments/<str:name>/blast-radius/', views.blast_radius, name='blast-radius'),

    # Environment detail (must be last to avoid conflicts)
    # A Juju model's detail page. Canonical path is /model/<name>/; the old
    # /environments/<name>/ redirects so existing links/bookmarks keep working.
    path('model/<str:name>/', views.environment_detail, name='environment-detail'),
    path('environments/<str:name>/',
         RedirectView.as_view(pattern_name='environment-detail', permanent=False, query_string=True)),
]
