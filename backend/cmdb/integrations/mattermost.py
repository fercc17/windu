"""
Mattermost notifications for maintenance windows.

Currently a **DM to user ``fercc17``** (override with ``MATTERMOST_DM_USER``).
Designed to move to a team-tagged channel later: ``_post`` already takes a
channel id and the thread reply tags the affected teams as ``@<slug>``.

Sends two posts: an initial DM and a threaded reply. Real when ``MATTERMOST_TOKEN``
and ``MATTERMOST_URL`` are set; otherwise logs a warning and returns gracefully
(never crashes the maintenance-window flow). Returns ``(success, error)`` so the
caller can record a ``MaintenanceNotificationChannel`` row.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Optional

import requests

if TYPE_CHECKING:
    from cmdb.apps.environments.models import Environment
    from cmdb.apps.maintenance.models import MaintenanceWindow

logger = logging.getLogger(__name__)

DEFAULT_DM_USER = "fercc17"


def _team_slugs(environments: list) -> list[str]:
    """Unique team/owner slugs across the affected environments."""
    slugs = []
    for env in environments:
        slug = env.team or env.owner
        if slug and slug not in slugs:
            slugs.append(slug)
    return slugs


def _build_messages(
    window: "MaintenanceWindow", environments: list, status: str = "Opened"
) -> tuple[str, str]:
    # Scope-safe: node is None for cloud/env-scoped windows, so use the
    # window's own scope helpers rather than window.node directly.
    emoji = "🔧" if status == "Opened" else "✅"
    cloud = window.resolved_cloud or "—"
    initial = (
        f"### {emoji} Maintenance Window — {window.target_label}\n"
        f"**Status:** {status}  \n"
        f"**Scope:** {window.scope} ({window.target_label}) | **Cloud:** {cloud}  \n"
        f"**Window:** {window.starts_at} → {window.ends_at} UTC  \n"
        f"**Reason:** {window.reason}"
    )
    env_names = ", ".join(e.name for e in environments) or "none"
    team_tags = " ".join(f"@{s}" for s in _team_slugs(environments)) or "none"
    closing_line = (
        "Please acknowledge if this affects your service."
        if status == "Opened"
        else "This maintenance window is now closed — alerting has been restored."
    )
    thread = (
        f"Environments affected: {env_names}  \n"
        f"Teams involved: {team_tags}  \n"
        f"{closing_line}"
    )
    return initial, thread


class _MattermostClient:
    def __init__(self, base_url: str, token: str, timeout: int = 30) -> None:
        self.base = base_url.rstrip("/") + "/api/v4"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def _get(self, path: str) -> dict:
        r = self.session.get(self.base + path, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def dm_channel_id(self, username: str) -> str:
        me = self._get("/users/me")
        target = self._get(f"/users/username/{username}")
        r = self.session.post(
            self.base + "/channels/direct",
            json=[me["id"], target["id"]],
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()["id"]

    def post(self, channel_id: str, message: str, root_id: Optional[str] = None) -> str:
        body = {"channel_id": channel_id, "message": message}
        if root_id:
            body["root_id"] = root_id
        r = self.session.post(self.base + "/posts", json=body, timeout=self.timeout)
        r.raise_for_status()
        return r.json()["id"]


def send_maintenance_notification(
    window: "MaintenanceWindow", environments: list, status: str = "Opened"
) -> tuple[bool, Optional[str]]:
    """Send the maintenance DM + thread reply. Returns (success, error).

    ``status`` is ``"Opened"`` on window creation and ``"Closed"`` on
    cancellation/completion (#35 — notify on both open and close).
    """
    token = os.environ.get("MATTERMOST_TOKEN")
    url = os.environ.get("MATTERMOST_URL")
    if not token or not url:
        msg = "MATTERMOST_TOKEN/MATTERMOST_URL not set"
        logger.warning("%s — skipping Mattermost notification", msg)
        return False, msg

    username = os.environ.get("MATTERMOST_DM_USER", DEFAULT_DM_USER)
    initial, thread = _build_messages(window, environments, status)
    try:
        client = _MattermostClient(url, token)
        channel_id = client.dm_channel_id(username)
        root_id = client.post(channel_id, initial)
        client.post(channel_id, thread, root_id=root_id)
        logger.info(
            "Mattermost maintenance DM (%s) sent to %s (root %s)",
            status, username, root_id,
        )
        return True, None
    except Exception as exc:  # noqa: BLE001 — never break the MW flow
        logger.exception("Mattermost notification failed")
        return False, str(exc)
