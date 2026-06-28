# Database Schema

## PostgreSQL

### `environments` table

Primary declared-state table. One row per environment. Updated by the parser on every merge to `is-infrastructure`.

```sql
CREATE TABLE environments (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identity
    name                  TEXT NOT NULL UNIQUE,      -- e.g. amer-prod-launchpad
    git_path              TEXT NOT NULL,             -- e.g. clusters/amer/prod/launchpad
    region                TEXT NOT NULL,             -- amer | apac | emea

    -- Ownership
    owner                 TEXT NOT NULL,             -- individual, e.g. fernando.carrillo
    team                  TEXT NOT NULL,             -- e.g. amer-sre
    oncall_handle         TEXT,                      -- PagerDuty service ID or Mattermost handle
    cost_center           TEXT,

    -- Classification
    env_type              TEXT NOT NULL,             -- prod | staging | dev | lab
    criticality_tier      INT CHECK (criticality_tier BETWEEN 1 AND 3),
        -- 1 = business critical, zero tolerance for unplanned downtime
        -- 2 = important, working hours response
        -- 3 = best effort
    data_classification   TEXT,                      -- pii | internal | public
    compliance_scope      TEXT[],                    -- e.g. {soc2, iso27001}

    -- Declared state (parsed from Git)
    charm_versions        JSONB,
        -- e.g. {"postgresql": "14/stable", "vault": "1.8/edge"}
        -- parsed from Juju bundle or Terraform manifests
    declared_at           TIMESTAMPTZ,               -- timestamp of last parser run
    last_git_commit       TEXT,                      -- SHA that triggered the last parse

    -- Lifecycle
    status                TEXT NOT NULL DEFAULT 'provisioning',
        -- provisioning | active | degraded | maintenance | decommissioning | archived
    created_at            TIMESTAMPTZ,               -- from cmdb label or git blame
    end_date              TIMESTAMPTZ,               -- null = live; set when manifest deleted

    -- Operational metadata
    maintenance_window    JSONB,
        -- e.g. {"day": "sunday", "start": "02:00", "end": "06:00", "tz": "UTC"}
    runbook_url           TEXT,

    -- Flux / reconciliation (updated by Flux notification hook)
    last_reconciled_at    TIMESTAMPTZ,
    last_good_commit      TEXT,                      -- last SHA that reconciled cleanly
    last_good_reconcile   TIMESTAMPTZ,

    updated_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX ON environments (region, env_type, status);
CREATE INDEX ON environments (criticality_tier);
CREATE INDEX ON environments (team);
CREATE INDEX ON environments (status);
```

### `environment_dependencies` table

Explicit dependency graph. One row per directed edge. Populated automatically by the parser (from Terraform remote state refs and Juju relations) and optionally from declared labels in manifests.

```sql
CREATE TABLE environment_dependencies (
    environment_name      TEXT NOT NULL REFERENCES environments(name),
    depends_on_name       TEXT NOT NULL REFERENCES environments(name),
    dependency_type       TEXT NOT NULL,
        -- infrastructure = inferred from Terraform remote state or Juju relation
        -- declared       = explicitly set via cmdb label in manifest
    PRIMARY KEY (environment_name, depends_on_name)
);

CREATE INDEX ON environment_dependencies (depends_on_name);
-- Allows fast "what would break if I take down X" query
```

### `placement_history` table

Written by the poller once per hour (not every 5 minutes). Used for incident retrospectives. Pruned to a rolling 30-day window by a scheduled job.

```sql
CREATE TABLE placement_history (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    environment_name      TEXT NOT NULL,
    primary_node          TEXT,
    secondary_node        TEXT,
    juju_model            TEXT,
    juju_units            JSONB,   -- e.g. [{"unit": "postgresql/0", "machine": "3"}]
    source                TEXT,    -- juju-api | kubectl
    recorded_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX ON placement_history (environment_name, recorded_at DESC);
```

## Redis

### Key structure

All live placement data lives in Redis with an 8-minute TTL. The poller runs every 5 minutes, so a missing key means the poller has missed at least one cycle.

```
Key:    env:{environment_name}:placement
TTL:    480 seconds (8 minutes)
Value:  JSON string

Example key:   env:amer-prod-launchpad:placement
Example value: {
  "primary_node":   "node-3.maas.canonical.com",
  "secondary_node": "node-7.maas.canonical.com",
  "juju_model":     "amer-prod",
  "juju_units":     [
    {"unit": "postgresql/0", "machine": "3"},
    {"unit": "postgresql/1", "machine": "7"}
  ],
  "polled_at":      "2026-04-10T14:35:00Z",
  "source":         "juju-api"
}
```

### Freshness semantics

| Redis result | Meaning | UI treatment |
|---|---|---|
| Key present, TTL > 0 | Placement is current | Show node names |
| Key missing (TTL expired) | Poller missed at least one cycle, or environment gone | Show "placement unknown" badge in amber |
| Key missing + environment status = decommissioning | Expected | Show "decommissioned" badge |

## YAML label convention

Every environment manifest in `is-infrastructure` must include these labels for the parser to produce a complete CMDB record. Fields without a label are left null.

```yaml
metadata:
  labels:
    cmdb.canonical.com/owner:              "fernando.carrillo"
    cmdb.canonical.com/team:               "amer-sre"
    cmdb.canonical.com/type:               "prod"              # prod|staging|dev|lab
    cmdb.canonical.com/criticality-tier:   "1"                 # 1|2|3
    cmdb.canonical.com/data-class:         "internal"          # pii|internal|public
    cmdb.canonical.com/oncall:             "PD-SVC-AMER-PROD"
    cmdb.canonical.com/created:            "2024-11-01"
    cmdb.canonical.com/runbook:            "https://wiki.canonical.com/sre/runbooks/amer-prod-launchpad"
    cmdb.canonical.com/depends-on:         "shared-vault-prod,shared-postgres-prod"
    cmdb.canonical.com/maintenance-window: '{"day":"sunday","start":"02:00","end":"06:00","tz":"UTC"}'
```

Dependencies declared via label are stored with `dependency_type = 'declared'`. Dependencies inferred by the parser from Terraform and Juju are stored with `dependency_type = 'infrastructure'`. Both can coexist for the same pair.
