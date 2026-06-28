from django.apps import AppConfig


class PagerDutyConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'cmdb.apps.pagerduty'
    label = 'pagerduty'
    verbose_name = 'PagerDuty (canonical alert/incident store)'
