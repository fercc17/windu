"""
Seed Redis placement keys for ps5/ps6/ps7 from the juju fixtures under
``tests/fixtures/juju`` (dev/demo).

In production the poller writes placement every 5 min; locally this derives the
same ``env:<name>:placement`` shape from the ``openstack server list`` fixtures
so the UI has live data to show. Mirrors ``scripts/seed_placement_from_fixtures.py``
but as a management command so views / call_command can run it.
"""
import json
import re
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand

from cmdb.apps.environments.models import Environment
from cmdb.redis_client import get_redis_client
from scripts.analyze_juju_fixtures import CLOUDS, FIXTURE_DIR, is_real_host, parse_table
from scripts.match_k8s_clusters import GENERIC, model_token, norm

_FLAVOR_CPU = re.compile(r"cpu(\d+)", re.IGNORECASE)
_FLAVOR_RAM = re.compile(r"ram(\d+)", re.IGNORECASE)


def _flavor_specs(flavor_name):
    """(vcpus, ram_mb) parsed from a flavor like ``...-cpu4-ram16-...`` (ram in GB)."""
    if not flavor_name:
        return None, None
    cpu = _FLAVOR_CPU.search(flavor_name)
    ram = _FLAVOR_RAM.search(flavor_name)
    return (int(cpu.group(1)) if cpu else None,
            int(ram.group(1)) * 1024 if ram else None)


class Command(BaseCommand):
    help = "Seed Redis placement for ps5/ps6/ps7 from tests/fixtures/juju (dev)."

    def add_arguments(self, parser):
        parser.add_argument("--ttl", type=int, default=3600)

    def handle(self, *args, **opts):
        ttl = opts["ttl"]

        token_hosts: dict[tuple[str, str], list[str]] = defaultdict(list)
        token_insts: dict[tuple[str, str], list] = defaultdict(list)
        clouds_seen = []
        for cloud in CLOUDS:
            path = FIXTURE_DIR / f"{cloud}.txt"
            if not path.exists():
                continue
            clouds_seen.append(cloud)
            for inst in parse_table(path, cloud):
                if inst.status != "ACTIVE" or not is_real_host(inst.host):
                    continue
                tok = model_token(inst.name)
                if tok:
                    token_hosts[(cloud, tok)].append(inst.host)
                    token_insts[(cloud, tok)].append(inst)

        env_norm: dict[str, str] = {}
        for name in Environment.objects.values_list("name", flat=True):
            env_norm[norm(name)] = name
            env_norm.setdefault(name.lower(), name)

        # Per env, pick the token with the most instances.
        best: dict[str, tuple[str, str]] = {}
        for (cloud, tok), hosts in token_hosts.items():
            if tok in GENERIC or len(tok) < 5:
                continue
            env_name = env_norm.get(norm(tok)) or env_norm.get(tok.lower())
            if not env_name:
                continue
            if env_name not in best or len(hosts) > len(token_hosts[best[env_name]]):
                best[env_name] = (cloud, tok)

        redis = get_redis_client()
        written = 0
        seeded_clouds: set[str] = set()
        for env_name, (cloud, tok) in best.items():
            insts = token_insts[(cloud, tok)]
            hosts = [i.host for i in insts]
            counts = Counter(hosts)
            ordered = [h for h, _ in counts.most_common()]
            vms, total_vcpus, total_ram_mb = [], 0, 0
            for i in insts:
                vcpus, ram_mb = _flavor_specs(i.flavor_name)
                total_vcpus += vcpus or 0
                total_ram_mb += ram_mb or 0
                vms.append({
                    "name": i.name, "host": i.host, "juju_unit": i.name,
                    "status": i.status, "flavor": i.flavor_name,
                    "availability_zone": i.availability_zone,
                    "vcpus": vcpus, "ram_mb": ram_mb, "architecture": None,
                })
            payload = {
                "environment_name": env_name,
                "primary_host": ordered[0] if ordered else None,
                "secondary_host": ordered[1] if len(ordered) > 1 else None,
                "hosts": sorted(counts), "vm_count": len(hosts), "vms": vms,
                "total_vcpus": total_vcpus, "total_ram_mb": total_ram_mb,
                "cloud": cloud, "source": "juju-fixture-seed",
            }
            redis.setex(f"env:{env_name}:placement", ttl, json.dumps(payload))
            seeded_clouds.add(cloud)
            written += 1

        for cloud in sorted(seeded_clouds):
            redis.setex(f"cloud:{cloud}:placement_available", ttl, "true")

        self.stdout.write(self.style.SUCCESS(
            f"seeded {written} placement keys across {sorted(seeded_clouds)} "
            f"from {clouds_seen} (ttl={ttl}s)"))
        return str(written)
