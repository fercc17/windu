"""URL routing for the netbox (physical nodes / clouds) app."""
from django.urls import path

from . import views

app_name = "netbox"

urlpatterns = [
    path("clouds/", views.cloud_list, name="cloud-list"),
    # Action route must precede the greedy <slug:slug> cloud-detail.
    path("clouds/collect/", views.trigger_netbox_collection, name="trigger-collection"),
    path("clouds/<slug:slug>/", views.cloud_detail, name="cloud-detail"),
    # Specific node sub-routes before the greedy <path:> node-detail.
    path("nodes/<path:hostname>/resilience/", views.node_resilience, name="node-resilience"),
    # Node detail. <path:> because hostnames may contain dots, slashes, spaces.
    path("nodes/<path:hostname>/", views.node_detail, name="node-detail"),
]
