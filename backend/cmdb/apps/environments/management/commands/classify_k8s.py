"""
Classify each Environment's Kubernetes distribution into ``k8s_distribution``.

Rules (derived from data already in the CMDB):

- **CK8s** — deploys the ``k8s`` or ``k8s-worker`` charms (the k8s-snap based
  Canonical Kubernetes). This is the authoritative signal; legacy charmed-k8s
  used ``kubernetes-control-plane``/``-worker`` (absent in this fleet).
- **ck8s-jenkins-aas** — a CK8s env that is a Jenkins service
  (``service_class='jenkins'`` or ``jenkins`` in the name). Jenkins aaS runs on
  top of CK8s, so it is modelled as a CK8s subtype.
- **legacy-k8s** — pre-CK8s clusters with no CK8s charm signature, inferred from
  live environments: ``microk8s`` in the name, or a ``k8s-openstack`` cluster.

Run::

    python manage.py classify_k8s            # apply
    python manage.py classify_k8s --dry-run  # report only
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from django.core.management.base import BaseCommand

from cmdb.apps.environments.models import Environment

CK8S_CHARMS = {"k8s", "k8s-worker"}


def classify(env: Environment) -> str | None:
    charms = set((env.charm_versions or {}).keys())
    name = (env.name or "").lower()
    is_ck8s = bool(charms & CK8S_CHARMS)

    if is_ck8s:
        if env.service_class == "jenkins" or "jenkins" in name:
            return "ck8s-jenkins-aas"
        return "ck8s"

    # Pre-CK8s clusters, inferred from naming on live-ish environments.
    if "microk8s" in name or "k8s-openstack" in name:
        return "legacy-k8s"
    return None


class Command(BaseCommand):
    help = "Classify Environment.k8s_distribution (CK8s / Jenkins-aaS / legacy-k8s)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--dry-run", action="store_true", help="Report but write nothing.")

    def handle(self, *args: Any, **opts: Any) -> None:
        tally: Counter = Counter()
        by_cloud: dict[str, Counter] = {}
        changed = 0

        for env in Environment.objects.all().only(
            "id", "name", "cloud", "service_class", "charm_versions", "k8s_distribution"
        ):
            new = classify(env)
            tally[new or "—"] += 1
            if new:
                by_cloud.setdefault(new, Counter())[env.cloud or "?"] += 1
            if new != env.k8s_distribution:
                changed += 1
                if not opts["dry_run"]:
                    env.k8s_distribution = new
                    env.save(update_fields=["k8s_distribution"])

        for kind in ("ck8s", "ck8s-jenkins-aas", "legacy-k8s"):
            self.stdout.write(f"{kind}: {tally[kind]}  by cloud: {dict(by_cloud.get(kind, {}))}")
        msg = f"classify_k8s done: changed={changed}" + (" [DRY RUN]" if opts["dry_run"] else "")
        self.stdout.write(self.style.SUCCESS(msg))
