"""URL routing for maintenance windows.

Included BEFORE the netbox app at the root so the node-scoped maintenance route
is not shadowed by netbox's greedy ``nodes/<path:hostname>/`` node-detail route.
"""
from django.urls import path

from . import views

app_name = "maintenance"

urlpatterns = [
    # Registered before netbox's clouds/<slug>/ so it isn't read as a slug.
    path("clouds/decommission-log/", views.decommission_log, name="decommission-log"),
    path(
        "clouds/<slug:slug>/maintenance/new/",
        views.maintenance_window_new_cloud,
        name="window-new-cloud",
    ),
    path("maintenance/", views.maintenance_list, name="window-list"),
    path("maintenance/<int:pk>/", views.maintenance_detail, name="window-detail"),
    path("maintenance/<int:pk>/cancel/", views.maintenance_window_cancel, name="window-cancel"),
    path(
        "nodes/<path:hostname>/maintenance/new/",
        views.maintenance_window_new,
        name="window-new",
    ),
    path(
        "environments/<str:name>/maintenance/new/",
        views.maintenance_window_new_env,
        name="window-new-env",
    ),
]
