# Netbox patch plan — add Availability Zone to devices

**Status:** proposed (not applied). Requires a **write-capable** Netbox token.
The token currently in `.env` is used read-only by IS-CMDB; no writes were made
while producing this document.

## Why

OpenStack scheduling places each instance in an **availability zone** (AZ), and
the AZ↔host assignment is operationally important (blast radius, maintenance
draining, capacity per failure domain). Today that mapping lives **only** in the
live `openstack server list` output — see [`az-node-mapping.json`](az-node-mapping.json).

A read-only audit of the live Netbox (v4.0.6) on 2026-06-08 confirms AZ is **not
modelled anywhere**:

- No custom field on `dcim.device` (only `purchase_date`, `refresh_date` exist).
- No custom field on `dcim.site`.
- `dcim/locations/` contains a single unrelated entry (`greece`).
- No site/region/rack-group encodes the OpenStack AZ name.

(Network gear names sometimes embed an AZ token, e.g. `OAM_TOR1_AZ1_RA1`, but
compute hosts such as `Ps5-Ra1-N1` do not, and there is no structured field.)

## Proposed change

Add a text custom field `availability_zone` to `dcim.device` and populate it from
`az-node-mapping.json`.

### 1. Create the custom field (once)

```http
POST {NETBOX_URL}extras/custom-fields/
Authorization: Token <WRITE_TOKEN>
Content-Type: application/json

{
  "object_types": ["dcim.device"],
  "name": "availability_zone",
  "label": "Availability Zone",
  "type": "text",
  "description": "OpenStack availability zone the host schedules into (source: openstack server list).",
  "required": false,
  "filter_logic": "loose",
  "group_name": "OpenStack"
}
```

> Netbox 4.x uses `object_types` (replacing the 3.x `content_types`). Values are
> app-label dotted, e.g. `dcim.device`.

### 2. Populate per device

For each `(cloud, hostname → az)` entry in `az-node-mapping.json`, resolve the
device id by name then PATCH its custom field. Note the **name mismatch** between
the OpenStack host (`ps5-ra1-n1.maas`) and the Netbox device (`Ps5-Ra1-N1`):
strip the domain suffix and match case-insensitively.

```http
GET {NETBOX_URL}dcim/devices/?name__ie=ps5-ra1-n1     # case-insensitive exact
PATCH {NETBOX_URL}dcim/devices/{id}/
{ "custom_fields": { "availability_zone": "availability-zone-1" } }
```

### 3. Verify

```http
GET {NETBOX_URL}dcim/devices/?cf_availability_zone=availability-zone-1
```

## Apply script (ready, gated on a write token)

A populate routine can reuse `cmdb/integrations/netbox_client.py`. It must **not**
run with the current read-only token. Suggested guard:

```python
if not os.environ.get("NETBOX_WRITE_TOKEN"):
    raise SystemExit("Refusing to write: set NETBOX_WRITE_TOKEN")
```

Counts to expect (from the fixtures): ps5 = 42 hosts, ps6 = 37, ps7 = 58.
