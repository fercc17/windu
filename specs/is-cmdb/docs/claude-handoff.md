# IS-CMDB Overnight Build — Claude Handoff Prompt

This document is a self-contained brief for a Claude Code instance with full tool permissions. Read it entirely before touching a single file.

---

## Repository

```
/home/fer/projects/is-cmdb
```

It is a Django 4.2 / Python 3.12 project. The main app lives under `cmdb/`. Tests go under `tests/`. Spec docs are in the repo root (ARCHITECTURE.md, SCHEMA.md, PARSER.md, DJANGO_UI.md, NETBOX_INTEGRATION.md). The authoritative code-style rules are in `.github/copilot-instructions.md`.

---

## Task 0 — Parse and analyse juju fixture files (do this first, before any code)

`tests/fixtures/juju/` contains three files — `ps5.txt`, `ps6.txt`, `ps7.txt` — each being the raw output of `openstack server list` for that cloud. They are tab-separated tables with columns: ID, Name, Status, Task State, Power State, Networks, Image Name, Image ID, **Flavor Name**, **Flavor ID**, **Availability Zone**, **Host**, Properties.

The **Host** column is the physical node hostname (e.g. `ps5-ra2-n4.maas`, `ps6-ra3-n4.ps6.canonical.com`). The **Availability Zone** column is the OpenStack AZ the instance is scheduled to.

Do the following:

1. **Parse all three files** — extract the distinct `(cloud, availability_zone, host)` tuples for all ACTIVE instances.

2. **Check Netbox for AZ data** — query `GET /api/dcim/sites/` and `GET /api/dcim/locations/` (and any custom fields on devices) to see if Availability Zone is modelled. Search for the AZ names found in the files (e.g. `availability-zone-1`, `AZ1`, `availability-zone-z15`).

3. **If AZ is NOT in Netbox** — write the full node→AZ mapping to `docs/findings/az-node-mapping.json` (keyed by cloud, then hostname → AZ). Also prepare a `docs/findings/netbox-az-patch.md` that documents exactly which Netbox API calls would be needed to add AZ as a custom field on devices and populate it — ready for when write access is available.

4. **Match Juju models to K8s clusters** — scan the instance `Name` column for patterns that suggest a Kubernetes cluster (e.g. names containing `k8s`, `kubernetes`, `control-plane`, `worker`, or matching environment names that have `service_class=kubernetes_cluster` in the DB). Cross-reference against `Environment` records in the DB where `service_class='kubernetes_cluster'`. This match may be partial or indirect — document what you find and what you cannot determine, rather than guessing.

