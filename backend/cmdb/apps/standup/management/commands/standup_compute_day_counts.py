"""Compute & store the per-day, per-region pulse counts for the current pulse.

windu's ETL is a pure table-copy; the legacy app computed the per-day counts
table live and never stored it. This step does the computation once — reusing
the original (tested) ``services.counts.build_counts`` engine against windu's
already-imported raw data (tickets, PagerDuty incidents/log entries, pulses) —
and stores the raw per-(pulse, region, day) cells in ``standup_pulse_day_count``.

The API then only reads those rows, sums the selected regions per day, and
applies the colour bands + percentage columns — no recompute per request.

Run after ``etl_import`` (or whenever the standup snapshot changes):

    manage.py standup_compute_day_counts
"""
from __future__ import annotations

from datetime import timezone as _tz

from django.core.management.base import BaseCommand
from django.utils import timezone

from standup_dashboard import config
from standup_dashboard.domain.models import Alert, AlertState, Pulse


class Command(BaseCommand):
    help = "Compute & store per-day per-region pulse counts for the current pulse."

    def handle(self, *args, **opts):
        # Reuse the original engine, so make sure its roster-derived config
        # (region timezones / member emails) is populated.
        if not config.REGIONS:
            config._set_roster(config._SEED_ROSTER)

        # The counts/pulse modules top-import the httpx-based Jira client; windu
        # only uses their pure logic, so stub httpx before importing them.
        from cmdb.apps.api.standup_view import _ensure_httpx_stub
        _ensure_httpx_stub()
        from standup_dashboard.services.counts import build_counts
        from standup_dashboard.services.pulse import current_pulse

        from cmdb.apps.api.standup_view import _latest_full_fetch, _domain_tickets, _dt
        from cmdb.apps.standup.models import Pulse as PulseRow, PulseDayCount
        from cmdb.apps.pagerduty.models import PdIncident, PdLogEntry, PdUser

        latest = _latest_full_fetch()
        if not latest:
            self.stdout.write("No standup fetches imported yet — nothing to compute.")
            return
        fid = latest.id
        now = _dt(latest.fetched_at) or timezone.now()

        tickets = _domain_tickets(fid)

        pulses = []
        for p in (PulseRow.objects.filter(fetch_id=fid)
                  .values("project_key", "sprint_id", "name", "start", "end", "state")):
            s, e = _dt(p["start"]), _dt(p["end"])
            if s and e:
                pulses.append(Pulse(project_key=p["project_key"], sprint_id=p["sprint_id"] or 0,
                                    name=p["name"] or "", start=s, end=e, state=p["state"] or "active"))
        if not pulses:
            self.stdout.write("No pulse window for the latest fetch — nothing to compute.")
            return

        # Domain alert *events* from the canonical PagerDuty log: each incident
        # contributes a handler-less TRIGGERED event plus ACK/RESOLVE events with
        # the acting engineer — exactly the shape build_counts expects.
        email_by_uid = {u.id: (u.email or "").lower() for u in PdUser.objects.all()}
        num_by_incident = {i["id"]: i["incident_number"]
                           for i in PdIncident.objects.values("id", "incident_number")}
        alerts = []
        for r in PdLogEntry.objects.values("incident_id", "type", "at", "agent_user_id"):
            t = (r["type"] or "").lower()
            if "trigger" in t:
                state = AlertState.TRIGGERED
            elif "ack" in t:
                state = AlertState.ACKNOWLEDGED
            elif "resolv" in t:
                state = AlertState.RESOLVED
            else:
                continue
            at = r["at"]
            if at is None:
                continue
            if at.tzinfo is None:
                at = at.replace(tzinfo=_tz.utc)
            handler = "" if state is AlertState.TRIGGERED else email_by_uid.get(r["agent_user_id"], "")
            alerts.append(Alert(id=r["incident_id"], handler_email=handler, state=state, at=at,
                                number=num_by_incident.get(r["incident_id"])))

        pulse_number = current_pulse(now.astimezone(_tz.utc).date())[0]
        now_iso = timezone.now().isoformat()

        bulk = []
        for region in config.REGIONS:  # AMER / APAC / EMEA
            order = 0
            for row in build_counts([region], tickets, alerts, pulses, now):
                if row.is_previous:
                    continue  # the per-day table shows the current pulse only
                mttr_n, mtta_n = row.alert_mttr_n, row.alert_mtta_n
                bulk.append(PulseDayCount(
                    pulse_number=pulse_number, region=region, sort_order=order, label=row.label,
                    is_weekend=row.is_weekend, is_total=row.is_total,
                    new_highest=row.new_highest.count, new_pr_mp=row.new_pr_mp.count,
                    new_ps5=row.new_ps5.count, new_regular=row.new_regular.count,
                    new_total=row.new_total.count,
                    closed_highest=row.closed_highest.count, closed_pr_mp=row.closed_pr_mp.count,
                    closed_ps5=row.closed_ps5.count, closed_total=row.closed_total.count,
                    isdb_closed=row.isdb_closed.count,
                    alerts_triggered=row.alerts_triggered.count, alerts_ack=row.alerts_ack.count,
                    alerts_resolved=row.alerts_resolved.count, alerts_total=row.alerts_total.count,
                    # Persist sum+n (not the mean) so regions stay addable at read time.
                    mttr_sum=int(round((row.alert_mttr_seconds or 0) * mttr_n)), mttr_n=mttr_n,
                    mtta_sum=int(round((row.alert_mtta_seconds or 0) * mtta_n)), mtta_n=mtta_n,
                    updated_at=now_iso))
                order += 1

        PulseDayCount.objects.all().delete()
        PulseDayCount.objects.bulk_create(bulk)
        self.stdout.write(self.style.SUCCESS(
            f"Stored {len(bulk)} per-day count rows across {len(config.REGIONS)} regions "
            f"for pulse {pulse_number}."))
