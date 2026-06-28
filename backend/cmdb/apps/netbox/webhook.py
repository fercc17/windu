"""
Netbox webhook receiver.

``POST /api/webhooks/netbox/`` — Netbox fires this on device create/update/delete.
The body is HMAC-SHA512 signed with ``NETBOX_WEBHOOK_SECRET``; if that env var is
unset we accept unsigned payloads (dev convenience) and log a warning.

Always responds 200 so Netbox never disables the webhook on transient errors;
unverified or unparseable payloads are acknowledged but not processed. Device
work is a single idempotent upsert, kept fast enough to run inline.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Node
from .sync import upsert_node_from_device

logger = logging.getLogger(__name__)

# The handoff specifies ``X-NetBox-Signature``; older Netbox uses
# ``X-Hook-Signature``. Accept either.
_SIGNATURE_HEADERS = ("X-NetBox-Signature", "X-Hook-Signature")


def _verify_signature(request: HttpRequest) -> bool:
    """Validate the HMAC-SHA512 signature, or pass through in dev (no secret)."""
    secret = os.environ.get("NETBOX_WEBHOOK_SECRET")
    if not secret:
        logger.warning(
            "NETBOX_WEBHOOK_SECRET not set — accepting Netbox webhook without "
            "signature verification (dev mode)"
        )
        return True
    provided = ""
    for header in _SIGNATURE_HEADERS:
        provided = request.headers.get(header, "")
        if provided:
            break
    if not provided:
        logger.warning("Netbox webhook missing signature header")
        return False
    expected = hmac.new(secret.encode(), request.body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, provided)


@csrf_exempt
@require_POST
def netbox_webhook(request: HttpRequest) -> JsonResponse:
    """Receive a Netbox device webhook and upsert/soft-delete the Node."""
    if not _verify_signature(request):
        logger.warning("Netbox webhook signature verification failed — ignoring payload")
        return JsonResponse({"detail": "signature verification failed"}, status=200)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        logger.warning("Netbox webhook: invalid JSON body")
        return JsonResponse({"detail": "invalid json"}, status=200)

    event = payload.get("event")
    model = payload.get("model")
    data = payload.get("data") or {}

    if model and model != "device":
        return JsonResponse({"detail": f"ignored model {model}"}, status=200)

    try:
        if event in ("created", "updated"):
            node, created = upsert_node_from_device(data)
            logger.info(
                "Netbox webhook %s device %s -> node %s (%s)",
                event, data.get("id"), node.hostname,
                "created" if created else "updated",
            )
        elif event == "deleted":
            netbox_id = data.get("id")
            n = Node.objects.filter(netbox_id=netbox_id).exclude(
                status="decommissioning"
            ).update(status="decommissioning")
            logger.info(
                "Netbox webhook deleted device %s -> %d node(s) decommissioning",
                netbox_id, n,
            )
        else:
            logger.info("Netbox webhook: unhandled event %r", event)
    except Exception:  # noqa: BLE001 — never 500 back to Netbox
        logger.exception("Netbox webhook processing error")

    return JsonResponse({"status": "ok"}, status=200)
