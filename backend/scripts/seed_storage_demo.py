#!/usr/bin/env python3
"""
Dev helper: seed StorageResource / StorageEnvironmentAccess from the DB.

Production data comes from ``tools/rados_ingest.py`` (stubbed until RadosGW
credentials exist). For local development this derives one StorageResource per
``service_class='object_storage'`` environment and wires up some intra-team and
cross-cloud access so the storage views (#45/#46/#47/#53) have realistic data.

Deterministic (crc32-based sizes) so reruns are stable.

Run::

    DJANGO_SETTINGS_MODULE=cmdb.settings python scripts/seed_storage_demo.py
"""
from __future__ import annotations

import os
import sys
import zlib
from collections import defaultdict
from pathlib import Path

import django

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cmdb.settings")
django.setup()

from cmdb.apps.environments.models import Environment  # noqa: E402
from cmdb.apps.storage.models import StorageEnvironmentAccess, StorageResource  # noqa: E402


def _stable(n: str, mod: int) -> int:
    return zlib.crc32(n.encode()) % mod


def main() -> None:
    owners = list(
        Environment.objects.filter(service_class="object_storage").order_by("name")
    )
    envs_by_team: dict[str, list] = defaultdict(list)
    for e in Environment.objects.exclude(team__isnull=True).exclude(team=""):
        envs_by_team[e.team].append(e)
    cross_pool = list(
        Environment.objects.exclude(cloud__isnull=True).exclude(cloud="").order_by("name")
    )

    storages = 0
    accesses = 0
    for i, owner in enumerate(owners):
        team = owner.team or owner.owner
        storage, _ = StorageResource.objects.update_or_create(
            name=owner.name,
            defaults={
                "bucket_name": f"{owner.name}-bucket",
                "cloud": owner.cloud or "unknown",
                "owner_team": team,
                "size_gb": float(_stable(owner.name, 50000) + 10),
                "object_count": _stable(owner.name + "obj", 5_000_000),
                "storage_type": "radosgw",
            },
        )
        storages += 1

        targets = [(owner, "readwrite")]
        # Same-team environments share the bucket (read-only).
        for mate in envs_by_team.get(team, []):
            if mate.id != owner.id and len(targets) < 4:
                targets.append((mate, "readonly"))
        # Every 3rd bucket also gets a cross-cloud consumer.
        if cross_pool and i % 3 == 0:
            cand = cross_pool[_stable(owner.name + "x", len(cross_pool))]
            if cand.cloud != owner.cloud:
                targets.append((cand, "readonly"))

        for env, access in targets:
            _, created = StorageEnvironmentAccess.objects.update_or_create(
                storage=storage, environment=env, defaults={"access_type": access}
            )
            accesses += int(created)

    print(f"seeded {storages} storage resources, {accesses} new access links")


if __name__ == "__main__":
    main()
