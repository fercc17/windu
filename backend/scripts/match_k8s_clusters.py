#!/usr/bin/env python3
"""
Cross-reference juju OpenStack instances (from the ``tests/fixtures/juju``
server-list fixtures) against ``Environment`` rows whose
``service_class='kubernetes_cluster'``.

The goal is to tie each declared Kubernetes cluster to the physical hosts /
availability zones its juju machines actually run on. The link is best-effort:
juju names instances ``juju-<model-uuid6>-<model-name>-<machine>`` only when the
model is descriptively named; many ps6 clusters share the bare model name
``k8s`` and are only distinguishable by their UUID prefix, so they cannot be
mapped from the instance name alone. Those cases are reported as gaps.

Run under the project's Django settings::

    DJANGO_SETTINGS_MODULE=cmdb.settings python scripts/match_k8s_clusters.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import django

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cmdb.settings")
django.setup()

from cmdb.apps.environments.models import Environment  # noqa: E402
from scripts.analyze_juju_fixtures import CLOUDS, FIXTURE_DIR, parse_table, is_real_host  # noqa: E402

# juju-<6hex>-<model>-<machine>  OR  juju-<6hex>-<machine>
JUJU_RE = re.compile(r"^juju-[0-9a-f]{6}-(?P<rest>.+)$")
TRAIL_NUM = re.compile(r"-\d+$")


# Model names too generic to identify a specific cluster from the instance
# name alone (many distinct clusters share these), so we never claim a match
# on them — only report them as ambiguous.
GENERIC = {"k8s", "kubernetes", "microk8s", "cos", "worker", "control-plane",
           "k8s-worker", "kubernetes-worker"}

CLOUD_SUFFIX = re.compile(r"-(ps\d|drs|microcloud-drs)$")


def model_token(name: str) -> str | None:
    """Best-effort extraction of the juju model name from an instance name."""
    m = JUJU_RE.match(name)
    if m:
        rest = m.group("rest")
        token = TRAIL_NUM.sub("", rest)  # strip trailing -<machine>
        return token or None
    # Non-juju named instance (e.g. ``go-cbd-worker-1``)
    return TRAIL_NUM.sub("", name) or None


def norm(s: str) -> str:
    """Normalise a name/token for comparison by dropping a cloud suffix."""
    return CLOUD_SUFFIX.sub("", s)


def main() -> None:
    # 1. Collect ACTIVE instances grouped by their model token.
    token_hosts: dict[tuple[str, str], set[str]] = defaultdict(set)
    token_azs: dict[tuple[str, str], set[str]] = defaultdict(set)
    token_count: dict[tuple[str, str], int] = defaultdict(int)
    for cloud in CLOUDS:
        path = FIXTURE_DIR / f"{cloud}.txt"
        if not path.exists():
            continue
        for inst in parse_table(path, cloud):
            if inst.status != "ACTIVE" or not is_real_host(inst.host):
                continue
            tok = model_token(inst.name)
            if not tok:
                continue
            key = (cloud, tok)
            token_hosts[key].add(inst.host)
            if inst.availability_zone:
                token_azs[key].add(inst.availability_zone)
            token_count[key] += 1

    # 2. k8s_cluster environments grouped by cloud.
    envs = list(
        Environment.objects.filter(service_class="kubernetes_cluster")
        .values_list("cloud", "name")
    )
    envs_by_cloud: dict[str, list[str]] = defaultdict(list)
    for cloud, name in envs:
        envs_by_cloud[cloud or "?"].append(name)

    # 3. Match each env name against the model tokens for its cloud.
    #    Conservative: accept only an exact match or a cloud-suffix-normalised
    #    equality, and never on a GENERIC token. Anything looser would be a
    #    guess (the handoff forbids guessing).
    matched: list[dict] = []
    unmatched_envs: list[str] = []
    for cloud, names in envs_by_cloud.items():
        cloud_tokens = [tok for (c, tok) in token_hosts if c == cloud]
        for name in sorted(names):
            hit = None
            for tok in cloud_tokens:
                if tok in GENERIC or len(tok) < 5:
                    continue
                if tok == name or norm(tok) == norm(name):
                    hit = tok
                    break
            if hit:
                key = (cloud, hit)
                matched.append({
                    "env": name,
                    "cloud": cloud,
                    "model_token": hit,
                    "instances": token_count[key],
                    "hosts": sorted(token_hosts[key]),
                    "azs": sorted(token_azs[key]),
                })
            else:
                unmatched_envs.append(f"{cloud}/{name}")

    # 4. Inventory of every k8s/microk8s model token (helps explain the gaps).
    k8s_tokens = sorted(
        f"{c}/{t}  ->  {token_count[(c, t)]} inst, "
        f"{len(token_hosts[(c, t)])} hosts, AZs {sorted(token_azs[(c, t)])}"
        for (c, t) in token_hosts
        if re.search(r"k8s|kubernetes", t, re.I)
    )
    # Ambiguous generic tokens that exist but cannot identify a cluster.
    ambiguous = sorted(
        f"{c}/{t} ({token_count[(c, t)]} inst on {len(token_hosts[(c, t)])} hosts)"
        for (c, t) in token_hosts
        if t in GENERIC and token_count[(c, t)] > 0
    )

    summary = {
        "k8s_cluster_envs": len(envs),
        "matched_count": len(matched),
        "matched": matched,
        "unmatched_env_count": len(unmatched_envs),
        "unmatched_envs": unmatched_envs,
        "ambiguous_generic_tokens": ambiguous,
        "k8s_token_inventory": k8s_tokens,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
