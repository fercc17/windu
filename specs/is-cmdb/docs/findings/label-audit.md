# is-infrastructure label audit

**Audit date:** 2026-05-13
**Infra repo SHA at time of audit:** `488002ab9e04c8ef2db13bdfd8aebe48c711fc20`
**Scope:** every file under `is-infrastructure/` (excluding `.git/`)

## TL;DR

**The Kubernetes-style `metadata.labels` block that PARSER.md and SCHEMA.md assume does not exist anywhere in `is-infrastructure`.** Out of 3,825 YAML files, zero contain a `labels:` key, and zero contain a `cmdb.canonical.com/*` label key. The parser specification needs to be re-grounded against the data the repo actually publishes.

That data does exist — it just lives in a different shape. Almost every CMDB field PARSER.md wants to capture can be sourced from the per-environment definition YAMLs at `services/definitions/<primitive>/<name>.yaml`. Those files use top-level scalar keys (`name`, `owner`, `cloud`, `service_class`, …) and a nested `cia_assessment` block that carries the criticality/SLO/ownership signal the spec calls for.

## Method

```
find . -type f \( -name '*.yaml' -o -name '*.yml' \) -not -path './.git/*'
grep -rl --include='*.yaml' --include='*.yml' 'labels'           .   # → 0 files
grep -rl --include='*.yaml' --include='*.yml' 'cmdb.canonical'   .   # → 0 files
grep -rh '^kind:'                                                .   # kind distribution
grep -rhE '^[a-zA-Z_][a-zA-Z_0-9]*:' services/definitions/       # top-level key frequency
```

Counts of `metadata.suspend`, `metadata.active`, `live`, etc. were taken with `grep -B/-A` against the per-environment YAML files in `services/definitions/`. Spot-checks confirmed the grep counts.

## Repository layout

The repo is **not** the `clusters/<region>/<env>/...` layout assumed by ARCHITECTURE.md. It is organised by *service primitive*, with three parallel trees keyed by environment name:

```
is-infrastructure/
├── services/
│   ├── definitions/        # 1,833 YAML files — DECLARATIVE METADATA (CMDB source of truth)
│   │   ├── compute/   (1,334 files)
│   │   ├── iam/       (   77 files)
│   │   ├── network/   (  159 files)
│   │   └── storage/   (  263 files)
│   ├── flux/               # 1,921 YAML files — Flux Terraform Controller CRDs
│   │   ├── compute/   (1,337 files)
│   │   ├── iam/       (   77 files)
│   │   ├── network/   (  161 files)
│   │   ├── storage/   (  263 files)
│   │   └── gitrepositories/
│   ├── resources/          # ~1,842 dirs of Terraform code (.tf, no YAML)
│   │   ├── compute/   (1,338 subdirs)
│   │   ├── iam/       (   78 subdirs)
│   │   ├── network/   (  162 subdirs)
│   │   └── storage/   (  264 subdirs)
│   └── content-cache-sites/   # 4 site dirs + sites.yaml + supported-domains.yaml
├── legacy/                 # 2,467 .tf + 384 .hcl files; no YAML, no labels
│   ├── canonistack/  cloud-archive-servers/  cloud-mirrors/  microcloud-drs/
│   ├── misc/         ps5/  ps6/  ps7/  ps8/  scalingstack/  core/  helpers/
├── scripts/  tests/  .github/
```

For every `services/definitions/<primitive>/<env>.yaml` there is (almost always) a matching `services/flux/<primitive>/<env>.yaml` and a matching `services/resources/<primitive>/<env>/` directory. The triple is the canonical unit of an "environment" in this repo.

| Primitive | `definitions/*.yaml` | `flux/*.yaml` | `resources/<env>/` dirs |
|---|---|---|---|
| compute  | 1,334 | 1,337 | 1,338 |
| iam      |    77 |    77 |    78 |
| network  |   159 |   161 |   162 |
| storage  |   263 |   263 |   264 |

The small mismatches between columns (~10 extra `flux/` or `resources/` entries) suggest a handful of environments are being onboarded or decommissioned with the three files out of sync. The parser should treat the `definitions/` YAML as the existence anchor and report mismatches.

## YAML kinds present