5. **Summarise findings** in `docs/findings/netbox-audit.md` (the output already required by issue #21), adding a section "Juju fixture analysis" covering: distinct AZs per cloud, node count per AZ, flavors seen per cloud, and K8s cluster matches found.

---

## Credentials (already in .env — do not commit)

```
NETBOX_TOKEN=<from .env>
NETBOX_URL=https://netbox.staging.admin.canonical.com/stg-netbox-k8s-netbox/api/
PAGERDUTY_API_TOKEN=<from .env>   # READ-ONLY — see constraints below
```

---

## Branch

Create **one branch** before writing any code. Name it:

```
feature/gh-21-22-23-24-26-27-28-29-30-31-32-33-34-35-36-37-38-39-42-44-45-46-47-53-56-59-62-63-64
```

**Do NOT merge into main and do NOT close any GitHub issue.** The owner will review each item and approve merges manually.

**Commit after every single issue is completed.** Use this format exactly:

```
gh-<number>: <issue title>

Co-Authored-By: Claude Opus <noreply@anthropic.com>
```

Example: `gh-22: 3.2 Create Node, NodeInterface, NodeCable Django models and migration`

This is critical — if the session runs out of tokens and dies, the next session will run `git log` to see what was already done and resume from the next issue. Without per-issue commits, work is lost or duplicated.

---

## Codebase snapshot

### Python files that already exist

```
cmdb/
  settings.py           — django-environ, reads .env; INSTALLED_APPS includes environments, api
  urls.py               — root router: admin/, environments app at /, api/ prefix
  redis_client.py       — single Redis client; all Redis access must go through here
  wsgi.py
  apps/
    environments/
      models.py         — Environment, EnvironmentDependency, CloudCapacity, PlacementHistory
      views.py          — EnvironmentListView, environment_detail, team_aggregation, blast_radius, etc.
      urls.py           — all environment + aggregation routes
      tables.py         — django_tables2 table classes
      filters.py        — EnvironmentFilter (django_filters)
      migrations/       — 0001..0006 applied
      management/commands/
        import_csv.py
        populate_architecture_from_redis.py
        update_cloud_capacity.py
    api/
      views.py           — health, team_resource_utilization
      urls.py            — /api/health/, /api/teams/resource-utilization/, schema, docs
    storage/
      models.py          — empty (just `# Create your models here.`)
      views.py           — empty
      admin.py, apps.py  — scaffolded only
```

### Templates

Only `cmdb/templates/base.html` exists. All new templates go under their app's `templates/<app>/` directory following the existing `environments/templates/environments/` pattern.

### Key invariants (non-negotiable)

- Parser is idempotent: `INSERT … ON CONFLICT DO UPDATE`, never `get_or_create`.
- Soft-delete only on `environments`: set `end_date` + `status='decommissioning'`, never DELETE.
- Redis TTL as health signal: views must handle `None` from `get_placement()`.
- Single Redis client: all Redis access through `cmdb/redis_client.py`.
- All SQL is parameterised — no string interpolation.
- Type hints on all function signatures.
- No `print()` — use `logging.getLogger(__name__)`.
- No admin/superuser views — SRE-only read tool.

---

## Issues to implement — with per-issue instructions

### Phase 3 — Netbox integration

#### #21 — 3.1 Explore Netbox instance and document available device data per cloud
**Output:** `docs/findings/netbox-audit.md`

Use the live Netbox API (token in `.env`) to explore:
- `GET /api/dcim/sites/` — 12 sites exist; map each to a cloud name
- `GET /api/dcim/devices/?limit=100` — 1 110 devices total; note available fields: id, name, device_type, role, site, rack, status, primary_ip, custom_fields, interface_count
- `GET /api/dcim/device-roles/` — list all roles; identify which ones are "server" vs "switch"
- `GET /api/dcim/interfaces/?limit=5` — document interface schema
- `GET /api/dcim/cables/?limit=5` — document cable schema; note whether switch uplink data is populated
- Custom fields: look at `custom_fields` on a few devices and list what keys exist

Write a concise markdown audit doc. This feeds issues #22, #23, #24, and #39.

---

#### #22 — 3.2 Create Node, NodeInterface, NodeCable Django models and migration

Create a new Django app `cmdb/apps/netbox/` with models:

```python
class Node(models.Model):
    netbox_id = models.IntegerField(unique=True)
    hostname = models.CharField(max_length=255, unique=True, db_index=True)
    site = models.CharField(max_length=100)           # Netbox site slug
    cloud = models.CharField(max_length=50, db_index=True)  # ps5, ps6, etc.
    role = models.CharField(max_length=100)           # server, switch, etc.
    rack = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=50)          # active, decommissioning, etc.
    primary_ip = models.GenericIPAddressField(blank=True, null=True)
    uplink_redundancy = models.BooleanField(default=False)
    physical_completeness = models.FloatField(default=0.0,
        help_text="0.0–1.0; fraction of interfaces with cable records")
    last_synced_at = models.DateTimeField(auto_now=True)

class NodeInterface(models.Model):
    node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name='interfaces')
    netbox_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=100)
    mac_address = models.CharField(max_length=17, blank=True, null=True)
    speed_mbps = models.IntegerField(blank=True, null=True)

class NodeCable(models.Model):
    netbox_id = models.IntegerField(unique=True)
    interface_a = models.ForeignKey(NodeInterface, on_delete=models.CASCADE,
                                    related_name='cables_as_a')
    interface_b = models.ForeignKey(NodeInterface, on_delete=models.CASCADE,
                                    related_name='cables_as_b', blank=True, null=True)
    cable_type = models.CharField(max_length=50, blank=True, null=True)
