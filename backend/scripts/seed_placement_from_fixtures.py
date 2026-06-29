#!/usr/bin/env python3
"""
Dev helper: seed Redis placement keys from the juju fixtures.

In production the poller writes placement to Redis every 5 min; for local
development this script derives equivalent placement from the
``tests/fixtures/juju`` server-list fixtures so the UI and
``link_placement_nodes`` have data to work with.

For each juju model token (see ``scripts/match_k8s_clusters``) that maps to a
real ``Environment`` name, it writes ``env:<name>:placement`` with the same shape
the collector produces (``primary_host``/``secondary_host``/``hosts``/...).

Run::

    DJANGO_SETTINGS_MODULE=cmdb.settings python scripts/seed_placement_from_fixtures.py [--ttl 3600]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import django

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cmdb.settings")
django.setup()

from cmdb.apps.environments.models import Environment  # noqa: E402
from cmdb.redis_client import get_redis_client  # noqa: E402
from scripts.analyze_juju_fixtures import CLOUDS, FIXTURE_DIR, is_real_host, parse_table  # noqa: E402
from scripts.match_k8s_clusters import GENERIC, model_token, norm  # noqa: E402


_FLAVOR_CPU = re.compile(r"cpu(\d+)", re.IGNORECASE)
_FLAVOR_RAM = re.compile(r"ram(\d+)", re.IGNORECASE)


def flavor_specs(flavor_name):
    """Parse (vcpus, ram_mb) from a flavor name like
    ``github-runner-cpu4-ram16-disk50-amd64`` (ram is in GB).

    Returns ``(None, None)`` for named flavors that don't encode specs
    (e.g. ``shared.xlarge``, ``vbuilder``).
    """
    if not flavor_name:
        return None, None
    cpu = _FLAVOR_CPU.search(flavor_name)
    ram = _FLAVOR_RAM.search(flavor_name)
    vcpus = int(cpu.group(1)) if cpu else None
    ram_mb = int(ram.group(1)) * 1024 if ram else None
    return vcpus, ram_mb


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ttl", type=int, default=3600)
    args = ap.parse_args()

    # token -> {cloud, instances:[host,...]} and token -> [Instance,...]
    token_hosts: dict[tuple[str, str], list[str]] = defaultdict(list)
    token_insts: dict[tuple[str, str], list] = defaultdict(list)
    for cloud in CLOUDS:
        path = FIXTURE_DIR / f"{cloud}.txt"
        if not path.exists():
            continue
        for inst in parse_table(path, cloud):
            if inst.status != "ACTIVE" or not is_real_host(inst.host):
                continue
            tok = model_token(inst.name)
            if tok:
                token_hosts[(cloud, tok)].append(inst.host)
                token_insts[(cloud, tok)].append(inst)

    # Build env-name lookup (normalised).
    env_norm: dict[str, str] = {}
    for name in Environment.objects.values_list("name", flat=True):
        env_norm[norm(name)] = name
        env_norm.setdefault(name.lower(), name)

    redis = get_redis_client()
    # Pick, per env, the token with the most instances.
    best: dict[str, tuple[str, str]] = {}
    for (cloud, tok), hosts in token_hosts.items():
        if tok in GENERIC or len(tok) < 5:
            continue
        env_name = env_norm.get(norm(tok)) or env_norm.get(tok.lower())
        if not env_name:
            continue
        if env_name not in best or len(hosts) > len(token_hosts[best[env_name]]):
            best[env_name] = (cloud, tok)

    written = 0
    seeded_clouds: set[str] = set()
    for env_name, (cloud, tok) in best.items():
        insts = token_insts[(cloud, tok)]
        hosts = [i.host for i in insts]
        counts = Counter(hosts)
        ordered = [h for h, _ in counts.most_common()]
        vms = []
        total_vcpus = 0
        total_ram_mb = 0
        for i in insts:
            # Derive per-VM specs from the flavor name where it encodes them.
            vcpus, ram_mb = flavor_specs(i.flavor_name)
            if vcpus:
                total_vcpus += vcpus
            if ram_mb:
                total_ram_mb += ram_mb
            vms.append({
                "name": i.name,
                "host": i.host,
                # Fixtures carry no juju unit column; approximate from the VM name.
                "juju_unit": i.name,
                "status": i.status,
                "flavor": i.flavor_name,
                "availability_zone": i.availability_zone,
                "vcpus": vcpus,
                "ram_mb": ram_mb,
                "architecture": None,
            })
        payload = {
            "environment_name": env_name,
            "primary_host": ordered[0] if ordered else None,
            "secondary_host": ordered[1] if len(ordered) > 1 else None,
            "hosts": sorted(counts),
            "vm_count": len(hosts),
            "vms": vms,
            # Totals summed from per-VM flavor specs (partial: named flavors
            # without cpu/ram in the name contribute nothing).
            "total_vcpus": total_vcpus,
            "total_ram_mb": total_ram_mb,
            "cloud": cloud,
            "source": "juju-fixture-seed",
        }
        redis.setex(f"env:{env_name}:placement", args.ttl, json.dumps(payload))
        seeded_clouds.add(cloud)
        written += 1

    # Flag placement availability per cloud that actually got seeded (not just ps7).
    for cloud in sorted(seeded_clouds):
        redis.setex(f"cloud:{cloud}:placement_available", args.ttl, "true")
    print(f"seeded {written} placement keys (ttl={args.ttl}s)")


if __name__ == "__main__":
    main()