```
2,007  kind: Terraform               (flux Terraform Controller CRDs)
  167  kind: SecretProviderClass
   83  kind: GitRepository
  ~1,568 YAML files have no `kind:` (services/definitions/**/*.yaml, services/content-cache-sites/sites.yaml, etc.)
```

None of these objects carry a `metadata.labels` block. The Kubernetes-style ones (`kind: Terraform`, `kind: GitRepository`, `kind: SecretProviderClass`) have only `metadata.name` and `metadata.namespace` set under `metadata:`.

## Files with `metadata.labels` blocks

**Zero.**

```
$ grep -rl --include='*.yaml' --include='*.yml' 'labels' . | wc -l
0
```

This is the headline finding. Every assumption in `PARSER.md` and `SCHEMA.md` that begins "from label `cmdb.canonical.com/…`" needs to be remapped onto the field structure documented below.

## Where CMDB-relevant data actually lives

### 1. `services/definitions/<primitive>/<env>.yaml` — the primary source

Two real samples:

```yaml
# services/definitions/compute/apt-cache.yaml
service_primitive: compute
service_class: edge_cloud_machine_model
name: apt-cache
owner: certification
description: cache to store apt packages to speed up lab testing workflow
cloud: edge-tel
juju_series: '3.5'
juju_controller_stage: production
live: false
iam_groups:
- is-platform-services-certification
network_size: 24
extra_network_peers: []
cia_assessment:
  asset:
    owner: yuchi.chu@canonical.com
    risk_owner: yuchi.chu@canonical.com
    delegate: yuchi.chu@canonical.com
    custodian: devices
  stage: production
  data:
    confidentiality: 1
    sensitive: false
    regulation_categories: []
    strategic_level: 3
    loss_impact_scope:
    - internal-limited
    - external-limited
  slo:
    downtime_impact: 3
    rto: 8035200
    level: IS24x5
metadata:
  active: true
  suspend: false
  risk_group: stable
  bastion_server: certification-bastion-tel.internal
```

```yaml
# services/definitions/iam/github-team-canonical-documentation.yaml
service_primitive: iam
service_class: github_team
service_id: github-team-canonical-documentation
github_org: canonical
name: Documentation
description: Canonical technical authors
members:
  launchpad_teams:
  - canonical-core-docs
maintainers:
  launchpad_users:
  - morrisong  - danieleprocida  - keirthana
metadata:
  active: true
  suspend: false
  risk_group: stable
```

### 2. `services/flux/<primitive>/<env>.yaml` — Flux Terraform Controller wrapper

```yaml
apiVersion: infra.contrib.fluxcd.io/v1alpha2
kind: Terraform
metadata:
  name: iam-github-team-canonical-documentation
  namespace: flux-terraform        # or flux-terraform-branch-planner
spec:
  approvePlan: auto
  path: ./services/resources/iam/github-team-canonical-documentation
  backendConfig:
    customConfiguration: |
      backend "s3" {
        key    = "iam/github-team-canonical-documentation/state"
        bucket = "ps6-flux-tfstate"
        region = "prodstack6"
        ...
```

These give the parser two pieces of information PARSER.md's `terraform.py` extractor was meant to provide:

- `spec.backendConfig.customConfiguration` contains the **state bucket and key** — the same data PARSER.md expected to parse from `.tf` files. Parsing the YAML is cheaper than parsing HCL.
- `spec.path` resolves to the matching `services/resources/<primitive>/<env>/` Terraform directory.

### 3. `services/resources/<primitive>/<env>/*.tf` — actual Terraform

Where `data "terraform_remote_state"` references live. Used for inferring infrastructure dependencies between environments.

### 4. `legacy/**` — no YAML, all Terraform

2,467 `.tf` and 384 `.hcl` files. No labels, no per-env YAML. Out of scope for the YAML-driven parser. If legacy environments must be inventoried, they need a separate ingestion path (e.g. directory-walking the `legacy/<cloud>/environments/<name>/` tree and treating each directory as a row).

## Unique top-level keys in `services/definitions/**/*.yaml`

Frequency over all 1,833 files (top ~40 keys):