```

Add `primary_node` and `secondary_node` FK fields to `Environment` pointing to `Node` (nullable). Register the new app in `INSTALLED_APPS`. Generate and apply migration.

---

#### #23 — 3.3 Write Netbox webhook receiver endpoint

File: `cmdb/apps/netbox/webhook.py`
URL: `POST /api/webhooks/netbox/`

- Validate `X-NetBox-Signature` HMAC-SHA512 using `NETBOX_WEBHOOK_SECRET` from env (fail gracefully if not set — log warning, still accept in dev).
- Event `created`: upsert Node via `netbox_id`.
- Event `updated`: update Node fields.
- Event `deleted`: set `status='decommissioning'`, never DELETE.
- Always return HTTP 200 immediately (no blocking DB work in the response path — use a signal or inline but keep it fast).

---

#### #24 — 3.4 Write nightly Netbox full reconciliation management command

File: `cmdb/apps/netbox/management/commands/reconcile_netbox.py`

- Paginate through all Netbox devices (`/api/dcim/devices/`).
- For each device: upsert into `Node` via `netbox_id`.
- Compute `physical_completeness` per node: `interfaces_with_cables / total_interfaces`.
- Devices absent from Netbox but present in DB: set `status='decommissioning'`.
- Log summary at end: inserted, updated, decommissioned counts.
- Use `requests` with a `session` and `time.sleep(0.1)` between pages to avoid overloading Netbox.

---

#### #26 — 3.6 Link environments to nodes via placement data

Extend the collector (or add a management command `link_placement_nodes`) that, after placement data arrives in Redis, sets `Environment.primary_node` and `Environment.secondary_node` by matching the hostname in the placement payload against `Node.hostname`. Only update if a matching Node exists; never create nodes here.

---

#### #27 — 3.7 Build node detail view

URL: `/nodes/<hostname>/`

Template: `cmdb/apps/netbox/templates/netbox/node_detail.html`

Sections:
- Physical identity: hostname, site, cloud, rack, role, status, primary IP
- Interfaces table: name, MAC, speed, cable present (yes/no)
- Environments currently placed on this node (from `Environment.primary_node` and `secondary_node`)
- Placement history (last 10 from `PlacementHistory`)
- Physical completeness badge: green ≥ 0.8, amber 0.5–0.8, red < 0.5
- Active maintenance window banner (red) — placeholder until #31 models exist

---

#### #28 — 3.8 Add physical_completeness warnings to cloud and environment views

On the environment detail page: show an amber banner if the environment's `primary_node.physical_completeness < 0.8`.
On the cloud detail page (from #29): show amber banner if any node in that cloud has `physical_completeness < 0.8`.

---

#### #29 — 3.9 Build cloud list and detail stub views

URL: `/clouds/` and `/clouds/<slug>/`

Cloud list columns: name, region, provider, status, node_count, environment_count, physical_completeness (worst-node value).

Cloud detail shows: nodes table, environments table, physical completeness banner.

Clouds are derived from `Node.cloud` distinct values + `Environment.cloud` distinct values (union). No separate Cloud model needed yet — compute on the fly.

---

### Phase 4 — PagerDuty / maintenance windows

#### #30 — 4.1 Audit PagerDuty services and define environment-to-service mapping strategy

**Output:** `docs/findings/pagerduty-audit.md`

Use the PagerDuty API (read-only token in `.env`) to:
- `GET /teams` — list all teams; find "IS" and "IS 24x7" (these are the only relevant teams).
- `GET /services?include[]=teams&limit=100` — paginate all pages. For each service, note which team it belongs to.
- For services belonging to IS or IS 24x7: list id, name, team.
- `GET /oncalls?limit=100` — list current on-call schedules for IS and IS 24x7.
- `GET /maintenance_windows?limit=10` — look at the schema of existing maintenance windows.

Map Juju model names from `tests/fixtures/juju/` (create that directory if empty) to PagerDuty services where naming matches. Document the mapping strategy: e.g. environment `name` → search PD service names for substring match. Note any gaps.

Also document: to create/delete maintenance windows you need a write-capable token. Current token is read-only. A second token `PAGERDUTY_WRITE_TOKEN` will be added later.

**Do NOT overload the PD API** — add `time.sleep(0.2)` between paginated requests.

---

#### #31 — 4.2 Create maintenance_windows and maintenance_notification_channels models

Create `cmdb/apps/maintenance/` Django app with:

```python
class MaintenanceWindow(models.Model):
    node = models.ForeignKey('netbox.Node', on_delete=models.CASCADE,
                             related_name='maintenance_windows')
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reason = models.TextField()
    STATUS_CHOICES = [('scheduled','Scheduled'),('active','Active'),
                      ('completed','Completed'),('cancelled','Cancelled')]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='scheduled')
    pagerduty_window_id = models.CharField(max_length=100, blank=True, null=True)
    created_by = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class MaintenanceNotificationChannel(models.Model):
    window = models.ForeignKey(MaintenanceWindow, on_delete=models.CASCADE,
                               related_name='channels')
    CHANNEL_CHOICES = [('pagerduty','PagerDuty'),('mattermost','Mattermost'),('email','Email')]
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES)
    sent_at = models.DateTimeField(blank=True, null=True)
    success = models.BooleanField(default=False)
    error_message = models.TextField(blank=True, null=True)

