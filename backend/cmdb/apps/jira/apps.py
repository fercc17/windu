from django.apps import AppConfig


class JiraConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'cmdb.apps.jira'
    label = 'jira'
    verbose_name = 'Jira (ISReq / ISDB)'
