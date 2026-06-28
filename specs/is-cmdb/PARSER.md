# Parser

## Overview

The parser runs as a GitHub Actions workflow in `is-cmdb`. It is triggered by a `repository_dispatch` event from `is-infrastructure` on every push to main. It checks out the infra repo at the triggered SHA, walks the YAML tree, and upserts rows into PostgreSQL.

It is stateless and idempotent. Running it twice on the same SHA produces the same result.

## Trigger

```yaml
# is-cmdb/.github/workflows/ingest.yml

on:
  repository_dispatch:
    types: [infra-updated]

jobs:
  parse:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout is-cmdb
        uses: actions/checkout@v4

      - name: Checkout is-infrastructure at triggered SHA
        uses: actions/checkout@v4
        with:
          repository: canonical/is-infrastructure
          ref: ${{ github.event.client_payload.sha }}
          token: ${{ secrets.INFRA_READ_TOKEN }}
          path: infra

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run parser
        env:
          DATABASE_URL: ${{ secrets.CMDB_DATABASE_URL }}
          GIT_SHA: ${{ github.event.client_payload.sha }}
        run: python parser/parser.py --source ./infra --sha $GIT_SHA
```

```yaml
# is-infrastructure/.github/workflows/notify-cmdb.yml

on:
  push:
    branches: [main]

jobs:
  dispatch:
    runs-on: ubuntu-latest
    steps:
      - uses: peter-evans/repository-dispatch@v3
        with:
          token: ${{ secrets.CMDB_DISPATCH_TOKEN }}
          repository: canonical/is-cmdb
          event-type: infra-updated
          client-payload: '{"sha": "${{ github.sha }}"}'
```

## Parser logic

### Entry point: `parser/parser.py`

```
Arguments:
  --source PATH   Path to checked-out is-infrastructure repo
  --sha    SHA    Git SHA of the triggered commit (stored as last_git_commit)

Behaviour:
  1. Walk --source recursively, find all YAML files
  2. For each YAML file, attempt to extract cmdb labels (see manifest.py)
  3. For files with cmdb labels, build an environment record
  4. For all Terraform .tf files, extract remote state references (see terraform.py)
  5. For all Juju bundle.yaml files, extract relation dependencies (see juju.py)
  6. Upsert all environment records into PostgreSQL environments table
  7. Upsert all dependency edges into environment_dependencies table
  8. For any git_path that existed in the DB but is not present in this parse run,
     set end_date = NOW() and status = 'decommissioning'
     (do not delete rows - preserve history)
  9. Log a summary: N environments upserted, M decommissioned, K dependencies written
```

### `parser/extractors/manifest.py`

```
Input:  parsed YAML dict from a single file, file path relative to repo root

Reads cmdb.canonical.com/* labels from metadata.labels.
Returns an EnvironmentRecord dataclass or None if no cmdb labels present.

EnvironmentRecord fields:
  name                  from label cmdb.canonical.com/name, or derived from git_path basename
  git_path              file path relative to repo root
  region                inferred from git_path (clusters/amer/... -> amer) or label
  owner                 from label cmdb.canonical.com/owner
  team                  from label cmdb.canonical.com/team
  oncall_handle         from label cmdb.canonical.com/oncall
  cost_center           from label cmdb.canonical.com/cost-center
  env_type              from label cmdb.canonical.com/type
  criticality_tier      from label cmdb.canonical.com/criticality-tier (int)
  data_classification   from label cmdb.canonical.com/data-class
  compliance_scope      from label cmdb.canonical.com/compliance (comma-separated -> array)
  charm_versions        from label cmdb.canonical.com/charms (JSON string) or extracted
                        from juju bundle channel/revision fields in the same file
  created_at            from label cmdb.canonical.com/created (ISO date string)
  maintenance_window    from label cmdb.canonical.com/maintenance-window (JSON string)
  runbook_url           from label cmdb.canonical.com/runbook
  declared_deps         from label cmdb.canonical.com/depends-on (comma-separated list)
```

### `parser/extractors/terraform.py`

```
Input:  path to a .tf file

Scans for backend "s3" or backend "gcs" blocks that reference a remote state key
belonging to a known environment name pattern.

Also scans for data "terraform_remote_state" blocks.

Returns list of DependencyEdge(from_env, to_env, type="infrastructure")

Pattern to match (example):
  data "terraform_remote_state" "vault" {
    config = {
      key = "shared-vault-prod/terraform.tfstate"
    }
  }
  → edge: current_env depends_on shared-vault-prod

The environment name is extracted from the key path by stripping /terraform.tfstate
and matching against the list of known environment names from the DB.
```

### `parser/extractors/juju.py`

```
Input:  path to a bundle.yaml file

Reads the relations block. Each relation is a pair of application endpoints.
Maps application names to environment names using a config file
(juju-app-to-env.yaml in the is-cmdb repo root, manually maintained).

Returns list of DependencyEdge(from_env, to_env, type="infrastructure")

Also extracts charm_versions from the applications block:
  applications:
    postgresql:
      charm: postgresql
      channel: 14/stable
→ charm_versions: {"postgresql": "14/stable"}

If a bundle.yaml is found inside a directory that has cmdb labels, merge
the charm_versions into the EnvironmentRecord for that directory.
```

### Upsert behaviour

```sql
INSERT INTO environments (name, git_path, region, owner, team, ...)
VALUES (...)
ON CONFLICT (name) DO UPDATE SET
  git_path            = EXCLUDED.git_path,
  owner               = EXCLUDED.owner,
  team                = EXCLUDED.team,
  -- all fields except: id, created_at (preserve original), end_date (managed separately)
  declared_at         = NOW(),
  last_git_commit     = EXCLUDED.last_git_commit,
  updated_at          = NOW();
```

For dependencies:
```sql
INSERT INTO environment_dependencies (environment_name, depends_on_name, dependency_type)
VALUES (...)
ON CONFLICT (environment_name, depends_on_name) DO UPDATE SET
  dependency_type = EXCLUDED.dependency_type;
-- 'declared' takes precedence over 'infrastructure' if both exist for same pair
```

## Error handling

- If a YAML file fails to parse, log the error and continue. Do not abort the entire run.
- If a dependency references an environment name not in the DB, log a warning and skip that edge. Do not create dangling foreign key references.
- If the DB is unreachable, fail the GitHub Actions job (non-zero exit). GitHub will show the failure in the Actions tab.
- All upserts run inside a single transaction. If any upsert fails, roll back and fail the job.

## Environment variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string, e.g. `postgresql://user:pass@host:5432/cmdb` |
| `GIT_SHA` | Passed from the workflow, stored as `last_git_commit` |
| `LOG_LEVEL` | Optional, defaults to INFO |