class MaintenanceWindowEnvironment(models.Model):
    window = models.ForeignKey(MaintenanceWindow, on_delete=models.CASCADE)
    environment = models.ForeignKey('environments.Environment', on_delete=models.CASCADE)

    class Meta:
        unique_together = ('window', 'environment')
```

Register in `INSTALLED_APPS`, generate and apply migration.

---

#### #32 — 4.3 Build maintenance window creation UI

URL: `/nodes/<hostname>/maintenance/new/`

Form fields:
- `starts_at` (datetime)
- `ends_at` (datetime)
- `reason` (textarea)
- Notification checkboxes: PagerDuty silence, Mattermost DM, Email

Preview section: list of environments whose `primary_node` or `secondary_node` is this node — shown before form submission so the SRE can see blast radius.

On POST: create `MaintenanceWindow`, create `MaintenanceWindowEnvironment` rows for each affected environment, then trigger the selected notification channels.

---

#### #33 — 4.4 Implement PagerDuty maintenance window creation via API — MOCKUP ONLY

**The current PagerDuty token is read-only. Do NOT attempt real API writes.**

Create `cmdb/integrations/pagerduty.py` with:

```python
def create_maintenance_window(window: MaintenanceWindow) -> str | None:
    """
    Creates a PD maintenance window covering all IS/IS-24x7 services on the node.
    Returns the PD window ID, or None if PAGERDUTY_WRITE_TOKEN is not set.
    NOTE: requires PAGERDUTY_WRITE_TOKEN (not the read-only token).
    """
    token = os.environ.get('PAGERDUTY_WRITE_TOKEN')
    if not token:
        logger.warning("PAGERDUTY_WRITE_TOKEN not set — skipping PD maintenance window creation")
        return None
    # TODO: implement when write token is available
    # POST https://api.pagerduty.com/maintenance_windows
    raise NotImplementedError("Requires PAGERDUTY_WRITE_TOKEN")

def cancel_maintenance_window(pd_window_id: str) -> bool:
    """DELETE /maintenance_windows/{id}. Requires PAGERDUTY_WRITE_TOKEN."""
    token = os.environ.get('PAGERDUTY_WRITE_TOKEN')
    if not token:
        logger.warning("PAGERDUTY_WRITE_TOKEN not set — skipping PD cancellation")
        return False
    raise NotImplementedError("Requires PAGERDUTY_WRITE_TOKEN")
```

Document in a comment the exact API call shape (payload, headers) so the write implementation is trivial once the token arrives.

---

#### #34 — 4.5 Implement PagerDuty maintenance window cancellation

URL: `POST /maintenance/<id>/cancel/`

View that calls `pagerduty.cancel_maintenance_window(window.pagerduty_window_id)`, sets `window.status = 'cancelled'`, saves, redirects to detail. Gracefully handles the `NotImplementedError` (shows a flash message: "PD cancellation requires write token").

---

#### #35 — 4.6 Implement Mattermost notification for maintenance windows

File: `cmdb/integrations/mattermost.py`

**Target:** DM to user `fercc17`. Eventually moves to a channel with team tags — design for that future.

Token source: `MATTERMOST_TOKEN` from env (add placeholder to `.env`).
Server: derive `MATTERMOST_URL` from env too.

Notification structure (two API calls — initial post + thread reply):

**Initial post (DM):**
```
### 🔧 Maintenance Window — <node hostname>
**Status:** Opened  
**Node:** <hostname> | **Cloud:** <cloud>  
**Window:** <starts_at> → <ends_at> UTC  
**Reason:** <reason>
```

**Thread reply (tagging teams):**
```
Environments affected: <comma-separated list>  
Teams involved: <unique team slugs from affected environments, each as @<slug>>  
Please acknowledge if this affects your service.
```

If `MATTERMOST_TOKEN` is not set: log a warning and return without error (do not crash the maintenance window creation flow).

---

#### #36 — 4.7 Implement email notification for maintenance windows — MOCK

File: `cmdb/integrations/email_notify.py`

Read recipients from the CIA assessment fields on affected environments (`cia_owner`, `cia_risk_owner`) — deduplicate, one email per unique address.

```python
def send_maintenance_email(window: MaintenanceWindow, environments: list) -> None:
    """
    Sends email notification to environment CIA owners.
    Currently mocked — SMTP credentials not yet available.
    Set EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD in .env to activate.
    """
    smtp_host = os.environ.get('EMAIL_HOST')
    if not smtp_host:
        logger.warning("EMAIL_HOST not set — logging email notification only")
        for env in environments:
            logger.info("Would email %s about maintenance on %s", env.cia_owner, window.node.hostname)
        return
    # TODO: implement real SMTP send when credentials are available
    raise NotImplementedError("Requires EMAIL_HOST credentials")
