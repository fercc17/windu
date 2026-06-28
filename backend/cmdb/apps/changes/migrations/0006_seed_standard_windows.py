"""Seed the three regional standard maintenance windows (docs/cab-design.md §7)."""
from datetime import time, timedelta

from django.db import migrations

# region, weekday (Mon=0), local start, duration hours, IANA timezone
SEED = [
    ('amer', 2, time(11, 0), 4, 'America/New_York'),  # Wed 11:00 ET
    ('emea', 1, time(10, 0), 4, 'Europe/Madrid'),     # Tue 10:00 Madrid (~1h after a 9:00 start)
    ('apac', 0, time(2, 0), 4, 'UTC'),                # Mon 02:00 UTC (= 8 PM Mexico City the prev day)
]


def seed(apps, schema_editor):
    Window = apps.get_model('changes', 'StandardMaintenanceWindow')
    for region, weekday, start, hours, tz in SEED:
        Window.objects.update_or_create(
            region=region,
            defaults=dict(weekday=weekday, start_time=start,
                          duration=timedelta(hours=hours), timezone=tz, active=True),
        )


def unseed(apps, schema_editor):
    Window = apps.get_model('changes', 'StandardMaintenanceWindow')
    Window.objects.filter(region__in=[s[0] for s in SEED]).delete()


class Migration(migrations.Migration):
    dependencies = [('changes', '0005_standardmaintenancewindow')]
    operations = [migrations.RunPython(seed, unseed)]
