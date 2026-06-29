#!/usr/bin/env python3
"""
One-shot read-only audit of PagerDuty for IS-CMDB issue #30.

Gathers the IS and IS 24x7 teams, their services, current on-calls, and the
maintenance-window schema, plus a juju-model -> service name-match analysis.
Caches the digest to ``/tmp/pd_explore.json``.

Run::

    DJANGO_SETTINGS_MODULE=cmdb.settings python scripts/explore_pagerduty.py
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

import django

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cmdb.settings")
django.setup()

from cmdb.integrations.pagerduty_client import PagerDutyClient  # noqa: E402
from scripts.analyze_juju_fixtures import CLOUDS, FIXTURE_DIR, parse_table  # noqa: E402
from scripts.match_k8s_clusters import model_token  # noqa: E402

CACHE = Path("/tmp/pd_explore.json")
TARGET_TEAMS = {"IS", "IS 24x7"}


def main() -> None:
    pd = PagerDutyClient()
    out: dict = {}

    # --- Teams ---------------------------------------------------------------
    teams = list(pd.paginate("teams", "teams"))
    out["team_total"] = len(teams)
    target = [t for t in teams if t["name"] in TARGET_TEAMS]
    # Fallback: case-insensitive / startswith if exact names differ.
    if len(target) < 2:
        target = [t for t in teams if t["name"].upper().startswith("IS")]
    out["target_teams"] = [{"id": t["id"], "name": t["name"]} for t in target]
    target_ids = {t["id"] for t in target}
    print(f"teams total: {len(teams)}; IS-ish teams: {[t['name'] for t in target]}")

    # --- Services for the target teams --------------------------------------
    services = list(
        pd.paginate(
            "services", "services",
            params={"include[]": "teams", "team_ids[]": list(target_ids)}
            if target_ids else {"include[]": "teams"},
        )
    )
    svc_rows = []
    for s in services:
        s_team_ids = {t["id"] for t in s.get("teams", [])}
        if target_ids and not (s_team_ids & target_ids):
            continue
        team_names = [t["name"] for t in s.get("teams", []) if t["id"] in target_ids] \
            or [t["name"] for t in s.get("teams", [])]
        svc_rows.append({
            "id": s["id"], "name": s["name"],
            "status": s.get("status"), "teams": team_names,
        })
    out["service_count"] = len(svc_rows)
    out["services"] = svc_rows
    print(f"services for IS/IS-24x7: {len(svc_rows)}")

    # --- On-calls ------------------------------------------------------------
    try:
        oncalls = list(
            pd.paginate(
                "oncalls", "oncalls",
                params={"team_ids[]": list(target_ids)} if target_ids else None,
                max_pages=3,
            )
        )
        out["oncalls"] = [
            {
                "escalation_level": o.get("escalation_level"),
                "user": (o.get("user") or {}).get("summary"),
                "schedule": (o.get("schedule") or {}).get("summary"),
                "escalation_policy": (o.get("escalation_policy") or {}).get("summary"),
            }
            for o in oncalls
        ]
    except Exception as exc:  # noqa: BLE001
        out["oncalls"] = f"error: {exc!r}"
    print(f"oncalls: {len(out['oncalls']) if isinstance(out['oncalls'], list) else out['oncalls']}")

    # --- Maintenance window schema ------------------------------------------
    try:
        mw = pd.get("maintenance_windows", {"limit": 10})
        windows = mw.get("maintenance_windows", [])
        out["maintenance_window_total"] = mw.get("total")
        out["maintenance_window_sample_keys"] = sorted(windows[0].keys()) if windows else []
        out["maintenance_window_sample"] = windows[0] if windows else None
    except Exception as exc:  # noqa: BLE001
        out["maintenance_window_error"] = repr(exc)
    print(f"maintenance window sample keys: {out.get('maintenance_window_sample_keys')}")

    # --- juju model -> service name match -----------------------------------
    tokens: Counter = Counter()
    for cloud in CLOUDS:
        path = FIXTURE_DIR / f"{cloud}.txt"
        if not path.exists():
            continue
        for inst in parse_table(path, cloud):
            if inst.status != "ACTIVE":
                continue
            tok = model_token(inst.name)
            if tok and len(tok) >= 5:
                tokens[tok] += 1
    svc_names = [s["name"].lower() for s in svc_rows]
    matches = []
    for tok in tokens:
        tl = tok.lower()
        for s in svc_rows:
            sl = s["name"].lower()
            if tl == sl or tl in sl or sl in tl:
                matches.append({"model": tok, "service": s["name"], "service_id": s["id"]})
                break
    out["model_service_matches"] = matches
    out["distinct_models"] = len(tokens)
    print(f"distinct juju models: {len(tokens)}; name-matched to PD services: {len(matches)}")

    CACHE.write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(f"cached -> {CACHE}")


if __name__ == "__main__":
    main()
