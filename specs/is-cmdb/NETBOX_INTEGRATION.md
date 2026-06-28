# Netbox Integration

## Overview

Netbox is the source of truth for physical devices in the Canonical IS infrastructure. IS-CMDB does not replace Netbox. It reads from Netbox to enrich environment records with physical context: which node each environment runs on, which rack that node is in, which switch that node connects to, and whether redundant physical paths exist.

The integration has two components: a real-time webhook receiver that processes device lifecycle events as they happen, and a nightly reconciliation job that performs a full diff between Netbox and the CMDB as a safety net.

## Data flow

Netbox pushes. IS-CMDB receives. IS-CMDB does not write back to Netbox.

```
Netbox (device created/updated/deleted)
  → HTTP POST to /api/webhooks/netbox/
    → webhook receiver validates HMAC-SHA512 signature
      → upserts/updates nodes table in PostgreSQL
        → node is now available for poller to link to environments

Nightly CronJob (03:00 UTC)
  → GET /api/dcim/devices/?limit=1000 (paginated)
    → full diff against nodes table
      → upserts any missed devices
        → updates physical_completeness flag per cloud
```

## Webhook receiver

### URL

```
POST /api/webhooks/netbox/
```

### Authentication

Netbox signs webhook payloads with HMAC-SHA512 using a secret token. The receiver validates this signature before processing any payload.

```python
import hmac
import hashlib

def validate_netbox_signature(request) -> bool:
    secret = settings.NETBOX_WEBHOOK_SECRET.encode()
    signature = request.headers.get("X-Hook-Signature", "")
    body = request.body
    expected = hmac.new(secret, body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(signature, expected)
```

Return 400 immediately if signature validation fails. Log the failure with the source IP.

### Supported events

The receiver handles events on `dcim.device` only. All device roles are accepted without filtering.

| Event | Action |
|---|---|
| created | INSERT into nodes table, status=active |
| updated | UPDATE nodes WHERE netbox_id=..., set last_synced_at=NOW() |
| deleted | UPDATE nodes SET status=decommissioning. Never DELETE. |

### Netbox webhook payload structure

```json
{
  "event": "created",
  "model": "dcim.device",
  "data": {
    "id": 42,
    "name": "node-3.maas.canonical.com",
    "status": {"value": "active"},
    "device_role": {"name": "Server"},
    "site": {"name": "amer-cloud-01"},
    "rack": {"name": "rack-7"},
    "position": 12,
    "primary_ip": {"address": "10.0.1.3/24"},
    "custom_fields": {}
  }
}
```

### Receiver behaviour

```
1. Validate HMAC-SHA512 signature. Return 400 on failure.
2. Check model == dcim.device. Return 200 and skip if other model.
3. Extract: netbox_id, hostname (data.name), ip_address, device_role,
   site name, rack name, rack_unit (position), primary_ip
4. Resolve cloud_id: Cloud.objects.filter(name=site_name).first()
   If not found: create node with null cloud_id and log a warning.
   Do not block the insert.
5. On event=created: INSERT node with status=active, sync_source=webhook
6. On event=updated: UPDATE node fields, set last_synced_at=NOW()
7. On event=deleted: UPDATE node SET status=decommissioning
8. Return 200 OK
```

Return 200 immediately. If processing takes more than 200ms, queue it asynchronously and return 200 first.

### Error handling

- Invalid signature: 400, log source IP, do not process
- Valid payload but processing error: return 200 (Netbox will retry on non-2xx), log error internally, alert via monitoring
- Unknown site (cloud not found): create node with null cloud_id, log warning at INFO level, continue normally

## Nightly reconciliation

### Management command

```
python manage.py reconcile_netbox
```

Schedule as a Kubernetes CronJob at 03:00 UTC.

### Behaviour

```
1. GET /api/dcim/devices/?limit=1000 from Netbox API, paginate with offset
2. For each device returned:
   a. Upsert node record (INSERT ON CONFLICT DO UPDATE)
   b. Set sync_source=poll, last_synced_at=NOW()
   c. Resolve cloud_id from site name
3. For each node in CMDB where sync_source=webhook and
   last_synced_at < 2 days ago: log warning "node {hostname} not seen in Netbox"
4. Update physical_completeness on each Cloud:
   a. full: all nodes in cloud have at least one NodeInterface record
      AND at least one NodeCable record
   b. partial: nodes have interfaces but no cables
   c. none: nodes have neither interfaces nor cables
5. Log summary: N upserted, M warnings, K clouds updated
```

