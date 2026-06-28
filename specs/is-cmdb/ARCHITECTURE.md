# CMDB Architecture

## Overview

This CMDB tracks all environments declared in the `is-infrastructure` GitOps repository. It answers two distinct questions with two distinct data stores:

- **What is this environment and who owns it?** Answered by PostgreSQL. Source of truth is the Git repository. Updated on every merge to main.
- **Where is it actually running right now?** Answered by Redis. Source of truth is the live Juju controller and Kubernetes API. Updated every 5 minutes by a poller.

These two concerns are intentionally separated. Declared state (Postgres) is persistent and historical. Live state (Redis) is ephemeral with TTL-based freshness indicators.

## Repo Structure

Two repositories are involved:

### `is-infrastructure` (existing)
Contains all Terraform modules, Juju bundles, Flux kustomizations, and YAML manifests. This repo declares the desired state of all environments. It also contains the Flux kustomization that deploys the CMDB stack itself.

```
is-infrastructure/
  apps/
    cmdb/
      kustomization.yaml        # Flux points at is-cmdb repo for the image
      postgres.yaml             # PostgreSQL deployment
      redis.yaml                # Redis deployment
      cronjob-poller.yaml       # Poller CronJob (every 5 minutes)
      deployment-django.yaml    # Django app deployment
      ingress.yaml              # Internal ingress
```

### `is-cmdb` (new)
Contains all application code. The infra repo deploys it; it has no knowledge of the infra repo's internals beyond reading its YAML files.

```
is-cmdb/
  parser/                       # Ingestion: reads infra repo YAML on merge
    parser.py                   # Main parser entrypoint
    extractors/
      terraform.py              # Extracts dependencies from remote state refs
      juju.py                   # Extracts dependencies from bundle relations
      manifest.py               # Reads cmdb labels from YAML manifests
  poller/                       # Live state: queries Juju API and K8s every 5 min
    poller.py                   # Main poller entrypoint
    sources/
      juju_api.py               # Primary: queries Juju controller API
      kubectl.py                # Fallback: queries Kubernetes API
  cmdb/                         # Django application
    settings.py
    urls.py
    apps/
      environments/
        models.py
        views.py
        serializers.py
        filters.py
        urls.py
      api/
        views.py                # REST API for programmatic access
  migrations/                   # Django DB migrations
  Dockerfile
  requirements.txt
  .github/
    workflows/
      ingest.yml                # Triggered by infra repo push via repository_dispatch
      build.yml                 # Builds and pushes Docker image on is-cmdb push
```

## Data Flow

```
is-infrastructure (merge to main)
  → repository_dispatch event
    → is-cmdb ingest.yml workflow
      → checks out is-infrastructure at triggered SHA
        → parser.py walks YAML tree
          → upserts environments table in PostgreSQL
          → upserts environment_dependencies table

Juju controller API + Kubernetes API
  → poller CronJob (every 5 minutes)
    → writes placement JSON to Redis with 8-minute TTL
    → appends row to placement_history table (every 60 minutes)

Django UI
  → reads environments + dependencies from PostgreSQL
  → reads current placement from Redis
  → surfaces stale badge when Redis key is missing (TTL expired)
```

## Cross-Repo Trigger

The infra repo sends a `repository_dispatch` event to the CMDB repo on every push to main. The infra repo has no other knowledge of the CMDB repo. If the CMDB ingestion fails, infrastructure reconciliation is completely unaffected.

The dispatch token requires:
- `contents: read` on `is-infrastructure`
- `actions: write` on `is-cmdb`

Store as `CMDB_DISPATCH_TOKEN` in `is-infrastructure` secrets and `INFRA_READ_TOKEN` in `is-cmdb` secrets.

## Technology Stack

| Component | Technology | Reason |
|---|---|---|
| Declared state store | PostgreSQL | Persistent, queryable, relational dependencies |
| Live state store | Redis | Fast reads, TTL-based freshness, no persistence burden |
| Placement history | PostgreSQL (separate table) | Incident retrospectives, rolling 24h window |
| Ingestion | Python (GitHub Actions) | Runs on merge, stateless, no infra needed |
| Poller | Python (Kubernetes CronJob) | Runs in-cluster, has network access to Juju/K8s APIs |
| UI | Django + django-tables2 + django-filter | Team can maintain without frontend expertise |
| API | Django REST Framework | Programmatic access for future tooling |