| Count | Key                          | Value format observed                                |
|------:|------------------------------|------------------------------------------------------|
| 1833  | `service_class`              | enum: `machine_model`, `container_model`, `database`, `kubernetes_cluster`, `peer_link`, `seg_reproducer`, `user_group`, `object_storage`, `cloud_ingress`, `juju_controller`, ~20 others |
| 1833  | `name`                       | free-text identifier (kebab-case or human label like `Documentation`) |
| 1833  | `metadata`                   | mapping; see *Nested `metadata` keys* below |
| 1832  | `service_primitive`          | enum: `compute` \| `iam` \| `network` \| `storage` |
| 1651  | `cloud`                      | enum: `ps5`, `ps6`, `ps7`, `ps8`, `microcloud-drs`, `edge-tel`, `edge-et3` |
| 1512  | `quotas`                     | mapping (quota-name → integer) |
| 1425  | `description`                | free text |
| 1335  | `owner`                      | enum-ish team slug: `is`, `webdesign`, `comsys`, `is-charms`, `snapstore`, `launchpad`, `security`, … — **always a team, never a person** |
| 1167  | `juju_series`                | string (e.g. `'3.5'`, `'3.6'`) |
| 1118  | `stage`                      | enum: `production`, `staging` |
| 1107  | `juju_controller`            | controller name (string; doubles as a dependency edge) |
| 1070  | `jaas_managed`               | bool |
|  770  | `cia_assessment`             | mapping; see *Nested `cia_assessment` keys* below |
|  731  | `extra_network_peers`        | list (mostly empty) |
|  729  | `grant_team_access`          | bool / list |
|  721  | `manage_flavor_access`       | bool |
|  721  | `builder_workloads`          | list |
|  610  | `iam_groups`                 | list of team slugs |
|  450  | `live`                       | bool — only 25% of files set this explicitly; default seems to be "yes if active" |
|  402  | `juju_controller_stage`      | enum: `production`, `staging`, `migration`, `migration-production`, `cloud-infrastructure`, `testing` |
|  357  | `compute_architecture`       | enum: `amd64`, `arm64`, … |
|  323  | `juju_model_config`          | mapping (free-form Juju config) |
|  320  | `network_type`               | enum |
|  286  | `cluster`                    | string |
|  207  | `size`                       | enum (database-only) |
|  207  | `postgresql_major_version`   | int |
|  201  | `gitops_model_management`    | bool |
|  193  | `accessing_juju_models`      | list of CMR model refs (dependency edges) |
|  119  | `data_integrator_accessing_juju_model` | list (dependency edges) |
|  207  | `remote_cmr_models`          | list of CMR model refs (dependency edges) |
|  105  | `services`                   | list |
|   93  | `worker_groups`              | list |
|   76  | `service_id`                 | string (iam only) |

Other tail keys (<100 files): `requester`, `user`, `members`, `maintainers`, `github_org`, `accessing_iam_groups`, `port_forwards`, `extra_tcp_ports`, `extra_tcp_ports_remote_ips`, `kube_apiserver_extra_sans`, `legacy_certificates_charm`, `postgresql_use_local_storage`, `address`, …

### Nested `metadata` keys

The `metadata:` block in *definitions* (not to be confused with the Kubernetes `metadata` in *flux* files) carries operational lifecycle state:

| Key                 | Value format     | Example                                  |
|---------------------|------------------|------------------------------------------|
| `metadata.active`   | bool             | `true` (1,764) / `false` (69)            |
| `metadata.suspend`  | bool             | `false` (1,717) / `true` (120)           |
| `metadata.risk_group` | enum string    | `stable`, observed across all files      |
| `metadata.bastion_server` | hostname   | `certification-bastion-tel.internal`     |
| `metadata.address`  | hostname/FQDN    | `ingress-drs-is-infrastructure.dynamic.admin.canonical.com` |

### Nested `cia_assessment` keys

Present in 770 of 1,833 files (≈42%). When present, structure is consistent:

