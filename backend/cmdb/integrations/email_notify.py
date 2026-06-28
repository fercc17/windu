"""
Email notifications for maintenance windows.

Recipients are the CIA-assessment owners of the affected environments
(``cia_owner`` and ``cia_risk_owner``), de-duplicated so each address receives
exactly one email (one message per unique recipient, sent individually so
recipients are not disclosed to each other).

Activated by SMTP credentials in the environment: ``EMAIL_HOST`` / ``EMAIL_PORT``
/ ``EMAIL_HOST_USER`` / ``EMAIL_HOST_PASSWORD`` / ``EMAIL_USE_TLS`` /
``EMAIL_FROM``. With ``EMAIL_HOST`` unset it logs the intended recipients and
sends nothing (dev/no-creds mode). ``EMAIL_BACKEND`` may override the Django
backend (defaults to SMTP).

Returns ``(success, error)`` so the caller can record a
``MaintenanceNotificationChannel`` row (mirrors the Mattermost integration).
Never raises into the maintenance-window flow.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from cmdb.apps.maintenance.models import MaintenanceWindow

logger = logging.getLogger(__name__)


def recipients_for(environments: list) -> list[str]:
    """Unique CIA owner / risk-owner addresses across the environments."""
    seen: list[str] = []
    for env in environments:
        for addr in (env.cia_owner, env.cia_risk_owner):
            if addr and addr not in seen:
                seen.append(addr)
    return seen


def _build_email(window: "MaintenanceWindow", environments: list) -> tuple[str, str]:
    """Subject + body, scope-safe (window.node is None for cloud/env scopes)."""
    cloud = window.resolved_cloud or "—"
    subject = (
        f"[IS-CMDB] Maintenance: {window.target_label} "
        f"({window.starts_at:%Y-%m-%d %H:%M} UTC)"
    )
    env_names = ", ".join(e.name for e in environments) or "none"
    body = (
        "A maintenance window has been scheduled that may affect your service.\n\n"
        f"Target:  {window.target_label} (scope: {window.scope}, cloud: {cloud})\n"
        f"Window:  {window.starts_at} → {window.ends_at} UTC\n"
        f"Reason:  {window.reason}\n\n"
        f"Affected environments: {env_names}\n"
    )
    return subject, body


def send_maintenance_email(
    window: "MaintenanceWindow", environments: list
) -> tuple[bool, Optional[str]]:
    """Notify environment CIA owners about a maintenance window opening (#36)."""
    recipients = recipients_for(environments)
    smtp_host = os.environ.get("EMAIL_HOST")

    if not smtp_host:
        logger.warning("EMAIL_HOST not set — logging email notification only")
        for addr in recipients:
            logger.info(
                "Would email %s about maintenance on %s", addr, window.target_label
            )
        return False, f"EMAIL_HOST not set — logged {len(recipients)} recipient(s) only"

    if not recipients:
        return False, "no CIA owner/risk-owner addresses on affected environments"

    subject, body = _build_email(window, environments)
    from_email = os.environ.get("EMAIL_FROM", "is-cmdb@canonical.com")
    try:
        from django.core.mail import EmailMessage, get_connection

        conn = get_connection(
            backend=os.environ.get(
                "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
            ),
            host=smtp_host,
            port=int(os.environ.get("EMAIL_PORT", "587")),
            username=os.environ.get("EMAIL_HOST_USER") or None,
            password=os.environ.get("EMAIL_HOST_PASSWORD") or None,
            use_tls=os.environ.get("EMAIL_USE_TLS", "true").lower()
            in ("1", "true", "yes"),
        )
        # One message per unique recipient (individual sends, no cross-disclosure).
        for addr in recipients:
            EmailMessage(
                subject=subject, body=body, from_email=from_email,
                to=[addr], connection=conn,
            ).send(fail_silently=False)
        logger.info(
            "sent %d maintenance email(s) for %s", len(recipients), window.target_label
        )
        return True, None
    except Exception as exc:  # noqa: BLE001 — never break the MW flow
        logger.exception("maintenance email send failed")
        return False, str(exc)
