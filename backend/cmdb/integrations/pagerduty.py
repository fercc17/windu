"""
PagerDuty maintenance-window operations.

Create / cancel maintenance windows on the **IS team** (``PQ4ZG3S``) services.
Per the #30 audit correction, IS services are account/team-wide alerting
pipelines with no per-cloud granularity, so a maintenance window silences a
fixed, configurable set of IS services regardless of which cloud/node it targets
(it cannot silence "ps7 only"). Env-scoped silencing of a single juju model goes
through COS/Alertmanager instead (see ``cos.py``), because PD windows are
service-scoped.

Requires a write-capable token ``PAGERDUTY_WRITE_TOKEN`` (the ``.env``
``PAGERDUTY_API_TOKEN`` is read-only). When the write token is unset these
functions log a warning and no-op so the UI flow degrades gracefully.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Optional

import requests

if TYPE_CHECKING:  # avoid importing models at module load
    from cmdb.apps.maintenance.models import MaintenanceWindow

logger = logging.getLogger(__name__)

PD_API = "https://api.pagerduty.com"
IS_TEAM_ID = "PQ4ZG3S"  # https://canonical.pagerduty.com/teams/PQ4ZG3S/users

# Default IS-team services a maintenance window silences (see #30 audit).
# Override with PAGERDUTY_MW_SERVICE_IDS (comma-separated service ids).
DEFAULT_MW_SERVICE_IDS = (
    "P0KBH6J",  # Batphone Alert
    "PJ5D40R",  # Support to IS - Alert
    "PT6U7AJ",  # Site24x7
)


def mw_service_ids() -> list[str]:
    """Resolve the IS-team service ids a maintenance window silences."""
    raw = os.environ.get("PAGERDUTY_MW_SERVICE_IDS", "").strip()
    if raw:
        return [s.strip() for s in raw.split(",") if s.strip()]
    return list(DEFAULT_MW_SERVICE_IDS)


def _headers(token: str, *, from_email: Optional[str] = None) -> dict:
    headers = {
        "Authorization": f"Token token={token}",
        "Content-Type": "application/json",
        "Accept": "application/vnd.pagerduty+json;version=2",
    }
    if from_email:
        headers["From"] = from_email
    return headers


def create_maintenance_window(window: "MaintenanceWindow") -> Optional[str]:
    """
    Create a PD maintenance window silencing the configured IS-team services
    (``mw_service_ids()``) for the window's time range. Returns the PD window
    id, or ``None`` if ``PAGERDUTY_WRITE_TOKEN`` is unset.

    NOTE: PD windows are service-scoped, so this silences IS alerting team-wide
    for the duration — it cannot target a single cloud or juju model. Env-scoped
    silencing of one model goes through COS (see ``cos.py``).
    """
    token = os.environ.get("PAGERDUTY_WRITE_TOKEN")
    if not token:
        logger.warning(
            "PAGERDUTY_WRITE_TOKEN not set — skipping PD maintenance window creation"
        )
        return None

    service_ids = mw_service_ids()
    if not service_ids:
        logger.warning("no PD maintenance-window service ids configured — skipping")
        return None

    resp = requests.post(
        f"{PD_API}/maintenance_windows",
        headers=_headers(token, from_email=window.created_by or "is-cmdb@canonical.com"),
        json={
            "maintenance_window": {
                "type": "maintenance_window",
                "start_time": window.starts_at.isoformat(),
                "end_time": window.ends_at.isoformat(),
                "description": f"IS-CMDB: {window.target_label} — {window.reason}",
                "services": [
                    {"id": sid, "type": "service_reference"} for sid in service_ids
                ],
            }
        },
        timeout=30,
    )
    resp.raise_for_status()
    pd_id = resp.json()["maintenance_window"]["id"]
    logger.info(
        "created PD maintenance window %s (%d service(s)) for %s",
        pd_id, len(service_ids), window.target_label,
    )
    return pd_id


def cancel_maintenance_window(pd_window_id: str) -> bool:
    """DELETE /maintenance_windows/{id}. Requires ``PAGERDUTY_WRITE_TOKEN``."""
    token = os.environ.get("PAGERDUTY_WRITE_TOKEN")
    if not token:
        logger.warning("PAGERDUTY_WRITE_TOKEN not set — skipping PD cancellation")
        return False

    resp = requests.delete(
        f"{PD_API}/maintenance_windows/{pd_window_id}",
        headers=_headers(token),
        timeout=30,
    )
    resp.raise_for_status()
    logger.info("cancelled PD maintenance window %s", pd_window_id)
    return resp.status_code == 204
