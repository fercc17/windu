#!/usr/bin/env python3
"""
One-shot read-only exploration of the live Netbox instance.

Gathers everything needed to write ``docs/findings/netbox-audit.md`` (issue #21)
and to answer Task 0's "is Availability Zone modelled in Netbox?" question. The
raw digest is cached to ``/tmp/netbox_explore.json`` so the audit docs can be
written without re-hitting the API.

Run::

    DJANGO_SETTINGS_MODULE=cmdb.settings python scripts/explore_netbox.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import django

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cmdb.settings")
django.setup()

from cmdb.integrations.netbox_client import NetboxClient  # noqa: E402

CACHE = Path("/tmp/netbox_explore.json")
AZ_HINT = re.compile(r"(availability|zone|^az$|_az$|-az$| az )", re.IGNORECASE)


def main() -> None:
    nb = NetboxClient()
    out: dict = {}

    # --- Sites (12 expected) -------------------------------------------------
    sites = list(nb.paginate("dcim/sites/"))
    out["sites"] = [
        {
            "name": s["name"], "slug": s["slug"],
            "region": (s.get("region") or {}).get("slug") if s.get("region") else None,
            "device_count": s.get("device_count"),
            "status": (s.get("status") or {}).get("value"),
            "custom_fields": s.get("custom_fields", {}),
        }
        for s in sites
    ]

    # --- Device roles --------------------------------------------------------
    roles = list(nb.paginate("dcim/device-roles/"))
    out["device_roles"] = [
        {"name": r["name"], "slug": r["slug"], "device_count": r.get("device_count")}
        for r in roles
    ]

    # --- Device totals + per-role counts ------------------------------------
    out["device_total"] = nb.count("dcim/devices/")
    role_counts = {}
    for r in roles:
        role_counts[r["slug"]] = nb.count("dcim/devices/", {"role": r["slug"]})
    out["device_count_by_role"] = role_counts

    # --- Sample 100 devices to learn the shape ------------------------------
    devices = list(nb.paginate("dcim/devices/", {"limit": 100}, max_pages=1))
    cf_keys: Counter = Counter()
    statuses: Counter = Counter()
    role_seen: Counter = Counter()
    site_seen: Counter = Counter()
    for d in devices:
        statuses[(d.get("status") or {}).get("value")] += 1
        role_seen[((d.get("role") or {}) or {}).get("slug")] += 1
        site_seen[((d.get("site") or {}) or {}).get("slug")] += 1
        for k in (d.get("custom_fields") or {}):
            cf_keys[k] += 1
    sample = devices[0] if devices else {}
    out["device_field_keys"] = sorted(sample.keys())
    out["device_sample"] = {
        "name": sample.get("name"),
        "role": ((sample.get("role") or {}) or {}).get("slug"),
        "site": ((sample.get("site") or {}) or {}).get("slug"),
        "status": (sample.get("status") or {}).get("value"),
        "primary_ip": ((sample.get("primary_ip") or {}) or {}).get("address"),
        "custom_fields": sample.get("custom_fields", {}),
    }
    out["device_custom_field_keys"] = dict(cf_keys)
    out["device_sample_statuses"] = dict(statuses)

    # --- Interfaces schema + count ------------------------------------------
    out["interface_total"] = nb.count("dcim/interfaces/")
    ifaces = list(nb.paginate("dcim/interfaces/", {"limit": 5}, max_pages=1))
    out["interface_field_keys"] = sorted(ifaces[0].keys()) if ifaces else []
    out["interface_sample"] = (
        {k: ifaces[0].get(k) for k in
         ("name", "type", "mac_address", "speed", "cable", "device")}
        if ifaces else {}
    )

    # --- Cables schema + count (key question: is uplink data populated?) ----
    out["cable_total"] = nb.count("dcim/cables/")
    cables = list(nb.paginate("dcim/cables/", {"limit": 5}, max_pages=1))
    out["cable_field_keys"] = sorted(cables[0].keys()) if cables else []
    out["cable_sample"] = cables[0] if cables else {}

    # --- Locations (could model AZ) -----------------------------------------
    locations = list(nb.paginate("dcim/locations/"))
    out["locations"] = [
        {"name": loc["name"], "slug": loc["slug"],
         "site": ((loc.get("site") or {}) or {}).get("slug")}
        for loc in locations
    ]
    out["location_total"] = len(locations)

    # --- Custom field definitions (authoritative AZ check) ------------------
    try:
        cfs = list(nb.paginate("extras/custom-fields/"))
        out["custom_field_defs"] = [
            {"name": c.get("name"), "type": (c.get("type") or {}).get("value"),
             "object_types": c.get("object_types") or c.get("content_types")}
            for c in cfs
        ]
    except Exception as exc:  # noqa: BLE001
        out["custom_field_defs"] = f"error: {exc!r}"

    # --- AZ modelling verdict -----------------------------------------------
    az_signals = []
    for c in (out.get("custom_field_defs") or []):
        if isinstance(c, dict) and c.get("name") and AZ_HINT.search(c["name"]):
            az_signals.append(f"custom-field:{c['name']}")
    for loc in out["locations"]:
        if AZ_HINT.search(loc["name"]) or AZ_HINT.search(loc["slug"]):
            az_signals.append(f"location:{loc['slug']}")
    for k in cf_keys:
        if AZ_HINT.search(k):
            az_signals.append(f"device-cf:{k}")
    out["az_signals"] = sorted(set(az_signals))

    CACHE.write_text(json.dumps(out, indent=2, default=str) + "\n")

    # --- Print digest --------------------------------------------------------
    print(f"sites: {len(out['sites'])}")
    for s in out["sites"]:
        print(f"   {s['slug']:<24} region={s['region']} devices={s['device_count']} cf={list(s['custom_fields'])}")
    print(f"\ndevice_total: {out['device_total']}")
    print("device_count_by_role:")
    for slug, n in sorted(role_counts.items(), key=lambda x: -x[1]):
        print(f"   {slug:<28} {n}")
    print(f"\ndevice custom_field keys (sample of 100): {out['device_custom_field_keys']}")
    print(f"device sample custom_fields: {out['device_sample']['custom_fields']}")
    print(f"\ninterface_total: {out['interface_total']}; keys: {out['interface_field_keys']}")
    print(f"interface sample: {out['interface_sample']}")
    print(f"\ncable_total: {out['cable_total']}; keys: {out['cable_field_keys']}")
    print(f"\nlocations: {out['location_total']} -> {[l['slug'] for l in out['locations']][:20]}")
    print(f"\ncustom_field_defs: {out['custom_field_defs'] if isinstance(out['custom_field_defs'], str) else [c['name'] for c in out['custom_field_defs']]}")
    print(f"\nAZ signals in Netbox: {out['az_signals'] or 'NONE — AZ is NOT modelled in Netbox'}")
    print(f"\ncached -> {CACHE}")


if __name__ == "__main__":
    main()
