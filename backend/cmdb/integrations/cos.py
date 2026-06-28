"""
COS / Alertmanager silence operations — PER CLOUD.

Each cloud runs its own Canonical Observability Stack (COS), so maintenance
silences must be sent to *that cloud's* Alertmanager. Endpoints/tokens are
resolved per cloud from the environment:

    COS_ALERTMANAGER_URL_<CLOUD>     e.g. COS_ALERTMANAGER_URL_PS6
    COS_ALERTMANAGER_TOKEN_<CLOUD>   e.g. COS_ALERTMANAGER_TOKEN_PS6

with a global fallback (COS_ALERTMANAGER_URL / COS_ALERTMANAGER_TOKEN) for a
shared/aggregating Alertmanager. <CLOUD> is the cloud slug upper-cased with
'-' -> '_' (ps6, edge-tel -> PS6, EDGE_TEL).

MOCKUP ONLY — no live HTTP calls yet. When a cloud has no endpoint configured
the helpers no-op (return None/False) and log; once an endpoint is set the
documented call shape raises NotImplementedError so it's obvious where to wire
the real request.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:  # avoid importing models at module load
    from cmdb.apps.maintenance.models import MaintenanceWindow

logger = logging.getLogger(__name__)


def _env_suffix(cloud: Optional[str]) -> str:
    return (cloud or "").upper().replace("-", "_")


def alertmanager_config(cloud: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Resolve (url, token) for a cloud's Alertmanager, with global fallback."""
    suffix = _env_suffix(cloud)
    url = (os.environ.get(f"COS_ALERTMANAGER_URL_{suffix}")
           or os.environ.get("COS_ALERTMANAGER_URL") or "").strip() or None
    token = (os.environ.get(f"COS_ALERTMANAGER_TOKEN_{suffix}")
             or os.environ.get("COS_ALERTMANAGER_TOKEN") or "").strip() or None
    return url, token


def _window_cloud(window: "MaintenanceWindow") -> Optional[str]:
    if window.cloud:
        return window.cloud
    if window.environment_id and window.environment:
        return window.environment.cloud
    if window.node_id and window.node:
        return window.node.cloud
    return None


def create_silence(window: "MaintenanceWindow") -> Optional[str]:
    """
    Create an Alertmanager silence for this environment's juju model on the
    cloud's COS. Returns the silence id, or None if that cloud's Alertmanager
    is not configured. Matches on the ``juju_model`` label so only this env's
    alerts are suppressed.
    """
    cloud = _window_cloud(window)
    base, token = alertmanager_config(cloud)
    if not base:
        logger.warning(
            "No COS Alertmanager configured for cloud %r — skipping silence for env %s",
            cloud, window.environment_id,
        )
        return None

    # --- Implementation, ready for when the endpoint is available ----------
    # import requests
    # headers = {"Content-Type": "application/json"}
    # if token:
    #     headers["Authorization"] = f"Bearer {token}"
    # resp = requests.post(
    #     f"{base.rstrip('/')}/api/v2/silences",
    #     headers=headers,
    #     json={
    #         "matchers": [
    #             {"name": "juju_model", "value": window.environment.name,
    #              "isRegex": False, "isEqual": True},
    #         ],
    #         "startsAt": window.starts_at.isoformat(),
    #         "endsAt": window.ends_at.isoformat(),
    #         "createdBy": window.created_by or "is-cmdb",
    #         "comment": f"IS-CMDB maintenance: {window.reason}",
    #     },
    #     timeout=30,
    # )
    # resp.raise_for_status()
    # return resp.json()["silenceID"]
    raise NotImplementedError(f"Wire COS Alertmanager for cloud {cloud!r}")


def create_cloud_silence(window: "MaintenanceWindow") -> Optional[str]:
    """
    Silence a whole cloud's Alertmanager (node-/cloud-scoped maintenance), e.g.
    matching on the ``juju_controller`` or a cloud label. Returns silence id or
    None when that cloud's Alertmanager is not configured. Stub for when
    cloud/node maintenance should also hit Alertmanager (today it goes to
    PagerDuty). See create_silence for the call shape.
    """
    cloud = _window_cloud(window)
    base, token = alertmanager_config(cloud)
    if not base:
        logger.warning("No COS Alertmanager configured for cloud %r — skipping cloud silence", cloud)
        return None
    raise NotImplementedError(f"Wire cloud-wide COS silence for cloud {cloud!r}")


def expire_silence(window: "MaintenanceWindow") -> bool:
    """DELETE /api/v2/silences/{id} on the window's cloud — expire early."""
    cloud = _window_cloud(window)
    base, token = alertmanager_config(cloud)
    if not base or not window.cos_silence_id:
        logger.warning("No COS Alertmanager for cloud %r — skipping silence expiry", cloud)
        return False

    # --- Implementation, ready for when the endpoint is available ----------
    # import requests
    # headers = {}
    # if token:
    #     headers["Authorization"] = f"Bearer {token}"
    # resp = requests.delete(
    #     f"{base.rstrip('/')}/api/v2/silences/{window.cos_silence_id}",
    #     headers=headers, timeout=30,
    # )
    # resp.raise_for_status()
    # return resp.status_code in (200, 204)
    raise NotImplementedError(f"Wire COS Alertmanager for cloud {cloud!r}")
