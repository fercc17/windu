# Netbox / placement audit

Living audit document. Sources:

- Live Netbox **v4.0.6** at `netbox.staging.admin.canonical.com/stg-netbox-k8s-netbox`,
  read-only token, audited **2026-06-08** (`scripts/explore_netbox.py`).
- OpenStack `server list` fixtures under `tests/fixtures/juju/` for ps5/ps6/ps7
  (`scripts/analyze_juju_fixtures.py`, `scripts/match_k8s_clusters.py`).

> The full device / role / interface / cable inventory (issue #21) is in
> [§3 Netbox device data](#3-netbox-device-data). §1–§2 and §4 are produced by
> Task 0.

---

## 1. Availability Zone modelling — **AZ is NOT in Netbox**

OpenStack schedules instances into AZs (`availability-zone-1`, `AZ1`,
`availability-zone-z15`, …) but Netbox models none of it:

| Checked | Result |
|---------|--------|
| `dcim.device` custom fields | only `purchase_date`, `refresh_date` |
| `dcim.site` custom fields | none |
| `dcim/locations/` | 1 entry (`greece`), unrelated |
| custom-field defs (`extras/custom-fields/`) | `purchase_date`, `refresh_date` only |
| region / rack-group encodes AZ | no |

→ The AZ↔host mapping is therefore recorded in
[`az-node-mapping.json`](az-node-mapping.json) (keyed `cloud → hostname → az`),
and a patch plan to add it to Netbox is in
[`netbox-az-patch.md`](netbox-az-patch.md), ready for when a write token exists.

## 2. Cloud ↔ Netbox site mapping

Derived from device-name prefixes (`scripts/explore_netbox.py`). OpenStack
"clouds" are not Netbox sites; the link is by hostname prefix:

| Cloud | Netbox site | Region | Evidence (device names) |
|-------|-------------|--------|-------------------------|
| ps5 | `il3` | europe | `Ps5-Ra1-N1`, `ps5-infra2`, `ps5-leaf-a1` |
| ps6 | `csb-cage02` | NorthAmerica | `ps6-100g-a2`, `ps6-infra*` |
| ps7 | `drs` | europe | `ps7-infra1`, `ps7-ra*` |
| ps8 | `vl2` | europe | `ps8-infra-1` |
| edge-tel | `tel` | asia | `tel-is-core1` |
| edge-et3 | `tor3` | NorthAmerica | `et3-core1` |
| microcloud-drs | `drs` | europe | shares the `drs` site with ps7 |

Other sites (`bjp`, `mlr`, `tmo`, `tor5`, `csb-cage01`, `remote`) hold network /
corporate gear not directly tied to a ps-cloud in the CMDB.

> ⚠️ **Name mismatch for placement linking (#26):** OpenStack hosts are
> lowercase + domain (`ps5-ra1-n1.maas`); Netbox devices are mixed-case, no
> domain (`Ps5-Ra1-N1`). Match case-insensitively after stripping the domain.

---

## 3. Netbox device data (issue #21)

Reproduce with `python scripts/explore_netbox.py` (caches to
`/tmp/netbox_explore.json`).

### Sites (12)

| Slug | Region | Devices | Custom fields |
|------|--------|--------:|---------------|
| tor3 | NorthAmerica | 226 | — |
| vl2 | europe | 267 | — |
| il3 | europe | 123 | — |
| csb-cage02 | NorthAmerica | 102 | — |
| csb-cage01 | NorthAmerica | 100 | — |
| drs | europe | 86 | — |
| mlr | europe | 82 | — |
| tel | asia | 74 | — |
| bjp | asia | 29 | — |
| tmo | asia | 20 | — |
| remote | — | 1 | — |
| tor5 | NorthAmerica | 0 | — |

### Device roles & counts (1110 devices total)

| Role (slug) | Count |
|-------------|------:|
| server | 926 |
| misc | 141 |
| storage | 37 |
| scs_console | 4 |
| hypervisor | 1 |
| corporate-laptop | 1 |

> **There is no dedicated `switch` role.** Network switches live under `server`
> or `misc` and are only recognisable by name (`*-aggsw*`, `*-leaf-*`, `*-core*`,
> `*-fabric-sw*`, `*-tor*`). This matters for #22 (`Node.role`) and #39 (switch
> graph). All sampled devices have `status = active`.

### Device fields available

`id, name, device_type, role, site, rack, location, position, status,
primary_ip / primary_ip4 / primary_ip6, oob_ip, serial, asset_tag, platform,
tenant, interface_count, virtual_chassis, cluster, tags, custom_fields,
created, last_updated`.

These map cleanly onto the `Node` model (#22): `netbox_id=id`,
`hostname=name`, `site=site.slug`, `cloud=` derived from name prefix
(see §2), `role=role.slug`, `rack=rack.name`, `status=status.value`,
`primary_ip=primary_ip.address`.

### Custom fields

Only two, both defined on `dcim.device`, both empty in the sample:

| Name | Type |
|------|------|
| purchase_date | date |
| refresh_date | date |

### Interfaces — **EMPTY**

`dcim/interfaces/` total count = **0**; every device reports
`interface_count = 0`. No MAC / speed / cable-attachment data exists.

### Cables — **EMPTY**

`dcim/cables/` total count = **0**. No switch-uplink or layer-1 topology data.

### Implications for the build

- `Node` (#22/#24) can be fully populated from `dcim/devices/`.
- `NodeInterface` / `NodeCable` (#22) will be **empty** after reconciliation —
  there is nothing to sync.
- `physical_completeness` (#24) = `interfaces_with_cables / total_interfaces`;
  with 0 interfaces it defaults to **0.0**, so every node renders the red
  "incomplete physical data" badge — which correctly reflects that Netbox holds
  no layer-1 data, not a CMDB bug.
- `uplink_redundancy` (#39) cannot be computed (no cables) → `False` everywhere;
  the **switch graph is a documented stub** (handoff's "cable data absent" branch).

---

## 4. Juju fixture analysis (Task 0)

Parsed from `tests/fixtures/juju/{ps5,ps6,ps7}.txt` (raw `openstack server list
--long`). Only **ACTIVE** instances with a real host are counted.

### Availability zones & node counts

| Cloud | ACTIVE inst | AZs | Hosts/AZ | Total hosts | Host domain |
|-------|------------:|-----|----------|------------:|-------------|
| ps5 | 872 | `availability-zone-1/2/3` | 14 / 14 / 14 | 42 | `.maas` |
| ps6 | 945 | `availability-zone-1/2/3` + `availability-zone-z15` | 10 / 11 / 10 / 6 | 37 | `.ps6.canonical.com` |
| ps7 | 954 | `AZ1/AZ2/AZ3` | 19 / 20 / 19 | 58 | `.ps7.canonical.com` |

- AZ naming is **inconsistent across clouds** (`availability-zone-N` vs `AZN`).
- ps6 has a dedicated **s390x** zone `availability-zone-z15` (hosts `ps6-s390x-n*`).
- Host naming: `<cloud>-r{a,b}<rack>-[<arch>-]n<n>` (arch token appears for
  arm64 / s390x / ppc64el / riscv64 hosts).

### Flavors seen per cloud (top)

- **ps5:** `stag-cpu2-ram4-disk20` (171), `vbuilder` (122), `prod-cpu2-ram4-disk20`
  (74), `prod-cpu8-ram8-disk100` (46), `builder-cpu8-ram32-disk100` (24).
- **ps6:** `charm-octavia-huge` (217), `staging-cpu1-ram2-disk20` (216),
  `vbuilder-arm64-large` (114), `vbuilder-large` (76), `vbuilder-ppc64el` (44).
- **ps7:** `seg-reproducer-cpu1-ram2-disk20-amd64` (116),
  `github-runner-cpu8-ram32-disk100-amd64` (86), `shared.xlarge[.arm64/.riscv64]`,
  `seg-reproducer-cpu2-ram4-disk20-amd64` (51).

### Kubernetes cluster matches

Method: extract the juju **model token** from each instance name
(`juju-<uuid6>-<model>-<machine>` → `<model>`; non-juju names → strip trailing
`-N`), then match against the **107** `service_class='kubernetes_cluster'`
environments (ps7 = 77, ps6 = 27, microcloud-drs = 3; ps5 = 0). Matches accepted
only on exact / cloud-suffix-normalised name equality — never on a generic token.

**11 clusters confidently matched** (all spread across all three AZs → AZ-resilient):

| Environment | Cloud | Instances | Distinct hosts | AZs |
|-------------|-------|----------:|---------------:|-----|
| k8s-candidate-livepatch-backend | ps7 | 7 | 3 | AZ1–3 |
| k8s-edge-livepatch-backend | ps7 | 7 | 5 | AZ1–3 |
| k8s-stable-livepatch-backend | ps7 | 7 | 4 | AZ1–3 |
| k8s-jaborvs-ps7 | ps7 | 7 | 5 | AZ1–3 |
| k8s-pfe-ps7-prod | ps7 | 7 | 5 | AZ1–3 |
| k8s-prod-comsys-ecommerce | ps7 | 7 | 5 | AZ1–3 |
| k8s-prod-documentation-ps7 | ps7 | 7 | 4 | AZ1–3 |
| k8s-prod-ubuntu-pro-ps7 | ps7 | 7 | 4 | AZ1–3 |
| k8s-stg-ubuntu-pro-ps7 | ps7 | 7 | 4 | AZ1–3 |
| k8s-ps7-ci-o11y | ps7 | 7 | 3 | AZ1–3 |
| k8s-stg-launchpad-cos | ps7 | 7 | 6 | AZ1–3 |

**What could NOT be determined (96 of 107):**

- **ps6 clusters mostly use the bare juju model name `k8s`** (24 instances on 6
  hosts share it) → the instance name does not identify *which* cluster; only the
  juju model UUID does, which is absent from the fixtures. Same for the generic
  `cos` token on ps7 (3 instances). These are reported as ambiguous, not guessed.
- The fixtures are a **~1000-row sample per cloud**, so many clusters' machines
  simply fall outside the snapshot — absence of a match is not absence of the
  cluster.
- ps5 has microk8s embedded inside non-`kubernetes_cluster` environments
  (e.g. `prod-is-kubernetes`, `prod-is-temporal-hra-microk8s-ps5`) which are
  classed `machine_model`/`container_model`, so they are out of scope for this
  match.

Regenerate: `python scripts/match_k8s_clusters.py` (JSON) /
`python scripts/analyze_juju_fixtures.py --emit` (mapping).
