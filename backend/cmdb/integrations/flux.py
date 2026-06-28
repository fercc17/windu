"""
Flux integration for DORA deploy metrics.

Ingestion is **webhook-based**: Flux's notification-controller (a ``Provider`` of
type ``generic-hmac`` + an ``Alert``) POSTs reconcile events to
``/dora/flux/webhook/`` (or ``/dora/flux/webhook/<cloud>/`` once there is one Flux
per cloud). The ``generic-hmac`` provider signs the raw body with HMAC-SHA256 and
sends ``X-Signature: sha256=<hexdigest>``; we verify it with the shared secret.

This module is **pure** (stdlib only, no Django/DB) so the parsing/verification is
unit-testable. The receiver view (``cmdb/apps/dora/views.py``) does the DB work.

"1 Flux today → 1 Flux per cloud later" stays a config change, not a rewrite:

1. **Per-cloud secret/endpoint with a global fallback**, mirroring ``cos.py``'s
   ``COS_ALERTMANAGER_URL_<CLOUD>`` convention (``flux_*`` resolvers below). The
   webhook URL carries the cloud as a path segment so the right secret is picked
   *before* the body is trusted.
2. **Attribute a deploy's cloud via the Environment join** (Flux ``Kustomization``
   name → ``Environment.gitops_path`` → ``Environment.cloud``), never via which
   Flux reported it — done in the view.

A poll fallback (``fetch_deployments``) is left unimplemented; webhook is the
chosen path.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Flux object kinds whose reconciles count as deployments.
RELEVANT_KINDS = frozenset({"Kustomization", "HelmRelease"})


def _cloud_env_suffix(cloud: Optional[str]) -> str:
    """ps6 -> PS6, edge-tel -> EDGE_TEL (matches cos.py's convention)."""
    return (cloud or "").upper().replace("-", "_")


def _resolve(prefix: str, cloud: Optional[str]) -> Optional[str]:
    """``<prefix>_<CLOUD>`` if set, else the global ``<prefix>``, else None."""
    if cloud:
        per_cloud = os.environ.get(f"{prefix}_{_cloud_env_suffix(cloud)}")
        if per_cloud:
            return per_cloud
    return os.environ.get(prefix) or None


def flux_endpoint(cloud: Optional[str] = None) -> Optional[str]:
    """Flux API URL for a cloud (poll fallback): ``FLUX_API_URL[_<CLOUD>]``."""
    return _resolve("FLUX_API_URL", cloud)


def flux_token(cloud: Optional[str] = None) -> Optional[str]:
    """Flux API token for a cloud (poll fallback): ``FLUX_API_TOKEN[_<CLOUD>]``."""
    return _resolve("FLUX_API_TOKEN", cloud)


def flux_webhook_secret(cloud: Optional[str] = None) -> Optional[str]:
    """HMAC secret for the webhook: ``FLUX_WEBHOOK_HMAC_SECRET[_<CLOUD>]``."""
    return _resolve("FLUX_WEBHOOK_HMAC_SECRET", cloud)


def verify_hmac(body: bytes, signature_header: str, secret: str) -> bool:
    """Constant-time-verify a Flux ``generic-hmac`` signature.

    The header is ``sha256=<hexdigest>`` (the ``sha256=`` prefix is optional /
    tolerated). Returns False on any mismatch or missing signature.
    """
    if not signature_header:
        return False
    provided = signature_header.split("=", 1)[1] if "=" in signature_header else signature_header
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided.strip())


@dataclass(frozen=True)
class FluxDeploy:
    """A normalized, terminal Flux reconcile event (one deployment)."""
    kind: str
    name: str
    namespace: str
    commit_sha: str
    revision: str
    succeeded: bool
    reconciled_at: datetime
    message: str = ""


def _revision_of(payload: dict) -> Optional[str]:
    """Pull the git revision from event metadata, falling back to the message."""
    meta = payload.get("metadata") or {}
    for key, value in meta.items():
        if value and "revision" in key.lower():
            return value
    msg = payload.get("message") or ""
    m = re.search(r"revision:?\s*(\S+)", msg, re.IGNORECASE)
    return m.group(1) if m else None


def _extract_sha(revision: Optional[str]) -> Optional[str]:
    """SHA out of a Flux revision string.

    Handles ``main@sha1:abcdef``, ``main/abcdef``, ``sha1:abcdef`` and bare hex.
    """
    if not revision:
        return None
    m = re.search(r"sha1:([0-9a-f]{7,40})", revision, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"[@/:]([0-9a-f]{7,40})$", revision.strip(), re.IGNORECASE)
    if m:
        return m.group(1)
    if re.fullmatch(r"[0-9a-f]{7,40}", revision.strip(), re.IGNORECASE):
        return revision.strip()
    return None


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_event(payload: dict) -> Optional[FluxDeploy]:
    """Normalize a Flux notification event into a ``FluxDeploy``.

    Returns None for events that are not a *terminal* reconcile of a relevant
    kind (progressing/info noise, wrong kind, no revision, no timestamp) — the
    caller treats None as "acknowledge but ignore".
    """
    obj = payload.get("involvedObject") or {}
    kind = obj.get("kind")
    if kind not in RELEVANT_KINDS:
        return None

    reason = payload.get("reason") or ""
    severity = (payload.get("severity") or "").lower()
    message = payload.get("message") or ""

    is_failure = severity == "error" or reason.endswith("Failed")
    is_success = reason.endswith("Succeeded") or (
        severity == "info" and "applied revision" in message.lower()
    )
    if not (is_failure or is_success):
        return None  # Progressing / health / drift noise — not a deployment

    sha = _extract_sha(_revision_of(payload))
    reconciled_at = _parse_ts(payload.get("timestamp"))
    if not sha or not reconciled_at:
        return None

    return FluxDeploy(
        kind=kind,
        name=obj.get("name", ""),
        namespace=obj.get("namespace", ""),
        commit_sha=sha[:40],
        revision=_revision_of(payload) or "",
        succeeded=not is_failure,
        reconciled_at=reconciled_at,
        message=message,
    )


def fetch_deployments(*args, **kwargs):
    """Poll fallback — not implemented; webhook is the chosen ingestion path."""
    raise NotImplementedError(
        "Flux ingestion is webhook-based (see parse_event + the /dora/flux/webhook/ "
        "receiver). The poll path is intentionally left unimplemented."
    )
