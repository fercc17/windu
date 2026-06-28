"""
Import charm_versions onto Environment from the is-infrastructure terraform.

Reuses the parser's (fixed) ``extract_charm_versions`` so charm data stays in
sync with what terraform declares, WITHOUT running a full parser upsert (which
would also rewrite other fields and decommission envs absent from the parse).

Run::

    python manage.py import_charms --source infrastructure-services
    python manage.py import_charms --source ../is-infrastructure --dry-run
"""
from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from cmdb.apps.environments.models import Environment


def _load_parser():
    """Import parser/parser.py from the repo root as a module."""
    path = Path(settings.BASE_DIR) / "parser" / "parser.py"
    if not path.exists():
        raise CommandError(f"parser not found at {path}")
    spec = importlib.util.spec_from_file_location("cmdb_parser", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Command(BaseCommand):
    help = "Import charm_versions from is-infrastructure terraform (charm-only, no full upsert)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--source", default="infrastructure-services",
            help="Path to the is-infrastructure repo (default: infrastructure-services).",
        )
        parser.add_argument("--dry-run", action="store_true", help="Report but write nothing.")

    def handle(self, *args: Any, **opts: Any) -> None:
        source = Path(opts["source"])
        if not (source / "services").exists():
            raise CommandError(f"{source}/services not found — is --source the infra repo?")

        parser_mod = _load_parser()
        extract = parser_mod.extract_charm_versions

        updated = unchanged = cleared = 0
        charm_tally: Counter = Counter()

        for env in Environment.objects.exclude(service_primitive__isnull=True).exclude(
            service_primitive=""
        ):
            charms = extract(env.name, env.service_primitive, source) or {}
            charm_tally.update(charms.keys())
            current = env.charm_versions or {}
            if charms == current:
                unchanged += 1
                continue
            if not charms and current:
                # terraform now declares no charms — clear stale entries
                cleared += 1
            if not opts["dry_run"]:
                env.charm_versions = charms
                env.save(update_fields=["charm_versions"])
            updated += 1

        with_charms = sum(1 for v in charm_tally.values() if v)
        self.stdout.write(
            f"distinct charms seen: {len(charm_tally)} | top: {charm_tally.most_common(8)}"
        )
        msg = (
            f"import_charms done: changed={updated} unchanged={unchanged} "
            f"cleared={cleared}" + (" [DRY RUN]" if opts["dry_run"] else "")
        )
        self.stdout.write(self.style.SUCCESS(msg))
