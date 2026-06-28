"""Load host-aggregate membership from the committed CSVs in data/host_aggregates/.

This populates ``Node.host_aggregate`` so a fresh dev DB gets the data via
``migrate`` (idempotent). It only updates nodes that already exist; if nodes are
seeded *after* this migration runs (e.g. a later ``reconcile_netbox``), re-apply
with ``manage.py load_host_aggregates data/host_aggregates/*.csv``.
"""
from __future__ import annotations

import csv
from pathlib import Path

from django.conf import settings
from django.db import migrations


def load_aggregates(apps, schema_editor):
    Node = apps.get_model("netbox", "Node")
    data_dir = Path(settings.BASE_DIR) / "data" / "host_aggregates"
    if not data_dir.exists():
        return
    for path in sorted(data_dir.glob("*.csv")):
        with path.open(newline="") as fh:
            for row in csv.DictReader(fh):
                hostname = (row.get("node") or "").strip()
                aggregate = (row.get("host_aggregate") or "").strip()
                if not hostname:
                    continue
                Node.objects.filter(hostname__iexact=hostname).update(
                    host_aggregate=aggregate
                )


def unload_aggregates(apps, schema_editor):
    Node = apps.get_model("netbox", "Node")
    Node.objects.exclude(host_aggregate__isnull=True).update(host_aggregate=None)


class Migration(migrations.Migration):
    dependencies = [
        ("netbox", "0004_node_host_aggregate"),
    ]

    operations = [
        migrations.RunPython(load_aggregates, unload_aggregates),
    ]
