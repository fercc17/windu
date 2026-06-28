"""
DORA-metrics dashboard.

Reads the two append-only event tables (``Incident`` now, ``DeploymentEvent``
once Flux is wired) and renders the four DORA metrics over a selectable window.
Deploy-sourced metrics show a "pending Flux" badge until deploys flow.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import timedelta

from django.http import HttpRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from cmdb.apps.environments.models import Environment
from cmdb.integrations import flux

from .metrics import (
    DORA_LEVELS_TABLE,
    LEVEL_META,
    LEVELS,
    DeployRecord,
    IncidentRecord,
    classify_metric,
    compute_summary,
    humanize_seconds,
    mttr_seconds,
)
from .models import DeploymentEvent, Incident

logger = logging.getLogger(__name__)

ALLOWED_WINDOWS = (7, 30, 90)


def dora_overview(request):
    days = request.GET.get("days")
    try:
        window_days = int(days) if days else 30
    except (TypeError, ValueError):
        window_days = 30
    if window_days not in ALLOWED_WINDOWS:
        window_days = 30

    since = timezone.now() - timedelta(days=window_days)

    inc_qs = Incident.objects.filter(created_at__gte=since)
    deploy_qs = DeploymentEvent.objects.filter(reconciled_at__gte=since)

    incidents = [
        IncidentRecord(created_at=i.created_at, resolved_at=i.resolved_at,
                       cloud=i.cloud, team=i.team)
        for i in inc_qs.only("created_at", "resolved_at", "cloud", "team")
    ]
    deploys = [
        DeployRecord(reconciled_at=d.reconciled_at, committed_at=d.committed_at,
                     succeeded=d.succeeded, cloud=d.cloud)
        for d in deploy_qs.only("reconciled_at", "committed_at", "succeeded", "cloud")
    ]

    summary = compute_summary(incidents, deploys, window_days)

    # Per-cloud MTTR + incident volume (where attribution landed).
    by_cloud: dict[str, list[IncidentRecord]] = defaultdict(list)
    for rec in incidents:
        by_cloud[rec.cloud or "—unattributed—"].append(rec)
    cloud_rows = []
    for cloud, recs in by_cloud.items():
        resolved = [r for r in recs if r.resolution_seconds is not None]
        cloud_rows.append({
            "cloud": cloud,
            "count": len(recs),
            "resolved": len(resolved),
            "mttr": humanize_seconds(mttr_seconds(recs)),
        })
    cloud_rows.sort(key=lambda r: r["count"], reverse=True)
    max_cloud_count = max((r["count"] for r in cloud_rows), default=1) or 1

    attributed = sum(1 for i in incidents if i.cloud)

    # Cards: value + whether the metric is live or pending Flux.
    metric_values = {
        "deploy_freq": summary.deployment_frequency_per_day,
        "lead_time": summary.lead_time_seconds,
        "cfr": summary.change_failure_rate,
        "mttr": summary.mttr_seconds,
    }
    cards = {
        "deploy_freq": _fmt(
            summary.deployment_frequency_per_day,
            lambda v: f"{v:.2f}/day", pending=summary.flux_pending),
        "lead_time": _fmt(
            summary.lead_time_seconds, humanize_seconds, pending=summary.flux_pending),
        "cfr": _fmt(
            summary.change_failure_rate,
            lambda v: f"{v * 100:.0f}%", pending=summary.flux_pending),
        "mttr": _fmt(
            summary.mttr_seconds, humanize_seconds, pending=False),
    }
    # Attach a DORA performance level (Elite/High/Medium/Low) to each card.
    levels = {key: classify_metric(key, val) for key, val in metric_values.items()}
    for key, card in cards.items():
        lvl = levels[key]
        card["level"] = lvl
        card["level_label"] = LEVEL_META[lvl]["label"] if lvl else None
        card["level_badge"] = LEVEL_META[lvl]["badge"] if lvl else None

    # Reference table rows, each tagged with the band the current value falls in.
    levels_table = [{**row, "current": levels.get(row["key"])} for row in DORA_LEVELS_TABLE]

    context = {
        "window_days": window_days,
        "allowed_windows": ALLOWED_WINDOWS,
        "summary": summary,
        "cards": cards,
        "median_ttr": humanize_seconds(summary.median_ttr_seconds),
        "cloud_rows": cloud_rows,
        "max_cloud_count": max_cloud_count,
        "attributed": attributed,
        "unattributed": summary.incident_count - attributed,
        "flux_pending": summary.flux_pending,
        "has_incidents": bool(incidents),
        "levels_table": levels_table,
        "level_columns": LEVELS,
        "level_meta": LEVEL_META,
    }
    return render(request, "dora/overview.html", context)


def _fmt(value, formatter, *, pending: bool) -> dict:
    """Build a card payload: live value, or 'pending Flux' / 'no data'."""
    if value is None:
        return {"value": "—", "pending": pending,
                "note": "pending Flux" if pending else "no data yet"}
    return {"value": formatter(value), "pending": False, "note": ""}


# --- Flux webhook receiver ----------------------------------------------------

@csrf_exempt
@require_POST
def flux_webhook(request: HttpRequest, cloud: str | None = None) -> JsonResponse:
    """Receive a Flux notification event and record a DeploymentEvent.

    ``POST /dora/flux/webhook/`` (one shared Flux) or
    ``POST /dora/flux/webhook/<cloud>/`` (one Flux per cloud — the path selects
    the per-cloud HMAC secret *before* the body is trusted).

    Mirrors the Netbox receiver: HMAC-verify, then always 200 so Flux never
    disables the Alert on a transient error. Unverified/irrelevant events are
    acknowledged but not recorded.
    """
    secret = flux.flux_webhook_secret(cloud)
    if secret:
        signature = request.headers.get("X-Signature", "")
        if not flux.verify_hmac(request.body, signature, secret):
            logger.warning("Flux webhook signature verification failed — ignoring")
            return JsonResponse({"detail": "signature verification failed"}, status=200)
    else:
        logger.warning(
            "FLUX_WEBHOOK_HMAC_SECRET%s not set — accepting Flux webhook without "
            "signature verification (dev mode)", f"_{cloud.upper()}" if cloud else "",
        )

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        logger.warning("Flux webhook: invalid JSON body")
        return JsonResponse({"detail": "invalid json"}, status=200)

    deploy = flux.parse_event(payload)
    if deploy is None:
        return JsonResponse({"status": "ignored"}, status=200)

    try:
        _record_deploy(deploy, payload, path_cloud=cloud)
    except Exception:  # noqa: BLE001 — never 500 back to Flux
        logger.exception("Flux webhook processing error")

    return JsonResponse({"status": "ok"}, status=200)


def _match_environment(name: str) -> Environment | None:
    """Map a Flux Kustomization name to its Environment via the gitops path.

    The Kustomization name is typically the model name (e.g. ``prod-is-vault-ps7``)
    and ``Environment.gitops_path`` is ``models/prod-is-vault-ps7``.
    """
    if not name:
        return None
    qs = Environment.objects
    return (
        qs.filter(gitops_path__iendswith=f"/{name}").first()
        or qs.filter(gitops_path=name).first()
        or qs.filter(name=name).first()
    )


def _record_deploy(deploy, payload: dict, *, path_cloud: str | None) -> None:
    """Idempotently upsert a DeploymentEvent and refresh the env's reconcile state.

    Dedupe key is ``(commit_sha, kustomization)`` where ``kustomization`` is
    prefixed with the cloud when the per-cloud webhook path provides one, so
    identically-named Kustomizations across clusters don't collapse. First apply
    of a revision wins on timestamp; a later failure flips ``succeeded``.
    """
    env = _match_environment(deploy.name)
    resolved_cloud = (env.cloud if env else None) or path_cloud
    kustomization = f"{path_cloud}/{deploy.name}" if path_cloud else deploy.name

    obj, created = DeploymentEvent.objects.get_or_create(
        commit_sha=deploy.commit_sha,
        kustomization=kustomization,
        defaults={
            "environment": env,
            "cloud": resolved_cloud,
            "committed_at": None,  # not in the webhook payload; enrich later for lead time
            "reconciled_at": deploy.reconciled_at,
            "succeeded": deploy.succeeded,
            "source": "flux",
            "raw": _trim_event(payload),
        },
    )
    if not created:
        fields: list[str] = []
        if obj.succeeded and not deploy.succeeded:
            obj.succeeded = False
            fields.append("succeeded")
        if deploy.reconciled_at < obj.reconciled_at:
            obj.reconciled_at = deploy.reconciled_at
            fields.append("reconciled_at")
        if env and not obj.environment_id:
            obj.environment, obj.cloud = env, resolved_cloud
            fields += ["environment", "cloud"]
        if fields:
            obj.save(update_fields=fields)

    # Fulfils the "updated by Flux notification hook" note on Environment.
    if env and (not env.last_reconciled_at or env.last_reconciled_at < deploy.reconciled_at):
        env.last_reconciled_at = deploy.reconciled_at
        updated = ["last_reconciled_at"]
        if deploy.succeeded:
            env.last_good_commit = deploy.commit_sha
            env.last_good_reconcile = deploy.reconciled_at
            updated += ["last_good_commit", "last_good_reconcile"]
        env.save(update_fields=updated)


def _trim_event(payload: dict) -> dict:
    """Keep a compact, forensic subset of the Flux event."""
    obj = payload.get("involvedObject") or {}
    return {
        "involvedObject": {k: obj.get(k) for k in ("kind", "namespace", "name")},
        "reason": payload.get("reason"),
        "severity": payload.get("severity"),
        "message": payload.get("message"),
        "timestamp": payload.get("timestamp"),
        "metadata": payload.get("metadata"),
    }