```

---

#### #37 — 4.8 Build maintenance window list and detail views

URL: `/maintenance/` — table of all windows, columns: node, starts_at, ends_at, status, PD window ID, actions.

URL: `/maintenance/<id>/` — detail: all fields, environments affected table, notification log (from `MaintenanceNotificationChannel`), Cancel button (POST to #34 endpoint, only shown if status is scheduled/active).

---

#### #38 — 4.9 Add active maintenance indicators to node and environment views

- Node detail (`/nodes/<hostname>/`): red banner at top if any `MaintenanceWindow` for this node is `status='active'` or `status='scheduled'` and `starts_at` ≤ now+24h.
- Environment detail: amber banner if the environment's `primary_node` or `secondary_node` has an active/upcoming maintenance window.
- Environment list: amber badge in the status column for environments under maintenance.

---

### Phase 5 — Storage & resilience

#### #39 — 5.1 Model switch dependencies from Netbox cable data

First, check whether switch/cable data exists in Netbox:
```
GET /api/dcim/devices/?role=switch&limit=10
GET /api/dcim/cables/?limit=10
```

**If switch and cable data exists:** create `node_switch_connections` table:
```python
class NodeSwitchConnection(models.Model):
    node = models.ForeignKey('netbox.Node', on_delete=models.CASCADE,
                             related_name='switch_connections')
    switch_hostname = models.CharField(max_length=255)
    interface_name = models.CharField(max_length=100)
    port_name = models.CharField(max_length=100)
```
Add `Node.uplink_redundancy = True` when the node has cables to ≥ 2 distinct switches.
Add management command `build_switch_graph` that reads `NodeCable` records and populates this table.

**If switch/cable data is absent or sparse:** document the finding in `docs/findings/netbox-audit.md` and create the model + command as a stub with a clear log message: "No cable data found in Netbox — switch graph is empty."

---

#### #42 — 5.4 Build resilience query UI on node and switch detail pages

Add an "Impact Analysis" section to the node detail page (`/nodes/<hostname>/`):
- List of environments on this node and whether they have a secondary node (redundant vs single-homed).
- Uplink redundancy badge from `Node.uplink_redundancy`.

Add `/nodes/<hostname>/resilience/` page:
- Which switch(es) this node uplinks to.
- Other nodes sharing the same switch(es) (blast radius if switch fails).

---

#### #44 — 5.6 Write RadosGW bucket discovery and ingestion

**Check first:** the `cmdb/apps/storage/` app already exists but has empty models and views. The issue asks for `tools/rados_ingest.py` querying the RadosGW admin API. Since we don't have RadosGW credentials, implement as a structured stub:

File: `tools/rados_ingest.py`

```python
"""
RadosGW bucket discovery and ingestion.
Requires: RADOS_ADMIN_URL, RADOS_ACCESS_KEY, RADOS_SECRET_KEY in environment.
Run: python tools/rados_ingest.py
"""
```

Also populate `cmdb/apps/storage/models.py`:

```python
class StorageResource(models.Model):
    name = models.CharField(max_length=255, unique=True, db_index=True)
    bucket_name = models.CharField(max_length=255)
    cloud = models.CharField(max_length=50, db_index=True)
    owner_team = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    size_gb = models.FloatField(default=0.0)
    object_count = models.IntegerField(default=0)
    STORAGE_TYPE_CHOICES = [('radosgw','RadosGW'),('s3','S3'),('gcs','GCS')]
    storage_type = models.CharField(max_length=20, choices=STORAGE_TYPE_CHOICES, default='radosgw')
    last_synced_at = models.DateTimeField(auto_now=True)

