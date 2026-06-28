# Poller

## Overview

The poller is a Kubernetes CronJob that runs every 5 minutes inside the cluster. It queries the Juju controller API (primary) and the Kubernetes API (fallback) to determine where each environment is currently running. Results are written to Redis with an 8-minute TTL and, once per hour, appended to the `placement_history` table in PostgreSQL.

A missing Redis key is itself an operational signal: if the TTL expires before the next poll, the environment's placement is unknown and the UI surfaces a stale badge.

## Kubernetes CronJob manifest

```yaml
# is-infrastructure/apps/cmdb/cronjob-poller.yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: cmdb-poller
  namespace: cmdb
spec:
  schedule: "*/5 * * * *"
  concurrencyPolicy: Forbid       # do not run overlapping polls
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 5
  jobTemplate:
    spec:
      template:
        spec:
          serviceAccountName: cmdb-poller
          restartPolicy: OnFailure
          containers:
            - name: poller
              image: ghcr.io/canonical/is-cmdb/poller:latest
              env:
                - name: REDIS_URL
                  valueFrom:
                    secretKeyRef:
                      name: cmdb-secrets
                      key: redis-url
                - name: DATABASE_URL
                  valueFrom:
                    secretKeyRef:
                      name: cmdb-secrets
                      key: database-url
                - name: JUJU_CONTROLLER_URL
                  valueFrom:
                    secretKeyRef:
                      name: cmdb-secrets
                      key: juju-controller-url
                - name: JUJU_USERNAME
                  valueFrom:
                    secretKeyRef:
                      name: cmdb-secrets
                      key: juju-username
                - name: JUJU_PASSWORD
                  valueFrom:
                    secretKeyRef:
                      name: cmdb-secrets
                      key: juju-password
```

## Poller logic

### Entry point: `poller/poller.py`

```
Runs on every CronJob invocation.

1. Load all environment names and their juju_model from PostgreSQL
   (only environments where status != 'archived' and end_date IS NULL)

2. For each environment, call get_placement(environment)

3. Write placement JSON to Redis:
   Key:   env:{environment.name}:placement
   Value: JSON (see schema)
   TTL:   480 seconds

4. Once per hour (check last_history_write timestamp in Redis):
   Write row to placement_history table in PostgreSQL

5. Log summary: N environments polled, M failed, K written to history
```

### `poller/sources/juju_api.py`

```
Uses the Juju Python client (python-libjuju) to connect to the Juju controller.

For each environment:
  - Connect to the model named in environment.juju_model
  - List all units and their machine/container placement
  - Identify the primary unit as the leader (juju show-unit --format json)
  - Identify secondary units as non-leaders

Returns PlacementResult:
  primary_node:    machine hostname or container name for the leader unit
  secondary_node:  machine hostname for first non-leader unit (if any)
  juju_model:      model name
  juju_units:      list of {unit, machine, leader: bool}
  source:          "juju-api"

If the model is not found or the connection fails, raise PollerSourceError.
The main poller catches this and falls through to the kubectl fallback.
```

### `poller/sources/kubectl.py`

```
Fallback source. Uses the in-cluster Kubernetes service account to query the K8s API
via the official kubernetes Python client.

For each environment:
  - Infer the namespace from the environment name
    (use a configurable name->namespace mapping in poller-config.yaml)
  - List all pods in the namespace
  - Group by node using pod.spec.node_name
  - Identify primary as the pod with the highest-ordinal StatefulSet index (if StatefulSet)
    or the pod with the oldest creation timestamp (fallback)

Returns PlacementResult:
  primary_node:    node name for primary pod
  secondary_node:  node name for first non-primary pod (if any)
  juju_model:      null (not available from K8s API)
  juju_units:      list of {pod_name, node, ready: bool}
  source:          "kubectl"

If no pods are found in the namespace, raise PollerSourceError.
```

### Placement result schema

```python
@dataclass
class PlacementResult:
    primary_node:   str | None
    secondary_node: str | None
    juju_model:     str | None
    juju_units:     list[dict]
    source:         str          # "juju-api" | "kubectl"
    polled_at:      str          # ISO 8601 timestamp
```

Serialised to JSON for Redis:
```json
{
  "primary_node":   "node-3.maas.canonical.com",
  "secondary_node": "node-7.maas.canonical.com",
  "juju_model":     "amer-prod",
  "juju_units": [
    {"unit": "postgresql/0", "machine": "3", "leader": true},
    {"unit": "postgresql/1", "machine": "7", "leader": false}
  ],
  "polled_at": "2026-04-10T14:35:00Z",
  "source": "juju-api"
}
```

## History pruning

A separate CronJob runs daily and deletes placement_history rows older than 30 days:

```sql
DELETE FROM placement_history WHERE recorded_at < NOW() - INTERVAL '30 days';
```

## Environment variables

| Variable | Description |
|---|---|
| `REDIS_URL` | Redis connection string, e.g. `redis://cmdb-redis:6379/0` |
| `DATABASE_URL` | PostgreSQL connection string |
| `JUJU_CONTROLLER_URL` | Juju controller API endpoint |
| `JUJU_USERNAME` | Juju API username |
| `JUJU_PASSWORD` | Juju API password |
| `POLL_INTERVAL_SECONDS` | Optional, for local dev only. CronJob schedule is authoritative. |
| `HISTORY_WRITE_INTERVAL_SECONDS` | How often to write to placement_history. Default 3600. |
| `LOG_LEVEL` | Optional, defaults to INFO |

## RBAC

The poller's Kubernetes service account needs:
```yaml
rules:
  - apiGroups: [""]
    resources: ["pods", "namespaces"]
    verbs: ["get", "list"]
  - apiGroups: ["apps"]
    resources: ["statefulsets", "deployments"]
    verbs: ["get", "list"]
```

No write access to any Kubernetes resource is required or granted.