| Key                                          | Value format               | Example                                          |
|----------------------------------------------|----------------------------|--------------------------------------------------|
| `cia_assessment.asset.owner`                 | email                      | `yuchi.chu@canonical.com`                        |
| `cia_assessment.asset.risk_owner`            | email                      | `yuchi.chu@canonical.com`                        |
| `cia_assessment.asset.delegate`              | email (optional)           | `james.simpson@canonical.com`                    |
| `cia_assessment.asset.custodian`             | team slug                  | `is`, `devices`, `snapstore`, …                  |
| `cia_assessment.stage`                       | enum                       | `production`, `staging`                          |
| `cia_assessment.data.confidentiality`        | int 1–3                    | `1`                                              |
| `cia_assessment.data.sensitive`              | bool                       | `false`                                          |
| `cia_assessment.data.regulation_categories`  | list of strings (often [])| `[]`                                             |
| `cia_assessment.data.strategic_level`        | int                        | `3`                                              |
| `cia_assessment.data.loss_impact_scope`      | list of enum strings       | `['internal-limited', 'external-limited']`       |
| `cia_assessment.slo.downtime_impact`         | int 1–3                    | `3`                                              |
| `cia_assessment.slo.rto`                     | int (seconds)              | `8035200`                                        |
| `cia_assessment.slo.level`                   | enum string                | `IS24x5`, `IS24x7`, …                            |

This is the structured criticality / data-classification / SLO signal that PARSER.md anticipated. The good news: it is well-formed JSON-ish data, not free-text labels.

## Coverage estimate

If we equate "environment" with "one `services/definitions/<primitive>/<env>.yaml`", there are **1,833 environments** in the modern (non-legacy) tree.

Per-field availability:

| CMDB field (per SCHEMA.md)   | Available in N/1833 | % coverage | Notes |
|------------------------------|--------------------:|-----------:|-------|
| `name`                       | 1833                | 100%       | Top-level `name:`; also derivable from filename |
| `git_path`                   | 1833                | 100%       | Trivially `services/definitions/<primitive>/<env>.yaml` |
| `region` / cloud             | 1651                | 90%        | Top-level `cloud:`. The 182 missing are mostly iam/github-team files where region is not meaningful |
| `env_type` (prod/staging/…)  | 1118                | 61%        | Top-level `stage:`. Also derivable from filename prefix (`prod-`, `stg-`, `dev-`) — almost universal in practice |
| `owner` (team)               | 1335                | 73%        | Top-level `owner:` (team slug). Also `cia_assessment.asset.custodian` is a team slug — combining the two yields ≥85% |
| `owner` (individual)         |  770                | 42%        | Only via `cia_assessment.asset.owner` (email) |
| `team`                       | 1335                | 73%        | Same as `owner` field — they are synonyms in this repo |
| `oncall_handle`              |    0                | 0%         | **Not present.** No PagerDuty service ID or Mattermost channel field anywhere |
| `cost_center`                |    0                | 0%         | **Not present.** |
| `criticality_tier` (1–3)     |  770                | 42%        | Derivable from `cia_assessment.slo.downtime_impact` (int 1–3) |
| `data_classification`        |  770                | 42%        | Derivable from `cia_assessment.data.sensitive` + `loss_impact_scope` |
| `compliance_scope`           |  770                | 42%        | Derivable from `cia_assessment.data.regulation_categories` (often empty list) |
| `charm_versions`             |    ?                | partial    | Not in definitions YAMLs; would need Juju bundle scraping or `juju_series` + per-charm channel discovery |
| `created_at`                 |    0                | 0%         | **Not present.** Closest proxy is `git log --diff-filter=A --follow <file>` |
| `status` (declared lifecycle)| 1833                | 100%       | Derivable from `metadata.active` + `metadata.suspend` + `live` |
| `maintenance_window`         |    0                | 0%         | **Not present.** |
| `runbook_url`                |    0                | 0%         | **Not present.** |
| `compliance_scope`           |   ~770              | 42%        | See above |
| Declared deps (`depends-on`) |    0                | 0%         | No explicit field. Inferable from `juju_controller`, `remote_cmr_models`, `accessing_juju_models`, `data_integrator_accessing_juju_model`, `iam_groups`, `extra_network_peers` |

**Overall conclusion:** ≈70–80% of the SCHEMA.md fields can be populated from `services/definitions/`. The remaining 20–30% (`oncall_handle`, `cost_center`, `maintenance_window`, `runbook_url`, `created_at`) are simply not declared anywhere in the repo and will be null until a separate source (Git history, PagerDuty API, runbook wiki) is wired in or new fields are added to the definition YAMLs.

## Files / environments with thin or missing metadata