class StorageEnvironmentAccess(models.Model):
    storage = models.ForeignKey(StorageResource, on_delete=models.CASCADE,
                                related_name='environment_accesses')
    environment = models.ForeignKey('environments.Environment', on_delete=models.CASCADE,
                                    related_name='storage_accesses')
    access_type = models.CharField(max_length=50, default='readwrite')
    class Meta:
        unique_together = ('storage', 'environment')
```

Register `cmdb.apps.storage` in `INSTALLED_APPS` (check if already there), generate and apply migration.

---

#### #45 — 5.7 Build storage resource list and detail views

URL: `/storage/` — table of all `StorageResource` records: name, cloud, owner_team, size_gb, object_count, environment count.

URL: `/storage/<name>/` — detail: metadata, environments that access this bucket (from `StorageEnvironmentAccess`).

URL: `/teams/<name>/storage/` — all storage resources owned by a team; link to `/storage/<name>/` for each.

---

#### #46 — 5.8 Add storage dependency to blast radius query

Extend the existing blast radius CTE (in `views.py`, endpoint `GET /api/environments/<name>/blast-radius/`) to include storage: if an environment accesses a `StorageResource`, include other environments that also access it as indirect blast-radius members with `dependency_type='storage'`.

Add `GET /api/storage/<name>/blast-radius/` — same shape, but starting from a storage resource rather than an environment.

---

#### #47 — 5.9 Build environment and RadosGW cross-reference view

Add a "Storage" section to the environment detail page listing all `StorageResource` records accessed by that environment.

Add `/storage/matrix/` — a matrix view: rows = storage resources, columns = teams, cells = environments using that storage. Useful for identifying cross-cloud storage dependencies.

---

### Phase 6 — Lifecycle views

#### #53 — 6.6 Build team RadosGW view

URL: `/teams/<name>/storage/` (same as #45 team view — implement them together).

Columns: bucket name, cloud, size_gb, environments accessing it, cross-cloud flag (True if environments from different clouds access this bucket).

---

#### #56 — 6.9 Build decommission notification log view

URL: `/clouds/decommission-log/`

Show all `MaintenanceNotificationChannel` records where the related `MaintenanceWindow.node.cloud` is being decommissioned (i.e. cloud status derived from all nodes in that cloud being `decommissioning`). Columns: cloud, node, environment, channel, sent_at, success. Filterable by cloud and date range.

---

#### #59 — 6.12 Build stakeholder management UI for cloud decommission notifications

**Do NOT implement email sending** (no SMTP yet).

On the cloud detail page (`/clouds/<slug>/`): add a "Stakeholders" section.

Add model `CloudStakeholder`:
```python
class CloudStakeholder(models.Model):
    cloud_slug = models.CharField(max_length=50, db_index=True)
    email = models.EmailField()
    name = models.CharField(max_length=255)
    added_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = ('cloud_slug', 'email')
```

UI: table of stakeholders + inline add/remove form. Validation: if cloud has any node with `status='decommissioning'`, block saving if stakeholder list would become empty (show form error).

---

### Phase 7 — Charm
The app charmed must be stored locally, do not upload anywhere.
#### #62 — 7.1 Design IS-CMDB charm architecture: 12-app k8s charm

**Output:** `docs/charm-architecture.md`

Document the 12 apps and their relations:

| App | Container image | Relations |
|-----|-----------------|-----------|
| django | is-cmdb-django | postgresql, redis, nginx, vault |
| postgresql | charmed-postgresql | django, collector, parser |
| redis | charmed-redis | django, collector |
| collector | is-cmdb-collector | postgresql, redis, s3-integrator |
| parser | is-cmdb-parser | postgresql |
| netbox-receiver | is-cmdb-netbox-receiver | postgresql |
| nginx | nginx | django |
| grafana | cos-grafana | prometheus, loki |
| prometheus | cos-prometheus | django (metrics endpoint) |
| loki | cos-loki | django (log push) |
| vault | vault | django |
| s3-integrator | s3-integrator | collector |

Document config options: `django-secret-key`, `netbox-url`, `netbox-token`, `pagerduty-token`, `debug`.
Document container images: all `is-cmdb-*` images are built from this repo's `Dockerfile`.

---

#### #63 — 7.2 Initialise is-cmdb-operator charm repo

Create the charm skeleton **inside this repo** under `charm/` (since the is-cmdb-operator repo doesn't exist yet):

```
charm/
  charmcraft.yaml
  metadata.yaml      # 12 containers + relations
  config.yaml
  src/
    charm.py
  lib/
    charms/          # placeholder
  tests/
    unit/
    integration/
