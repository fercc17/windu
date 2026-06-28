"""
Derive denormalized GitOps fields on Environment from gitops_model_management.

The authoritative GitOps signal already lives in ``Environment.gitops_model_management``,
parsed from is-infrastructure by the parser. This command flattens that JSON into
indexed columns the list/filter/dashboard can use cheaply, and — for environments
whose model lives in the on-disk ``is-terraform-models`` repo — resolves the model's
Terraform ``source = "...//<path>?ref=..."`` references into the set of
``is-terraform-modules`` subpaths the env consumes.

Mapping chain::

    Environment (infra-services yaml)
       └─ gitops_model_management.{repository_url, path, enabled, suspend}
          └─▶ terraform model  (is-terraform-models/models/<name>/...)
                 └─ module "x" { source = "...is-terraform-modules//<subpath>?ref=..." }
                    └─▶ terraform modules (is-terraform-modules/<subpath>)

Run::

    python manage.py refresh_gitops
    python manage.py refresh_gitops --dry-run
    python manage.py refresh_gitops --models-source is-terraform-models --modules-source is-terraform-modules
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand

from cmdb.apps.environments.models import Environment

# is-terraform-models and its Launchpad mirror canonical-terraform-modules are the
# same modules repo; capture the subpath after the `//` module separator.
_MODULE_SRC_RE = re.compile(
    r'(?:is-terraform-modules|canonical-terraform-modules)(?:\.git)?//([^?"&\s]+)'
)
_SOURCE_RE = re.compile(r'source\s*=\s*"([^"]+)"')


def _repo_slug(url: str | None) -> str | None:
    """Reduce a git URL to a bare repo slug, e.g. is-terraform-models."""
    if not url:
        return None
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    return slug[:-4] if slug.endswith(".git") else slug


def resolve_modules(model_dir: Path) -> list[str]:
    """Return sorted is-terraform-modules subpaths referenced by a model's *.tf files."""
    mods: set[str] = set()
    for tf in model_dir.rglob("*.tf"):
        try:
            text = tf.read_text(errors="ignore")
        except OSError:
            continue
        for src in _SOURCE_RE.findall(text):
            m = _MODULE_SRC_RE.search(src)
            if m:
                mods.add(m.group(1).strip("/"))
    return sorted(mods)


class Command(BaseCommand):
    help = "Flatten gitops_model_management into indexed fields and resolve model->modules."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--models-source", default="is-terraform-models",
            help="Path to the is-terraform-models checkout (default: is-terraform-models).",
        )
        parser.add_argument(
            "--modules-source", default="is-terraform-modules",
            help="Path to the is-terraform-modules checkout (default: is-terraform-modules).",
        )
        parser.add_argument("--dry-run", action="store_true", help="Report but write nothing.")

    def handle(self, *args: Any, **opts: Any) -> None:
        models_root = Path(opts["models_source"])
        modules_root = Path(opts["modules_source"])
        dry = opts["dry_run"]

        have_models = (models_root / "models").is_dir()
        if not have_models:
            self.stdout.write(self.style.WARNING(
                f"{models_root}/models not found — flags will be set but modules left empty."
            ))
        # Known module subpaths on disk, used to keep gitops_modules to real modules.
        known_modules: set[str] = set()
        if modules_root.is_dir():
            for tf in modules_root.rglob("*.tf"):
                known_modules.add(str(tf.parent.relative_to(modules_root)))

        managed = unmanaged = changed = with_modules = 0
        repo_tally: Counter = Counter()
        module_tally: Counter = Counter()

        for env in Environment.objects.all().iterator():
            block = env.gitops_model_management or {}
            is_managed = bool(block) and isinstance(block, dict)

            repo = path = None
            enabled = suspended = None
            modules: list[str] = []

            if is_managed:
                managed += 1
                repo = _repo_slug(block.get("repository_url"))
                path = block.get("path")
                enabled = block.get("enabled")
                suspended = block.get("suspend")
                repo_tally[repo or "unknown"] += 1

                # Resolve modules only for the on-disk is-terraform-models repo.
                if have_models and repo == "is-terraform-models" and path:
                    model_dir = models_root / path
                    if model_dir.is_dir():
                        found = resolve_modules(model_dir)
                        # Keep only modules that exist on disk when we have the catalog.
                        modules = [m for m in found if not known_modules or m in known_modules]
                        if modules:
                            with_modules += 1
                            module_tally.update(modules)
            else:
                unmanaged += 1

            new = (is_managed, repo, path, enabled, suspended, modules)
            cur = (env.gitops_managed, env.gitops_repo, env.gitops_path,
                   env.gitops_enabled, env.gitops_suspended, env.gitops_modules or [])
            if new == cur:
                continue
            changed += 1
            if not dry:
                env.gitops_managed = is_managed
                env.gitops_repo = repo
                env.gitops_path = path
                env.gitops_enabled = enabled
                env.gitops_suspended = suspended
                env.gitops_modules = modules
                env.save(update_fields=[
                    "gitops_managed", "gitops_repo", "gitops_path",
                    "gitops_enabled", "gitops_suspended", "gitops_modules",
                ])

        self.stdout.write(f"top repos: {repo_tally.most_common(8)}")
        self.stdout.write(f"top modules: {module_tally.most_common(8)}")
        msg = (
            f"refresh_gitops done: managed={managed} unmanaged={unmanaged} "
            f"changed={changed} envs_with_modules={with_modules}"
            + (" [DRY RUN]" if dry else "")
        )
        self.stdout.write(self.style.SUCCESS(msg))