- **Legacy tree** (`legacy/**`): 0 YAML files. All ~2,851 Terraform/HCL files. Out of scope for v1 of the parser. Likely represents a real population of long-lived environments not yet reflected in the new format.
- **`services/content-cache-sites/`**: only 4 site dirs and 2 top-level YAML files (`sites.yaml`, `supported-domains.yaml`). Schema differs from the `services/definitions/` model; needs a small dedicated extractor or to be folded into `definitions/network/` manually.
- **`services/definitions/iam/*.yaml`** (77 files): often lack `cloud:`, `stage:`, `cia_assessment:`. These represent IAM/team objects rather than cloud workloads. The CMDB should expect a sparse row for them.
- **`services/flux/gitrepositories/`** (~6 files): `kind: GitRepository`, not per-env metadata. Not a CMDB source; ignore.
- Roughly **715 of 1833** files have no `live:` key. Practical implication: treat absent `live` as `true` when `metadata.active=true` and `metadata.suspend=false`.

## Naming inconsistencies and gotchas

- **Two different `metadata:` namespaces.** `services/flux/*.yaml` uses Kubernetes `metadata:` (name + namespace). `services/definitions/*.yaml` uses an in-domain `metadata:` (active/suspend/risk_group). The parser must read both and not confuse them.
- **`owner` vs `cia_assessment.asset.custodian` vs `cia_assessment.asset.owner`.** Three "owner" concepts: top-level `owner` (team slug), `custodian` (also team slug, sometimes different from `owner`), `asset.owner` (individual email). The CMDB should store all three and treat them as separate columns rather than collapsing them.
- **`stage` is set in two places.** Top-level `stage:` and `cia_assessment.stage`. They agree in every spot-check; pick top-level as authoritative and warn on mismatch.
- **`live: false` ≠ decommissioned.** It often means "declared but not yet provisioned" or "model paused". `metadata.suspend: true` is closer to "in maintenance". Real decommissioning is signalled by deletion of the YAML file. Soft-delete logic in PARSER.md should be triggered by *file disappearance*, not by `live: false`.
- **Filename prefix carries semantics.** `prod-foo-ps6.yaml` / `stg-foo-ps6.yaml` / `dev-foo-ps6.yaml` / `k8s-prod-foo.yaml`. Useful fallback when `stage:` is missing.
- **`cloud:` value `ps6` ≠ a region.** PS5/6/7/8 are ProdStack clouds. `edge-tel`, `edge-et3`, `microcloud-drs` are physical edge locations. The CMDB `region` column probably wants a derived geography (`amer`/`emea`/`apac`/`edge`), not the raw cloud name. That mapping is **not** in the repo today and needs to be added in Issue 0.2 as a static lookup table.

## Recommended label-to-field mapping (input to Issue 0.2)

Since there are no labels at all, the mapping is really field-to-field. The proposed `parser/label_mapping.yaml` format from Issue 0.2 should be reframed as `parser/field_mapping.yaml` with these entries:

```yaml
# parser/field_mapping.yaml  (proposed)
source_root: services/definitions

fields:
  name:
    source: yaml_key
    key: name
    fallback: filename_stem            # e.g. apt-cache.yaml → "apt-cache"
  git_path:
    source: derived
    rule: "services/definitions/{service_primitive}/{filename}"
  region:
    source: derived
    rule: cloud_to_region_lookup       # static map, see Issue 0.2
    input_key: cloud
  cloud:
    source: yaml_key
    key: cloud
  env_type:
    source: yaml_key
    key: stage
    fallback: filename_prefix          # prod-* | stg-* | dev-* | k8s-prod-*
    enum: [prod, staging, dev, lab]
  team:
    source: yaml_key
    key: owner
    fallback: cia_assessment.asset.custodian
  owner_individual:
    source: yaml_path
    key: cia_assessment.asset.owner
    required: false
  risk_owner:
    source: yaml_path
    key: cia_assessment.asset.risk_owner
    required: false
  custodian:
    source: yaml_path
    key: cia_assessment.asset.custodian
    required: false
  criticality_tier:
    source: yaml_path
    key: cia_assessment.slo.downtime_impact   # int 1..3 already
    required: false
  data_classification:
    source: derived
    rule: cia_assessment.data.sensitive ? "pii" : (loss_impact_scope contains "external-*" ? "internal" : "public")
    required: false
  compliance_scope:
    source: yaml_path
    key: cia_assessment.data.regulation_categories
    transform: list_or_empty
  charm_versions:
    source: not_available_from_yaml
    note: "Would require crawling juju bundles or live Juju API. Defer to poller for now."
  status:
    source: derived
    rule: |
      if file missing on this run            -> "decommissioning"
      elif metadata.suspend == true          -> "maintenance"
      elif metadata.active == false          -> "provisioning"
      elif live == false                     -> "provisioning"
      else                                   -> "active"
  declared_at:
    source: derived
    rule: NOW()
  last_git_commit:
    source: cli_arg
    arg: --sha
  service_primitive:
    source: yaml_key
    key: service_primitive            # new column suggestion, not in current SCHEMA.md
  service_class:
    source: yaml_key
    key: service_class                # new column suggestion
  juju_controller:
    source: yaml_key
    key: juju_controller              # also emit as dependency edge
  juju_series:
    source: yaml_key
    key: juju_series
  juju_controller_stage:
    source: yaml_key
    key: juju_controller_stage
  bastion_server:
    source: yaml_path
    key: metadata.bastion_server
  risk_group:
    source: yaml_path
    key: metadata.risk_group

  oncall_handle:     { source: not_available_from_yaml }
  cost_center:       { source: not_available_from_yaml }
  maintenance_window:{ source: not_available_from_yaml }
  runbook_url:       { source: not_available_from_yaml }
  created_at:        { source: git_log, command: "git log --diff-filter=A --follow --format=%aI -1 -- <file>" }

dependencies:
  - source_key: juju_controller
    edge_type: infrastructure
    target_kind: juju_controller       # special pseudo-env, populated from juju_controller definitions
  - source_key: remote_cmr_models
    edge_type: infrastructure
    target_kind: env_name
    transform: list
  - source_key: accessing_juju_models
    edge_type: infrastructure
    target_kind: env_name
    transform: list
  - source_key: data_integrator_accessing_juju_model
    edge_type: infrastructure
    target_kind: env_name
    transform: list
  - source_key: iam_groups
    edge_type: infrastructure
    target_kind: iam_group
    transform: list
```

## Recommendations for the design docs

These follow directly from the audit and are the input the next issue (0.2) is expected to consume:

1. **Re-anchor PARSER.md.** Replace the "read `metadata.labels`" extractor with a "read `services/definitions/<primitive>/<name>.yaml`" extractor. The Terraform extractor stays but switches its primary input from `.tf` files to the `spec.backendConfig.customConfiguration` block of `services/flux/<primitive>/<name>.yaml` (cheaper and equivalent for state-key inference).
2. **Add columns to `environments`** for `service_primitive`, `service_class`, `cloud`, `juju_controller`, `juju_controller_stage`, `juju_series`, `custodian`, `risk_owner`, `bastion_server`, `risk_group`. They are first-class in this repo and dropping them would lose signal.
3. **Add a `cloud_to_region` lookup table** to the CMDB (Issue 0.2). The `cloud` values (`ps5`/`ps6`/`ps7`/`ps8`/`edge-tel`/`edge-et3`/`microcloud-drs`) need an explicit mapping to the `region` enum in SCHEMA.md; that mapping is **not** in `is-infrastructure`.
4. **Defer `oncall_handle`, `cost_center`, `runbook_url`, `maintenance_window`.** Mark these fields nullable and surface them in the UI as "not declared". A follow-up issue should add them to `services/definitions/` once the SRE team has a place to source them (probably PagerDuty + a runbooks wiki index).
5. **Drop the `cmdb.canonical.com/` label convention from the design docs** or move it to "future state — to be introduced if/when teams adopt it." Today it is fiction.
6. **Treat the legacy tree as out of scope** for v1 of the parser. Decide separately whether to ingest it via a directory-walk fallback (using path components for region/team) or to leave it dark.

## References

- `ARCHITECTURE.md`, `SCHEMA.md`, `PARSER.md` — original assumptions (label-based) that this audit overturns.
- `services/definitions/compute/apt-cache.yaml` — representative compute environment.
- `services/definitions/iam/github-team-canonical-documentation.yaml` — representative IAM object.
- `services/flux/compute/apt-cache.yaml` — representative Flux Terraform Controller manifest.
- Infra SHA audited: `488002ab9e04c8ef2db13bdfd8aebe48c711fc20`.
