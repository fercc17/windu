"""
Resolve the charms an env runs, from sources derivable in the checked-out repos.

Charm sources, all merged into charm_versions tagged ``service-deployed``
(charms parsed straight from a model's terraform keep their real version and are
preserved). The command reconciles on re-run.

1. service_class template -> is-terraform-modules modules -> charms
   (deterministic; jenkins-k8s, k8s, ...). #124
2. subordinates: include_<x> = true in the template, or include_<x>: true in the
   env YAML (and removed by : false). #125  telegraf/grafana-agent/...
3. module references in a checked-out model: a model that calls
   ``source = ".../juju/applications/<mod>"`` runs that module's charms. #128
   (temporal etc. once their model repos are checked out; jimm/lego/vault now).
4. COS: a cluster whose k8s_models contains a 'cos' model runs the cos module's
   charms (alertmanager-k8s, grafana-k8s, ...). #126  Mostly PS7.

Module charm names are resolved from literals, ``var.<x>_charm.name`` (the
variable's default name), and ``local.*_charm_name``. Count-guarded
(conditional) charms are skipped.

Run::  python manage.py refresh_service_charms [--dry-run]
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Set

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from cmdb.apps.environments.models import Environment

_CHARM_BLOCK = re.compile(r'charm\s*\{(.*?)\}', re.DOTALL)
_NAME_LIT = re.compile(r'name\s*=\s*"([^"]+)"')
# variable default charm name: `name = "x"` or `name = optional(string, "x")`
_VAR_DEFAULT_NAME = re.compile(r'name\s*=\s*(?:optional\([^,]+,\s*)?"([^"]+)"')
_NAME_VAR = re.compile(r'name\s*=\s*var\.([a-z0-9_]+)(?:\.name)?')
_NAME_LOCAL = re.compile(r'name\s*=\s*local\.([a-z0-9_]+)')
_VAR_BLOCK = re.compile(r'variable\s+"([a-z0-9_]+)"\s*\{(.*?)\n\}', re.DOTALL)
_LOCAL_CHARM_NAME = re.compile(r'([a-z0-9_]+_charm_name)\s*=\s*"([^"]+)"')
_MODULE_REF = re.compile(r'terraform_modules\.([a-z0-9_]+)\.')
_SOURCE_REF = re.compile(r'source\s*=\s*"([^"]+)"')
_RESOURCE = re.compile(r'resource\s+"juju_application"')
_CONDITIONAL_COUNT = re.compile(r'count\s*=\s*var\.[a-z0-9_]+\s*\?[^\n]*:\s*0')
_TPL_INCLUDE = re.compile(r'(?:subordinates_)?include_([a-z0-9_]+)\s*=\s*true')
_YAML_INCLUDE = re.compile(r'^\s*include_([a-z0-9_]+)\s*:\s*(true|false)\s*$', re.M)
_YAML_NAME = re.compile(r'^\s*name\s*:\s*"?([A-Za-z0-9._-]+)"?\s*$', re.M)

SERVICE_TAG = "service-deployed"


def _is_conditional(text: str, charm_start: int) -> bool:
    res = list(_RESOURCE.finditer(text, 0, charm_start))
    return bool(res and _CONDITIONAL_COUNT.search(text[res[-1].start():charm_start]))


def module_charm_catalog(modules_root: Path) -> Dict[str, Set[str]]:
    """{module dir (full path and basename) -> charm names}, resolving literal,
    var.<x>_charm.name (default), and local.*_charm_name; skipping conditional."""
    by_dir: Dict[Path, list] = defaultdict(list)
    for tf in modules_root.rglob('*.tf'):
        by_dir[tf.relative_to(modules_root).parent].append(tf)

    catalog: Dict[str, Set[str]] = defaultdict(set)
    for d, files in by_dir.items():
        texts = {f: f.read_text(errors='ignore') for f in files}
        alltext = "\n".join(texts.values())
        # variable default name: first quoted name after `variable "X" {`
        # (window-based — robust to nested braces in object/default).
        var_name: Dict[str, str] = {}
        for vm in re.finditer(r'variable\s+"([a-z0-9_]+)"\s*\{', alltext):
            window = alltext[vm.end():vm.end() + 600]
            nxt = window.find('\nvariable ')
            if nxt != -1:
                window = window[:nxt]
            nm = _VAR_DEFAULT_NAME.search(window)
            if nm:
                var_name[vm.group(1)] = nm.group(1)
        locals_name = dict(_LOCAL_CHARM_NAME.findall(alltext))
        for text in texts.values():
            for cb in _CHARM_BLOCK.finditer(text):
                if _is_conditional(text, cb.start()):
                    continue
                block = cb.group(1)
                charm = None
                lit = _NAME_LIT.search(block)
                var = _NAME_VAR.search(block)
                loc = _NAME_LOCAL.search(block)
                if lit and '${' not in lit.group(1):
                    charm = lit.group(1)
                elif var:
                    charm = var_name.get(var.group(1))
                elif loc:
                    charm = locals_name.get(loc.group(1))
                if charm:
                    catalog[str(d)].add(charm)
                    catalog[d.name].add(charm)
    return catalog


def subordinate_var_charm(modules_root: Path) -> Dict[str, str]:
    main = modules_root / 'subordinates' / 'main.tf'
    if not main.exists():
        return {}
    text = main.read_text(errors='ignore')
    locals_map = dict(_LOCAL_CHARM_NAME.findall(text))
    out: Dict[str, str] = {}
    for res in re.finditer(r'resource\s+"juju_application"\s+"[^"]+"\s*\{(.*?)\n\}', text, re.DOTALL):
        body = res.group(1)
        cm = re.search(r'count\s*=\s*var\.(include_[a-z0-9_]+)', body)
        cb = _CHARM_BLOCK.search(body)
        if not cm or not cb:
            continue
        lit = _NAME_LIT.search(cb.group(1))
        loc = _NAME_LOCAL.search(cb.group(1))
        charm = lit.group(1) if lit else (locals_map.get(loc.group(1)) if loc else None)
        if charm:
            out[cm.group(1)] = charm
    return out


def _norm_source(src: str) -> str:
    """Normalise a module source ref to an is-terraform-modules subpath."""
    s = src.split('?')[0].strip().rstrip('/')
    if 'is-terraform-modules/' in s:
        s = s.split('is-terraform-modules/', 1)[1]
    return s.lstrip('./')


class Command(BaseCommand):
    help = "Resolve service-class, subordinate, module-reference and COS charms into charm_versions."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--infra-source", default="infrastructure-services")
        parser.add_argument("--modules-source", default="is-terraform-modules")
        parser.add_argument("--models-source", default="is-terraform-models")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **opts: Any) -> None:
        base = Path(settings.BASE_DIR)
        def _resolve(p):
            p = Path(p)
            return p if p.is_absolute() else base / p
        infra, mods, models = map(_resolve, (opts["infra_source"], opts["modules_source"], opts["models_source"]))
        if not (infra / 'scripts').is_dir():
            raise CommandError(f"{infra} is not the infrastructure-services checkout")
        if not mods.is_dir():
            raise CommandError(f"{mods} is not the is-terraform-modules checkout")

        catalog = module_charm_catalog(mods)
        subs = subordinate_var_charm(mods)
        cos_charms = catalog.get('cos', set())

        # service_class -> principal charms + literal-true template subordinates
        tpl_dir = infra / 'scripts' / 'infrastructure' / 'templates' / 'stable' / 'terraform'
        principal: Dict[str, Set[str]] = {}
        tpl_subs: Dict[str, Set[str]] = defaultdict(set)
        for tpl in tpl_dir.glob('*.tf.j2'):
            sc = tpl.stem[:-3] if tpl.stem.endswith('.tf') else tpl.stem
            if sc.split('_')[0] in ('backend', 'providers', 'outputs'):
                continue
            text = tpl.read_text(errors='ignore')
            charms = set().union(*(catalog.get(k, set()) for k in set(_MODULE_REF.findall(text))) or [set()])
            if charms:
                principal[sc] = charms
            lines = text.splitlines()
            for i, line in enumerate(lines):
                m = _TPL_INCLUDE.search(line)
                prev = lines[i - 1] if i else ''
                if m and not ('{%' in prev and 'if' in prev):
                    charm = subs.get(f"include_{m.group(1)}")
                    if charm:
                        tpl_subs[sc].add(charm)

        # per-env YAML subordinate overrides
        yaml_over: Dict[str, Dict[str, bool]] = {}
        for yml in (infra / 'services' / 'definitions').rglob('*.yaml'):
            text = yml.read_text(errors='ignore')
            nm = _YAML_NAME.search(text)
            if not nm:
                continue
            ov = {subs[f"include_{v}"]: (val == 'true')
                  for v, val in _YAML_INCLUDE.findall(text) if f"include_{v}" in subs}
            if ov:
                yaml_over[nm.group(1)] = ov

        # module references per on-disk model dir. Exact name match only — the
        # gitops_path fallback matched unrelated dirs and over-attributed.
        def model_dir(env):
            cand = models / 'models' / env.name
            return cand if cand.is_dir() else None

        self.stdout.write(
            f"catalog modules: {len(catalog)} keys | cos charms: {len(cos_charms)} | "
            f"service_classes: {len(principal)}"
        )

        changed = 0
        added: Counter = Counter()
        for env in Environment.objects.all():
            sc = env.service_class or ""
            target: Set[str] = set(principal.get(sc, set())) | set(tpl_subs.get(sc, set()))
            for charm, enabled in yaml_over.get(env.name, {}).items():
                target.add(charm) if enabled else target.discard(charm)
            # COS: cluster hosting a cos model
            kmodels = [(m.get('name') if isinstance(m, dict) else str(m)) or '' for m in (env.k8s_models or [])]
            if any(n.lower() == 'cos' or n.lower().endswith('-cos') for n in kmodels):
                target |= cos_charms
            # module references in the env's checked-out model
            md = model_dir(env)
            if md:
                for tf in md.rglob('*.tf'):
                    for src in _SOURCE_REF.findall(tf.read_text(errors='ignore')):
                        target |= catalog.get(_norm_source(src), set())

            current = dict(env.charm_versions or {})
            rebuilt = {c: v for c, v in current.items() if v != SERVICE_TAG}
            for c in target:
                rebuilt.setdefault(c, SERVICE_TAG)
            if rebuilt == current:
                continue
            added.update(set(rebuilt) - set(current))
            if not opts["dry_run"]:
                env.charm_versions = rebuilt
                env.save(update_fields=["charm_versions"])
            changed += 1

        verb = "would update" if opts["dry_run"] else "updated"
        self.stdout.write(f"refresh_service_charms: {verb} {changed} envs")
        if added:
            self.stdout.write(f"charms added: {added.most_common(25)}")
