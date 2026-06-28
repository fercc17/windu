from django.apps import AppConfig


class DoraConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'cmdb.apps.dora'
    label = 'dora'
    verbose_name = 'DORA metrics'
