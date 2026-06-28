from django.apps import AppConfig


class StandupConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'cmdb.apps.standup'
    label = 'standup'
    verbose_name = 'Standup dashboard state'