```

`charmcraft.yaml`: `type: charm`, `bases: ubuntu-22.04`, `parts: charm`.

`metadata.yaml`: name `is-cmdb`, display-name `IS CMDB`, containers for all 12 apps.

`config.yaml`: all config options from #62.

`src/charm.py`: minimal skeleton — `ISCmdbCharm(CharmBase)` class with `__init__` registering `_on_pebble_ready`.

---

#### #64 — 7.3 Implement django app workload in charm

In `charm/src/charm.py`, implement the django workload fully:

```python
def _on_pebble_ready(self, event):
    container = event.workload
    layer = {
        "services": {
            "django": {
                "override": "replace",
                "command": "gunicorn cmdb.wsgi:application --bind 0.0.0.0:8000",
                "startup": "enabled",
                "environment": {
                    "SECRET_KEY": self.config["django-secret-key"],
                    "DATABASE_URL": self._get_database_url(),
                    "REDIS_URL": self._get_redis_url(),
                    "DEBUG": str(self.config.get("debug", False)),
                }
            }
        }
    }
    container.add_layer("django", layer, combine=True)
    container.autostart()

def _on_postgresql_database_created(self, event):
    self.unit.status = MaintenanceStatus("Running migrations")
    # store DATABASE_URL in peer relation data
    self._stored.database_url = event.master.uri
    container = self.unit.get_container("django")
    container.exec(["python", "manage.py", "migrate"]).wait()

def _on_redis_relation_joined(self, event):
    self._stored.redis_url = f"redis://{event.relation.data[event.app]['hostname']}:{event.relation.data[event.app]['port']}/0"
```

Add unit tests under `charm/tests/unit/test_charm.py` using `ops.testing.Harness`.

---

## Fixtures

Sample Juju model data goes in `tests/fixtures/juju/`. Create one JSON file per cloud (e.g. `ps5.json`, `ps6.json`, `scalingstack.json`) with the schema:

```json
{
  "cloud": "ps5",
  "models": [
    {
      "name": "ps5-prod-launchpad",
      "controller": "ps5-controller",
      "machines": [
        {"id": "0", "hostname": "ps5-node-01", "series": "jammy", "units": ["postgresql/0", "vault/0"]}
      ]
    }
  ]
}
```

Populate with representative fake data matching the environment names already in the DB.

---

## What is mocked vs real

| Item | Status |
|------|--------|
| Netbox API reads | REAL — use token from .env |
| PagerDuty API reads | REAL — use token from .env |
| PagerDuty maintenance window create/cancel | MOCK — `PAGERDUTY_WRITE_TOKEN` not available yet |
| Mattermost DM | REAL if `MATTERMOST_TOKEN` set, otherwise log-only |
| Email sending | MOCK — log-only until `EMAIL_HOST` is set |
| RadosGW bucket ingestion | STUB — no credentials yet |

---

## Issues explicitly excluded from this session

- **#25** (3.5 LLDP neighbour discovery) — deferred, left for later
- **#40** (5.2 Switch failure impact query) — dropped entirely

---

## Run / test commands

```bash
cd /home/fer/projects/is-cmdb
source .venv/bin/activate   # or conda activate
python cmdb/manage.py migrate
python cmdb/manage.py runserver 0.0.0.0:8000

# Run tests
pytest tests/
```

Django manage.py is at `cmdb/manage.py`, not the repo root.

---

## Final reminders

1. Create the branch first, before any file changes.
2. Do not merge into `main`.
3. Do not close any GitHub issue.
4. Every new Django app needs to be added to `INSTALLED_APPS` in `cmdb/settings.py`.
5. Every new URL module needs to be included in either `cmdb/urls.py` or the relevant app's `urls.py`.
6. Run `python cmdb/manage.py makemigrations` and `migrate` after each new model.
7. Type hints on all function signatures. No `print()`.
