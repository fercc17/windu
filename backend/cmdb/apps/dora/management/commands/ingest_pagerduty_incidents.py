"""
Ingest PagerDuty incidents into ``dora.Incident`` for DORA MTTR / change-failure.

Idempotent: keyed on the PagerDuty incident id via ``update_or_create``, so
re-running over an overlapping window refreshes status/resolution in place rather
than duplicating. Uses the read-only ``PAGERDUTY_API_TOKEN`` — no write scope.

Attribution is best-effort (PagerDuty IS services are team-wide, not per-cloud —
see ``integrations/pagerduty.py``):
  - environment ← Environment.oncall_handle == incident.service.id
  - cloud       ← slug parsed from the title, else the matched env's cloud
  - team        ← matched env's team, else the incident's first PD team

Run::

    python manage.py ingest_pagerduty_incidents                 # last 90 days
    python manage.py ingest_pagerduty_incidents --since-days 30
    python manage.py ingest_pagerduty_incidents --team-ids PQ4ZG3S
    python manage.py ingest_pagerduty_incidents --dry-run
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from cmdb.apps.dora.metrics import parse_cloud
from cmdb.apps.dora.models import Incident
from cmdb.apps.environments.models import Environment
from cmdb.integrations.pagerduty_incidents import fetch_incidents, resolved_at_of

# Static fallback so cloud-parsing works even on an empty DB; merged with the
# live distinct Environment.cloud values at runtime.
_FALLBACK_CLOUDS = (
    "ps5", "ps6", "ps7", "ps8", "microcloud-drs", "edge-tel", "edge-et3",
)


class Command(BaseCommand):
    help = "Ingest PagerDuty incidents into dora.Incident (idempotent on PD id)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--since-days", type=int, default=90,
                            help="Window of incidents to pull, in days (default: 90).")
        parser.add_argument("--team-ids", nargs="*", default=None,
                            help="Scope to these PagerDuty team ids (optional).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Report counts but write nothing.")

    def handle(self, *args: Any, **opts: Any) -> None:
        since_days: int = opts["since_days"]
        dry: bool = opts["dry_run"]
        until = timezone.now()
        since = until - timedelta(days=since_days)

        known_clouds = sorted(
            set(_FALLBACK_CLOUDS)
            | set(
                Environment.objects.exclude(cloud__isnull=True).exclude(cloud="")
                .values_list("cloud", flat=True).distinct()
            )
        )
        # Index PD-service-id -> env once, instead of a query per incident.
        svc_to_env = {
            e.oncall_handle: e
            for e in Environment.objects.exclude(oncall_handle__isnull=True)
                                        .exclude(oncall_handle="")
        }

        fetched = created = updated = attr_env = attr_cloud = 0
        try:
            incidents = fetch_incidents(since, until, team_ids=opts["team_ids"])
        except RuntimeError as exc:  # token unset
            self.stderr.write(self.style.ERROR(str(exc)))
            return

        for inc in incidents:
            fetched += 1
            service = inc.get("service") or {}
            service_id = service.get("id")
            title = inc.get("title") or inc.get("summary")

            env = svc_to_env.get(service_id)
            cloud = parse_cloud(title, known_clouds) or (env.cloud if env else None)
            team = (env.team if env else None) or _pd_team(inc)
            if env:
                attr_env += 1
            if cloud:
                attr_cloud += 1

            resolved_raw = resolved_at_of(inc)
            defaults = {
                "incident_number": inc.get("incident_number"),
                "title": (title or "")[:500],
                "status": inc.get("status") or "triggered",
                "urgency": inc.get("urgency"),
                "service_id": service_id,
                "service_name": service.get("summary"),
                "created_at": parse_datetime(inc["created_at"]),
                "resolved_at": parse_datetime(resolved_raw) if resolved_raw else None,
                "html_url": inc.get("html_url"),
                "environment": env,
                "cloud": cloud,
                "team": team,
                "raw": _trim(inc),
            }

            if dry:
                exists = Incident.objects.filter(pd_id=inc["id"]).exists()
                created += 0 if exists else 1
                updated += 1 if exists else 0
                continue

            _, was_created = Incident.objects.update_or_create(
                pd_id=inc["id"], defaults=defaults
            )
            created += 1 if was_created else 0
            updated += 0 if was_created else 1

        msg = (
            f"pagerduty ingest: fetched={fetched} created={created} updated={updated} "
            f"attributed_env={attr_env} attributed_cloud={attr_cloud} "
            f"window={since_days}d" + (" [DRY RUN]" if dry else "")
        )
        self.stdout.write(self.style.SUCCESS(msg))


def _pd_team(incident: dict[str, Any]) -> Optional[str]:
    teams = incident.get("teams") or []
    return teams[0].get("summary") if teams else None


def _trim(incident: dict[str, Any]) -> dict[str, Any]:
    """Keep a compact, forensic subset of the payload (not the whole blob)."""
    keep = (
        "id", "incident_number", "title", "status", "urgency",
        "created_at", "last_status_change_at", "html_url",
    )
    out = {k: incident.get(k) for k in keep if k in incident}
    svc = incident.get("service") or {}
    out["service"] = {"id": svc.get("id"), "summary": svc.get("summary")}
    return out
