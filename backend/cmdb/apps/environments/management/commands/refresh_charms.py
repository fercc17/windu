"""
Refresh charm_versions from the on-disk is-terraform-models checkout.

The parser / import_charms only read a single main.tf at the old
is-infrastructure path, so they miss charm{} blocks declared in the
is-terraform-models layout where a model is split across
``models/<name>/{core,iam,...}/*.tf`` (issue #123 — e.g. lego in
k8s-jaas-idp-ps6/core/main.tf).

This command scans each env's model directory **recursively** for charm{}
blocks and merges them into charm_versions (additive — it never clears existing
entries, so charms resolved from other sources are preserved).

Run::

    python manage.py refresh_charms                 # uses ./is-terraform-models
    python manage.py refresh_charms --source PATH --dry-run
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from cmdb.apps.environments.models import Environment

_CHARM_BLOCK = re.compile(r'charm\s*\{(.*?)\}', re.DOTALL)
_NAME = re.compile(r'name\s*=\s*"([^"]+)"')
_CHANNEL = re.compile(r'channel\s*=\s*"([^"]+)"')
_REVISION = re.compile(r'revision\s*=\s*(\d+)')


def extract_from_model_dir(model_dir: Path) -> Dict[str, str]:
    """Recursively parse every charm{} block under a model directory."""
    charms: Dict[str, str] = {}
    for tf in sorted(model_dir.rglob('*.tf')):
        try:
            text = tf.read_text(errors='ignore')
        except OSError:
            continue
        for m in _CHARM_BLOCK.finditer(text):
            block = m.group(1)
            name = _NAME.search(block)
            if not name:
                continue
            channel = _CHANNEL.search(block)
            revision = _REVISION.search(block)
            if channel and revision:
                val = f"{channel.group(1)} (rev {revision.group(1)})"
            elif channel:
                val = channel.group(1)
            elif revision:
                val = f"rev {revision.group(1)}"
            else:
                val = "unknown"
            charms[name.group(1)] = val
    return charms


def resolve_model_dir(env: Environment, root: Path) -> Optional[Path]:
    """Locate an env's model directory in the is-terraform-models checkout.

    Prefer the exact ``models/<name>`` match (unambiguous); fall back to
    gitops_path when the model is declared to live in is-terraform-models.
    """
    by_name = root / 'models' / env.name
    if by_name.is_dir():
        return by_name
    if (env.gitops_path
            and (env.gitops_repo in (None, '', 'is-terraform-models'))):
        by_path = root / env.gitops_path
        if by_path.is_dir():
            return by_path
    return None


class Command(BaseCommand):
    help = "Merge charm_versions from a recursive scan of the is-terraform-models checkout."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--source", default="is-terraform-models",
            help="Path to the is-terraform-models checkout (default: ./is-terraform-models).",
        )
        parser.add_argument("--dry-run", action="store_true", help="Report but write nothing.")

    def handle(self, *args: Any, **opts: Any) -> None:
        root = Path(opts["source"])
        if not root.is_absolute():
            root = Path(settings.BASE_DIR) / root
        if not (root / "models").is_dir():
            raise CommandError(f"{root}/models not found — is --source the is-terraform-models checkout?")

        changed = scanned = 0
        added_charms: Counter = Counter()

        for env in Environment.objects.all():
            model_dir = resolve_model_dir(env, root)
            if not model_dir:
                continue
            scanned += 1
            charms = extract_from_model_dir(model_dir)
            if not charms:
                continue
            current = env.charm_versions or {}
            merged = {**current, **charms}
            if merged == current:
                continue
            added_charms.update(set(charms) - set(current))
            if not opts["dry_run"]:
                env.charm_versions = merged
                env.save(update_fields=["charm_versions"])
            changed += 1

        verb = "would change" if opts["dry_run"] else "changed"
        self.stdout.write(
            f"refresh_charms: scanned {scanned} model dirs, {verb} {changed} envs"
        )
        if added_charms:
            self.stdout.write(f"newly-added charms: {added_charms.most_common(15)}")
