"""URL routing for the change-management (CAB) UI."""
from django.urls import path

from . import views

app_name = "changes"

urlpatterns = [
    path("changes/", views.changes_list, name="list"),
    path("changes/new/", views.change_create, name="create"),
    path("changes/ci-search/", views.ci_search, name="ci-search"),
    path("changes/people-search/", views.people_search, name="people-search"),
    path("changes/ci-info/", views.ci_info, name="ci-info"),
    path("changes/standard-windows/", views.standard_windows, name="standard-windows"),
    path("changes/load-demo/", views.load_demo, name="load-demo"),  # TEMP: seed examples from live data

    path("changes/<str:reference>/", views.change_detail, name="detail"),
]
