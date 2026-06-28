# Django UI

## Overview

The Django application serves the CMDB web interface and a REST API. It reads declared state from PostgreSQL and live placement from Redis. It does not write to either store directly — all writes go through the parser (Postgres) or the poller (Redis).

## Dependencies

```
django>=4.2
djangorestframework
django-tables2
django-filter
django-environ
psycopg2-binary
redis
```

## Django apps

### `environments` app

Handles the main CMDB UI. All user-facing views live here.

#### `models.py`

Define Django models that map to the schema in SCHEMA.md.

```python
# Key models:

class Environment(models.Model):
    # All fields from the environments table
    # Use ArrayField (django.contrib.postgres) for compliance_scope
    # Use JSONField for charm_versions, maintenance_window
    # status choices: provisioning, active, degraded, maintenance, decommissioning, archived
    # criticality_tier choices: 1, 2, 3

class EnvironmentDependency(models.Model):
    environment = models.ForeignKey(Environment, related_name='dependencies', on_delete=models.CASCADE)
    depends_on  = models.ForeignKey(Environment, related_name='dependents',   on_delete=models.CASCADE)
    dependency_type = models.CharField(max_length=20)  # infrastructure | declared

    class Meta:
        unique_together = ('environment', 'depends_on')

class PlacementHistory(models.Model):
    environment_name = models.CharField(max_length=255)
    primary_node     = models.CharField(max_length=255, null=True)
    secondary_node   = models.CharField(max_length=255, null=True)
    juju_model       = models.CharField(max_length=255, null=True)
    juju_units       = models.JSONField(null=True)
    source           = models.CharField(max_length=20)
    recorded_at      = models.DateTimeField(auto_now_add=True)
```

#### `views.py`

```python
# EnvironmentListView
#   URL: /
#   Uses django-tables2 EnvironmentTable
#   Uses django-filter EnvironmentFilter
#   Annotates each environment with its live placement from Redis
#     placement = redis_client.get(f"env:{env.name}:placement")
#     If None: placement_status = "stale"
#     If present: placement_status = "live", parse JSON for node names
#   Template: environments/list.html

# EnvironmentDetailView
#   URL: /environments/<name>/
#   Shows full environment record from Postgres
#   Shows live placement panel from Redis (with stale indicator if key missing)
#   Shows dependency graph: what this env depends on + what depends on this env
#   Shows placement history (last 10 rows from placement_history table)
#   Template: environments/detail.html

# BlastRadiusView
#   URL: /environments/<name>/blast-radius/
#   Recursively resolves environment_dependencies to show full downstream impact
#   Returns JSON: {"affected": ["env-a", "env-b", ...]}
#   Used by the detail page to render the blast radius section
```

#### `tables.py`

```python
# EnvironmentTable (django-tables2)
# Columns:
#   name              - linkified to detail view
#   region            - filterable
#   env_type          - filterable, badge rendering
#   criticality_tier  - sortable, badge (T1 = red, T2 = amber, T3 = green)
#   team              - filterable
#   owner
#   status            - badge rendering with colour per status value
#   placement_status  - "live" (green) or "stale" (amber) based on Redis TTL
#   primary_node      - from Redis, or "unknown" if stale
#   updated_at        - sortable
```

#### `filters.py`

```python
# EnvironmentFilter (django-filter)
# Filterable fields:
#   region            - exact, multiple choice
#   env_type          - exact, multiple choice
#   criticality_tier  - exact, multiple choice
#   team              - exact
#   status            - exact, multiple choice
#   owner             - contains
#   name              - contains
#   placement_status  - live | stale (computed from Redis, not a DB field)
#     For placement_status filter: fetch all live keys from Redis using SCAN
#     pattern env:*:placement, build set of live env names, filter queryset
```

### `api` app

REST API for programmatic access. Used by future tooling (e.g. a Slack bot for blast radius queries, or a Grafana datasource plugin).

```python
# Endpoints:

GET  /api/environments/
     Returns paginated list of environments with declared state fields
     Supports same filter params as the UI

GET  /api/environments/<name>/
     Returns full environment record

GET  /api/environments/<name>/placement/
     Returns current Redis placement data
     Returns {"status": "stale"} if Redis key is missing

GET  /api/environments/<name>/blast-radius/
     Returns recursive list of downstream affected environments

GET  /api/environments/<name>/dependencies/
     Returns direct dependencies (both directions)

GET  /api/health/
     Returns {"postgres": "ok", "redis": "ok"} or appropriate error status
     Used by Kubernetes liveness and readiness probes
```

## Redis client

```python
# cmdb/redis_client.py
# Initialise a single Redis connection using REDIS_URL from environment
# Provide two helpers used by views:

def get_placement(env_name: str) -> dict | None:
    """
    Returns parsed placement dict if key exists and is not stale.
    Returns None if key is missing (TTL expired or never written).
    """

def get_all_live_env_names() -> set[str]:
    """
    Uses Redis SCAN to find all keys matching env:*:placement.
    Returns set of environment names that currently have live placement data.
    Used by the filter to support placement_status=live|stale filtering.
    """
```

## Templates

Use Django template inheritance. Base template includes:
- Top navigation with region filter tabs (All / AMER / APAC / EMEA)
- Search bar (filters by name)
- Link to API docs

List template:
- django-tables2 rendered table with sorting and pagination
- Filter sidebar (region, type, tier, status, placement status)
- Export to CSV button (django-tables2 built-in)

Detail template:
- Two-column layout: left = declared state (from Postgres), right = live state (from Redis)
- Live state panel shows primary_node, secondary_node, last polled time
- Stale indicator: amber banner if Redis key missing, showing time since last known placement
- Dependencies section: expandable list of what this env depends on and what depends on it
- Blast radius section: "If this environment went down, X environments would be affected"
- Placement history: table of last 10 placement_history rows with timestamps
- Maintenance window display: human-readable from JSON field
- Runbook link: prominent button if runbook_url is set

## Settings

```python
# Required environment variables (use django-environ):
DATABASE_URL     # PostgreSQL connection string
REDIS_URL        # Redis connection string
SECRET_KEY       # Django secret key
ALLOWED_HOSTS    # Comma-separated list
DEBUG            # True | False
```

## Deployment

The Django app runs as a standard Kubernetes Deployment. The Flux kustomization in `is-infrastructure/apps/cmdb/` manages it.

```yaml
# is-infrastructure/apps/cmdb/deployment-django.yaml
# Key settings:
#   replicas: 2
#   image: ghcr.io/canonical/is-cmdb/cmdb:latest
#   readinessProbe: GET /api/health/ 200
#   livenessProbe:  GET /api/health/ 200
#   resources:
#     requests: {cpu: 100m, memory: 256Mi}
#     limits:   {cpu: 500m, memory: 512Mi}
```

Django migrations run as a Kubernetes Job on each deployment, before the Deployment rolls out:
```yaml
# is-infrastructure/apps/cmdb/job-migrate.yaml
# command: python manage.py migrate --noinput
# runPolicy: before Deployment rollout
```
