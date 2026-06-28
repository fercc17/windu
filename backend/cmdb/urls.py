"""
URL configuration for IS-CMDB project.
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('cmdb.apps.environments.urls')),
    # maintenance before netbox: claims nodes/<h>/maintenance/* before the
    # greedy nodes/<path:hostname>/ node-detail route can swallow it.
    path('', include('cmdb.apps.maintenance.urls')),
    path('', include('cmdb.apps.netbox.urls')),
    path('', include('cmdb.apps.storage.urls')),
    path('', include('cmdb.apps.dora.urls')),
    path('', include('cmdb.apps.changes.urls')),
    path('api/', include('cmdb.apps.api.urls')),
]
