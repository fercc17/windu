"""
Site24x7 maintenance scheduling — SCAFFOLD.

Some services are also monitored by Site24x7, which has its own maintenance
concept (a Scheduled Maintenance, not an Alertmanager silence). When IS-CMDB
opens/closes a maintenance window we may also need to create/cancel a matching
Site24x7 maintenance so external checks don't page.

MOCKUP ONLY — no live calls. Configure via:

    SITE24X7_OAUTH_TOKEN     (or refresh-token flow; Site24x7 uses Zoho OAuth)
    SITE24X7_BASE_URL        (defaults to https://www.site24x7.com/api)

When unset these no-op and log. The real call shape is documented inline.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from cmdb.apps.maintenance.models import MaintenanceWindow

logger = logging.getLogger(__name__)


def _token() -> Optional[str]:
    return (os.environ.get("SITE24X7_OAUTH_TOKEN") or "").strip() or None


def _base_url() -> str:
    return (os.environ.get("SITE24X7_BASE_URL") or "https://www.site24x7.com/api").rstrip("/")


def create_maintenance(window: "MaintenanceWindow") -> Optional[str]:
    """Create a Site24x7 scheduled maintenance for the window. Returns its id,
    or None when Site24x7 is not configured."""
    if not _token():
        logger.warning("SITE24X7_OAUTH_TOKEN not set — skipping Site24x7 maintenance")
        return None
    # --- Ready for when credentials are available --------------------------
    # import requests
    # resp = requests.post(
    #     f"{_base_url()}/downtime_schedules",
    #     headers={"Authorization": f"Zoho-oauthtoken {_token()}",
    #              "Content-Type": "application/json;charset=UTF-8"},
    #     json={
    #         "display_name": f"IS-CMDB: {window.reason}"[:128],
    #         "maintenance_type": 1,          # one-time
    #         "start_time": window.starts_at.strftime("%Y-%m-%dT%H:%M:%S"),
    #         "end_time": window.ends_at.strftime("%Y-%m-%dT%H:%M:%S"),
    #         # "resource_type"/"monitors": resolve from the window's cloud/env.
    #     },
    #     timeout=30,
    # )
    # resp.raise_for_status()
    # return resp.json()["data"]["downtime_id"]
    raise NotImplementedError("Wire Site24x7 scheduled maintenance")


def cancel_maintenance(maintenance_id: str) -> bool:
    """Delete/cancel a Site24x7 scheduled maintenance. No-op when unconfigured."""
    if not _token():
        logger.warning("SITE24X7_OAUTH_TOKEN not set — skipping Site24x7 cancel")
        return False
    # import requests
    # resp = requests.delete(
    #     f"{_base_url()}/downtime_schedules/{maintenance_id}",
    #     headers={"Authorization": f"Zoho-oauthtoken {_token()}"}, timeout=30,
    # )
    # return resp.status_code in (200, 204)
    raise NotImplementedError("Wire Site24x7 scheduled maintenance cancel")
