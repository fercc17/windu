"""URL routing for the DORA-metrics dashboard."""
from django.urls import path

from . import views

app_name = "dora"

urlpatterns = [
    path("dora/", views.dora_overview, name="overview"),
    # Flux notification-controller posts reconcile events here. The optional
    # <cloud> segment selects a per-cloud HMAC secret for the 1-Flux-per-cloud
    # future; the bare path serves the single shared Flux today.
    path("dora/flux/webhook/", views.flux_webhook, name="flux-webhook"),
    path("dora/flux/webhook/<slug:cloud>/", views.flux_webhook, name="flux-webhook-cloud"),
]
