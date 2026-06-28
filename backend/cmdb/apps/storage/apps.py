from django.apps import AppConfig


class StorageConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'cmdb.apps.storage'
    label = 'storage'
    verbose_name = 'Storage resources'
