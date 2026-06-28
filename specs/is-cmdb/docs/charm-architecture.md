# IS-CMDB charm architecture (issue #62)

A single Juju **Kubernetes charm** (`is-cmdb`) operating a 12-application bundle.
Each application is a Pebble-managed workload container in the same charm
deployment, related to its data stores and to the COS observability stack.

## Applications & relations

| App | Container image | Relations |
|-----|-----------------|-----------|
| django | `is-cmdb-django` | postgresql, redis, nginx, vault |
| postgresql | `charmed-postgresql` | django, collector, parser |
| redis | `charmed-redis` | django, collector |
| collector | `is-cmdb-collector` | postgresql, redis, s3-integrator |
| parser | `is-cmdb-parser` | postgresql |
| netbox-receiver | `is-cmdb-netbox-receiver` | postgresql |
| nginx | `nginx` | django |
| grafana | `cos-grafana` | prometheus, loki |
| prometheus | `cos-prometheus` | django (metrics endpoint) |
| loki | `cos-loki` | django (log push) |
| vault | `vault` | django |
| s3-integrator | `s3-integrator` | collector |

### Data flow (recap of ARCHITECTURE.md)

- **parser** (GitHub Actions today; charmed workload here) writes *declared* state
  to **postgresql** — idempotent `INSERT … ON CONFLICT DO UPDATE`.
- **collector** writes *live placement* to **redis** (TTL 480 s) and rolls hourly
  snapshots to **postgresql** / **s3** (via s3-integrator).
- **netbox-receiver** consumes Netbox webhooks → **postgresql** (Node upserts).
- **django** reads postgresql + redis, never writes either; fronted by **nginx**,
  secrets from **vault**, metrics scraped by **prometheus**, logs shipped to
  **loki**, dashboards in **grafana**.

```
            ┌─────────┐      ┌──────────┐
 webhooks → │ netbox- │      │  parser  │ ← repository_dispatch
            │ receiver│      └────┬─────┘
            └────┬────┘           │
                 ▼                ▼
              ┌──────────── postgresql ────────────┐
              │                                     │
   ┌──────────┴───┐   ┌────────┐   ┌────────────┐   │
   │  collector   │──▶│ redis  │◀──│   django   │───┘
   └──────┬───────┘   └────────┘   └─────┬──────┘
          │ s3-integrator                │ nginx / vault
          ▼                              ▼
        S3 (history)            prometheus / loki / grafana (COS)
```

## Config options

| Option | Type | Purpose |
|--------|------|---------|
| `django-secret-key` | string (secret) | Django `SECRET_KEY` |
| `netbox-url` | string | Netbox API base URL |
| `netbox-token` | string (secret) | Netbox read token |
| `pagerduty-token` | string (secret) | PagerDuty read token |
| `debug` | boolean | Django `DEBUG` (default false) |

Secrets are delivered through Juju user secrets / the **vault** relation rather
than plain config where possible.

## Container images

All `is-cmdb-*` images are built from this repo's `Dockerfile`, differing only by
the entrypoint command:

| Image | Entrypoint |
|-------|------------|
| `is-cmdb-django` | `gunicorn cmdb.wsgi:application --bind 0.0.0.0:8000` |
| `is-cmdb-collector` | `python collector/ps5_parser.py` (cron/loop) |
| `is-cmdb-parser` | `python parser/parser.py …` |
| `is-cmdb-netbox-receiver` | django app serving `/api/webhooks/netbox/` |

`charmed-postgresql`, `charmed-redis`, `nginx`, `vault`, `s3-integrator`, and the
`cos-*` images are upstream/rocks images consumed as-is.

## Non-negotiable invariants carried into the charm

- django container is **read-only** to the data stores; only parser/collector/
  netbox-receiver write.
- redis TTL (480 s) is a health signal — django renders a stale badge on a
  missing key; the charm must not paper over a stopped collector.
- a single Redis client (`cmdb/redis_client.py`) — the charm injects `REDIS_URL`,
  never a second client.

See #63 for the charm skeleton and #64 for the django workload implementation.
