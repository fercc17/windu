"""URL routing for storage resources."""
from django.urls import path

from . import views

app_name = "storage"

urlpatterns = [
    path("storage/", views.storage_list, name="list"),
    path("storage/matrix/", views.storage_matrix, name="matrix"),
    path("api/storage/<str:name>/blast-radius/", views.storage_blast_radius, name="blast-radius"),
    # /storage/matrix/ is registered above so it isn't captured as <name>.
    path("storage/<str:name>/", views.storage_detail, name="detail"),
    path("teams/<str:name>/storage/", views.team_storage, name="team-storage"),
]
