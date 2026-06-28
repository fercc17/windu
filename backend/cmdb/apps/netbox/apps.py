from django.apps import AppConfig


class NetboxConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'cmdb.apps.netbox'
    label = 'netbox'
    verbose_name = 'Netbox / Physical nodes'
