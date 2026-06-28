"""
Pre-compute per-environment dependency + host-aggregate summary onto the
Environment row, so the main list table needs no joins at request time:

- ``cached_depends_on``       — comma-joined names of models this env depends on
- ``cached_dependents_count`` — how many environments depend on this env
- ``host_aggregate``          — copied from the env's primary node

Run after a parse / placement-link / host-aggregate load::

    python manage.py refresh_dependency_cache
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from django.core.management.base import BaseCommand
from django.db.models import Count

from cmdb.apps.environments.models import Environment, EnvironmentDependency


class Command(BaseCommand):
    help = "Recompute cached_depends_on / cached_dependents_count / host_aggregate."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--dry-run", action="store_true", help="Report but write nothing.")

    def handle(self, *args: Any, **opts: Any) -> None:
        # depends_on: env -> [names it depends on]
        depends_on: dict[str, list[str]] = defaultdict(list)
        for edge in EnvironmentDependency.objects.values_list(
            "environment_name", "depends_on_name"
        ):
            depends_on[edge[0]].append(edge[1])

        # dependents count: depends_on_name -> count of distinct dependents
        dependents: dict[str, int] = {
            row["depends_on_name"]: row["n"]
            for row in EnvironmentDependency.objects.values("depends_on_name").annotate(
                n=Count("environment_name", distinct=True)
            )
        }

        updated = 0
        # select_related primary_node so host_aggregate read is not a per-row query
        for env in Environment.objects.select_related("primary_node").all():
            deps = sorted(set(depends_on.get(env.name, [])))
            new_depends = ", ".join(deps) if deps else None
            new_count = dependents.get(env.name, 0)
            new_agg = env.primary_node.host_aggregate if env.primary_node_id else None

            if (
                env.cached_depends_on != new_depends
                or env.cached_dependents_count != new_count
                or env.host_aggregate != new_agg
            ):
                updated += 1
                if not opts["dry_run"]:
                    env.cached_depends_on = new_depends
                    env.cached_dependents_count = new_count
                    env.host_aggregate = new_agg
                    env.save(
                        update_fields=[
                            "cached_depends_on",
                            "cached_dependents_count",
                            "host_aggregate",
                        ]
                    )

        msg = f"refresh_dependency_cache done: updated={updated}" + (
            " [DRY RUN]" if opts["dry_run"] else ""
        )
        self.stdout.write(self.style.SUCCESS(msg))
