"""
Trigger maintenance-window notifications and record the outcome.

Each selected channel calls its integration and a ``MaintenanceNotificationChannel``
row is written with success/error, so the detail view (#37) and the decommission
log (#56) have an audit trail. No channel failure ever propagates.
"""
from __future__ import annotations

import logging

from django.utils import timezone

from cmdb.integrations import cos, email_notify, mattermost, pagerduty

from .models import MaintenanceNotificationChannel, MaintenanceWindow

logger = logging.getLogger(__name__)


def _record(window: MaintenanceWindow, channel: str, success: bool, error) -> None:
    MaintenanceNotificationChannel.objects.create(
        window=window,
        channel=channel,
        sent_at=timezone.now(),
        success=success,
        error_message=error,
    )


def trigger_notifications(
    window: MaintenanceWindow, environments: list, channels: set[str]
) -> None:
    """Fire the selected notification channels for a maintenance window.

    Silence routing depends on scope: environment-scoped windows silence a single
    juju model via COS/Alertmanager; node- and cloud-scoped windows silence the
    cloud's PagerDuty service. The view picks the right channel per scope, but we
    guard here too so the dispatch stays correct regardless of caller.
    """
    if "pagerduty" in channels and window.scope != MaintenanceWindow.SCOPE_ENVIRONMENT:
        success, error = False, None
        try:
            pd_id = pagerduty.create_maintenance_window(window)
            if pd_id:
                window.pagerduty_window_id = pd_id
                window.save(update_fields=["pagerduty_window_id"])
                success = True
            else:
                error = "PAGERDUTY_WRITE_TOKEN not set (window not silenced)"
        except NotImplementedError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("PagerDuty notification failed")
            error = str(exc)
        _record(window, "pagerduty", success, error)

    if "cos" in channels:
        success, error = False, None
        try:
            silence_id = cos.create_silence(window)
            if silence_id:
                window.cos_silence_id = silence_id
                window.save(update_fields=["cos_silence_id"])
                success = True
            else:
                error = "COS_ALERTMANAGER_URL not set (model not silenced)"
        except NotImplementedError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("COS silence failed")
            error = str(exc)
        _record(window, "cos", success, error)

    if "mattermost" in channels:
        success, error = mattermost.send_maintenance_notification(window, environments)
        _record(window, "mattermost", success, error)

    if "email" in channels:
        success, error = email_notify.send_maintenance_email(window, environments)
        _record(window, "email", success, error)
