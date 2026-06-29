#!/usr/bin/env python3
"""
Analyse the openstack ``server list`` fixtures under ``tests/fixtures/juju/``.

Each fixture (``ps5.txt``, ``ps6.txt``, ``ps7.txt``) is the raw ASCII-table
output of ``openstack server list --long`` for one cloud. This script parses
those tables and extracts the distinct ``(cloud, availability_zone, host)``
tuples for ACTIVE instances, plus supporting statistics used by Task 0 of the
overnight build (issue #21 Juju section).

Run with ``--emit`` to (re)write ``docs/findings/az-node-mapping.json``;
without it the script only prints a human-readable summary to stdout.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "juju"
FINDINGS_DIR = REPO_ROOT / "docs" / "findings"
CLOUDS = ["ps5", "ps6", "ps7"]

# Column order in the fixtures.
COLUMNS = [
    "id", "name", "status", "task_state", "power_state", "networks",
    "image_name", "image_id", "flavor_name", "flavor_id",
    "availability_zone", "host", "properties",
]


@dataclass
class Instance:
    cloud: str
    id: str
    name: str
    status: str
    flavor_name: str
    availability_zone: str
    host: str


def parse_table(path: Path, cloud: str) -> Iterator[Instance]:
    """Yield one Instance per data row of an openstack server-list table."""
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue  # skip +---+ borders and blank lines
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) != len(COLUMNS):
            continue
        if cells[0].lower() == "id":
            continue  # header row
        row = dict(zip(COLUMNS, cells))
        yield Instance(
            cloud=cloud,
            id=row["id"],
            name=row["name"],
            status=row["status"],
            flavor_name=row["flavor_name"],
            availability_zone=row["availability_zone"],
            host=row["host"],
        )


def load_all() -> dict[str, list[Instance]]:
    data: dict[str, list[Instance]] = {}
    for cloud in CLOUDS:
        path = FIXTURE_DIR / f"{cloud}.txt"
        if not path.exists():
            logger.warning("fixture missing: %s", path)
            data[cloud] = []
            continue
        data[cloud] = list(parse_table(path, cloud))
    return data


def is_real_host(host: str) -> bool:
    return bool(host) and host.lower() != "none"


def summarise(data: dict[str, list[Instance]]) -> None:
    for cloud, instances in data.items():
        statuses = Counter(i.status for i in instances)
        active = [i for i in instances if i.status == "ACTIVE"]
        active_with_host = [i for i in active if is_real_host(i.host)]
        azs = Counter(i.availability_zone for i in active_with_host if i.availability_zone)
        hosts_per_az: dict[str, set[str]] = defaultdict(set)
        for i in active_with_host:
            if i.availability_zone:
                hosts_per_az[i.availability_zone].add(i.host)
        flavors = Counter(i.flavor_name for i in active if i.flavor_name)
        print(f"\n=== {cloud} ===")
        print(f"  rows total            : {len(instances)}")
        print(f"  status distribution   : {dict(statuses)}")
        print(f"  ACTIVE                : {len(active)}")
        print(f"  ACTIVE w/ real host   : {len(active_with_host)}")
        print(f"  distinct AZs          : {len(azs)} -> {dict(azs)}")
        print(f"  distinct hosts        : {len({i.host for i in active_with_host})}")
        print("  hosts per AZ          :")
        for az, hosts in sorted(hosts_per_az.items()):
            print(f"     {az:<24} {len(hosts)} hosts  e.g. {sorted(hosts)[:2]}")
        print(f"  top flavors           : {dict(flavors.most_common(8))}")
        # Sample host names to learn the naming convention.
        print(f"  sample hosts          : {sorted({i.host for i in active_with_host})[:5]}")


def build_mapping(data: dict[str, list[Instance]]) -> dict:
    mapping: dict = {}
    for cloud, instances in data.items():
        active = [i for i in instances if i.status == "ACTIVE" and is_real_host(i.host)]
        host_to_az: dict[str, str] = {}
        for i in active:
            if i.availability_zone:
                host_to_az[i.host] = i.availability_zone
        mapping[cloud] = dict(sorted(host_to_az.items()))
    return mapping


def k8s_candidates(data: dict[str, list[Instance]]) -> dict[str, list[str]]:
    """Instance names hinting at Kubernetes (control-plane / worker / k8s)."""
    pat = re.compile(r"(k8s|kubernetes|control[-_]?plane|worker)", re.IGNORECASE)
    out: dict[str, list[str]] = {}
    for cloud, instances in data.items():
        names = sorted({i.name for i in instances if pat.search(i.name)})
        out[cloud] = names
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", action="store_true", help="write az-node-mapping.json")
    args = ap.parse_args()

    data = load_all()
    summarise(data)

    k8s = k8s_candidates(data)
    print("\n=== K8s-name candidates (by pattern) ===")
    for cloud, names in k8s.items():
        print(f"  {cloud}: {len(names)} names; sample {names[:6]}")

    if args.emit:
        mapping = build_mapping(data)
        FINDINGS_DIR.mkdir(parents=True, exist_ok=True)
        out = FINDINGS_DIR / "az-node-mapping.json"
        out.write_text(json.dumps(mapping, indent=2) + "\n")
        total = sum(len(v) for v in mapping.values())
        logger.info("wrote %s (%d hosts across %d clouds)", out, total, len(mapping))


if __name__ == "__main__":
    main()
