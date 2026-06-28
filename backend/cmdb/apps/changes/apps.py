from django.apps import AppConfig


class ChangesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'cmdb.apps.changes'
    label = 'changes'
    verbose_name = 'Change management (CAB)'