### Netbox API authentication

```
GET https://{netbox_host}/api/dcim/devices/?limit=1000&offset=0
Authorization: Token {NETBOX_API_TOKEN}
Content-Type: application/json
```

## LLDP neighbour discovery

### Purpose

Cable and interface data in Netbox varies by cloud. Where cable data is missing, the physical_completeness flag is set to partial or none, and resilience queries return qualified results. The LLDP discovery script populates cable data without requiring physical access to the data centre.

### Script

```
tools/lldp_to_netbox.py
```

This is a one-time or periodic tool, not a CronJob. Run manually when a new cloud is provisioned or when cable data needs refreshing.

### Behaviour

```
1. Read switch list from tools/lldp-switches.yaml
2. For each switch: SSH using paramiko, run LLDP neighbour command
3. Parse output: local_port, remote_hostname, remote_port per neighbour
4. For each server-to-switch connection found:
   a. Look up server device in Netbox by hostname
   b. Look up switch device in Netbox by hostname
   c. GET /api/dcim/interfaces/?device_id=... to check if interface exists
   d. If not: POST /api/dcim/interfaces/ to create it
   e. GET /api/dcim/cables/?termination_a_id=... to check if cable exists
   f. If not: POST /api/dcim/cables/ to create it
5. Log: N cables created, M already existed, K skipped (device not in Netbox)
```

### Config file: tools/lldp-switches.yaml

```yaml
switches:
  - hostname: switch-a.amer-cloud-01.canonical.com
    vendor: arista
    credentials_env: SWITCH_A_PASSWORD
    lldp_command: "show lldp neighbors detail"
  - hostname: switch-b.amer-cloud-01.canonical.com
    vendor: juniper
    credentials_env: SWITCH_B_PASSWORD
    lldp_command: "show lldp neighbors"
```

### Vendor notes

Arista switches: `show lldp neighbors detail` returns structured output. Parse with regex for `Interface` (local port) and `System Name` (remote hostname).

Juniper switches: `show lldp neighbors` returns tabular output. Parse columns for local interface and system name.

If a vendor is not in the supported list, log a warning and skip that switch.

## Physical completeness

Each cloud has a `physical_completeness` field that affects how the UI presents resilience query results.

| Value | Meaning | UI treatment |
|---|---|---|
| full | All nodes have interface and cable records in Netbox | Resilience queries are authoritative |
| partial | Nodes have interface records but no cable records | Resilience queries show amber warning: connectivity inferred, not confirmed |
| none | Nodes have no interface or cable records | Resilience queries show red warning: physical dependency data unavailable |

The nightly reconciliation job recalculates this flag for all clouds on every run.

## Switch resilience model

For active/active clouds (the majority of Canonical clouds), each server node connects to two switches on different uplinks. The IS-CMDB models this in the `node_switch_connections` table, populated by `python manage.py build_switch_graph` from NodeCable records.

When a switch failure is simulated via the resilience API:

- Node has connections to 2 or more switches: result is `redundancy_degraded` (traffic fails over, no outage)
- Node has connection to only 1 switch: result is `offline` (node loses connectivity)

The single active/passive cloud is flagged in Cloud.notes as a temporary state. Treat it as a single-uplink environment for resilience calculations until it is migrated to active/active.

## Environment variables

| Variable | Description |
|---|---|
| `NETBOX_WEBHOOK_SECRET` | HMAC-SHA512 secret configured in Netbox webhook settings |
| `NETBOX_API_TOKEN` | Netbox API token for the reconciliation job |
| `NETBOX_HOST` | Netbox instance hostname, e.g. netbox.canonical.com |

## Netbox webhook configuration

Configure in Netbox under Administration > Webhooks:

- Name: IS-CMDB device sync
- Object types: dcim.device
- Events: create, update, delete
- URL: https://{cmdb-host}/api/webhooks/netbox/
- HTTP method: POST
- HTTP content type: application/json
- Secret: value of NETBOX_WEBHOOK_SECRET
- SSL verification: enabled

## References

- Netbox webhook documentation: https://docs.netbox.dev/en/stable/integrations/webhooks/
- Netbox REST API: https://docs.netbox.dev/en/stable/integrations/rest-api/
- python-paramiko (SSH): https://www.paramiko.org/
- LLDP (Link Layer Discovery Protocol): IEEE 802.1AB
