"""Rich Stand up board — full-parity rebuild against the unified tables.

Reuses the original standup-dashboard PURE domain logic (vendored under
``libs/standup_dashboard``): the Ticket/Role/Group model, the role resolver,
the To Do/WIP/Success/Distractors classifier, and the role x ticket-kind colour
matrix. Only the data-gathering (DB reads) is re-implemented here on the windu
Django models, so chip/ticket colours and grouping match the original exactly.

The "now" reference is the latest fetch time (the snapshot is historical), so
the 24h windows and role-of-the-day reflect what the board showed at fetch time.

Deferred (Tier 3, noted in the UI): the per-window time-metric columns
(alert/worklog/calendar/GitHub/distractor-share) and the edit modals.
"""
from __future__ import annotations

import json
from datetime import datetime, date, timedelta, timezone as _tz

from django.db.models import Count

from cmdb.apps.standup.roster import ROSTER, REGIONS, REGION_TZ

# Pure domain logic reused verbatim from the original app.
from standup_dashboard.domain.models import Ticket, Role, TicketGroup, TouchEvent
from standup_dashboard.domain import coloring, roles as role_logic
from standup_dashboard.services.classification import classify_for_engineer

GROUP_ORDER = [TicketGroup.TODO, TicketGroup.WIP, TicketGroup.SUCCESS, TicketGroup.DISTRACTORS]
JIRA_BASE = "https://warthogs.atlassian.net/browse/"


def _dt(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=_tz.utc)
    except ValueError:
        return None


def _date(s):
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _labels(s):
    if not s:
        return []
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def build_standup_board(now=None):
    from cmdb.apps.standup.models import (
        FetchSnapshot, TouchEvent as TouchRow, StandupTicket, Pulse,
        RoleSchedule, RoleOverride, WeekendOncall, PulseSummary)
    from cmdb.apps.pagerduty.models import PdLogEntry, PdUser

    # Use the latest *full* fetch — the one carrying pulse/sprint data. Incremental
    # fetches in between only re-pull a handful of touched tickets (no sprints), so
    # the absolute-latest snapshot is usually degenerate (matches the original's
    # "last good fetch" fallback).
    good = Pulse.objects.values_list("fetch_id", flat=True).distinct()
    latest = (FetchSnapshot.objects.filter(id__in=good).order_by("-id").first()
              or FetchSnapshot.objects.order_by("-id").first())
    if not latest:
        return [{"type": "kv", "title": "Stand up", "values": {"status": "No standup fetches imported yet."}}]
    fid = latest.id
    now_ref = _dt(latest.fetched_at) or (now or datetime.now(_tz.utc))
    since_24h = now_ref - timedelta(hours=24)

    # Domain tickets (latest fetch).
    tickets = []
    for r in StandupTicket.objects.filter(fetch_id=fid).values():
        tickets.append(Ticket(
            id=r["ticket_key"], project_key=r["project_key"], title=r["title"] or "",
            status=r["status"] or "", priority=r["priority"], labels=_labels(r["labels_json"]),
            assignee_email=r["assignee_email"], sprint_id=r["sprint_id"],
            is_done_date=_date(r["is_done_date"]), created=_dt(r["created"]),
            status_category=r["status_category"], reporter_email=r["reporter_email"],
            wip_since=_dt(r["wip_since"]), estimate_seconds=r["estimate_seconds"],
            spent_seconds=r["spent_seconds"]))

    # Domain touches (latest fetch) + the 24h subset per (engineer, ticket).
    touches, touched_24h = [], set()
    for r in TouchRow.objects.filter(fetch_id=fid).values("engineer_email", "ticket_id", "kind", "at", "seconds"):
        at = _dt(r["at"])
        touches.append(TouchEvent(ticket_id=r["ticket_id"], engineer_email=r["engineer_email"],
                                  kind=r["kind"], at=at or now_ref, seconds=r["seconds"] or 0))
        if at and at >= since_24h:
            touched_24h.add((r["engineer_email"], r["ticket_id"]))

    # Active sprints + pulse window for classification.
    pulses = list(Pulse.objects.filter(fetch_id=fid).values("project_key", "sprint_id", "start", "end", "state"))
    active_sprint_ids = {p["sprint_id"] for p in pulses if (p["state"] or "").lower() == "active"} or \
                        {p["sprint_id"] for p in pulses}
    pw = None
    for p in pulses:
        s, e = _dt(p["start"]), _dt(p["end"])
        if s and e:
            pw = (s.date(), e.date())
            if p["project_key"] == "ISReq":
                break

    # Roles: weekly schedule + active overrides.
    weekly = {}
    for rs in RoleSchedule.objects.order_by("updated_at").values("engineer_email", "weekday", "role"):
        weekly[(rs["engineer_email"], rs["weekday"])] = rs["role"]
    overrides = {}
    for ro in RoleOverride.objects.values("engineer_email", "role", "expires_at"):
        exp = _dt(ro["expires_at"])
        if exp is None or exp > now_ref:
            overrides[ro["engineer_email"]] = ro["role"]

    # Alert ack/resolve counts per engineer, 24h + pulse, from canonical pd_log_entry.
    email_by_uid = {u.id: (u.email or "").lower() for u in PdUser.objects.all()}
    pulse_start = pw[0] if pw else since_24h.date()
    alert_stats = {}
    for r in (PdLogEntry.objects.exclude(agent_user_id=None)
              .values("agent_user_id", "type", "at")):
        em = email_by_uid.get(r["agent_user_id"], "")
        if not em:
            continue
        t = (r["type"] or "").lower()
        kind = "ack" if "ack" in t else ("res" if "resolv" in t else None)
        if not kind:
            continue
        at = r["at"]
        st = alert_stats.setdefault(em, {"ack24": 0, "res24": 0, "ackP": 0, "resP": 0})
        if at and at >= since_24h:
            st[kind + "24"] += 1
        if at and at.date() >= pulse_start:
            st[kind + "P"] += 1

    # Classify each rostered engineer once (role-independent), cache.
    rostered = {e["email"] for e in ROSTER}
    groups_by_email = {}
    for email in rostered:
        groups_by_email[email] = classify_for_engineer(email, tickets, touches, active_sprint_ids, pw)

    def ribbon(t):
        return {"Highest": "H2", "High": "H1", "Medium": "M", "Low": "L1", "Lowest": "L2"}.get(
            (t.priority or "").strip(), "")

    def build_eng(e, region_key):
        email = e["email"]
        tz = REGION_TZ.get(region_key, "UTC")
        role = role_logic.effective_role(email, tz, now_ref, weekly, overrides)
        groups = groups_by_email.get(email, {})
        out_groups, color_counts = {}, {"green": 0, "yellow": 0, "red": 0}
        assigned_open = completed = 0
        for g in GROUP_ORDER:
            items = groups.get(g, [])
            rows = []
            for t in items:
                assigned = (t.assignee_email == email)
                color = coloring.ticket_color(role, t, assigned=assigned, group=g).value
                color_counts[color] = color_counts.get(color, 0) + 1
                rows.append({
                    "key": t.id, "title": t.title, "color": color,
                    "ribbon": ribbon(t), "priority": t.priority or "", "status": t.status,
                    "project": t.project_key, "is_review": t.is_pr_mp_review,
                    "url": JIRA_BASE + t.id,
                    "touched_24h": (email, t.id) in touched_24h,
                })
            if g in (TicketGroup.TODO, TicketGroup.WIP) and items:
                assigned_open += sum(1 for t in items if t.assignee_email == email)
            if g is TicketGroup.SUCCESS:
                completed += sum(1 for t in items if t.assignee_email == email)
            out_groups[g.value] = rows
        tset = {tid for (em, tid) in touched_24h if em == email}
        all_touched = {tc.ticket_id for tc in touches if tc.engineer_email == email}
        st = alert_stats.get(email, {})
        return {
            "name": e["name"], "email": email, "role": role.value,
            "manager": e["manager"], "starred": e["starred"],
            "touched_24h": len(tset), "touched_pulse": len(all_touched),
            "assigned_open": assigned_open, "completed": completed,
            "alerts_ack_24h": st.get("ack24", 0), "alerts_res_24h": st.get("res24", 0),
            "alerts_ack_pulse": st.get("ackP", 0), "alerts_res_pulse": st.get("resP", 0),
            "colors": color_counts, "groups": out_groups,
        }

    regions = []
    for rk in REGIONS:
        engs = [build_eng(e, rk) for e in ROSTER if rk in e["regions"]]
        engs.sort(key=lambda x: (-(x["colors"]["red"] + x["colors"]["yellow"]),
                                 -x["touched_pulse"], x["name"]))
        regions.append({"key": rk, "engineers": engs})
    management = [build_eng(e, "EMEA") for e in ROSTER if e["global"]]

    board = {
        "type": "standup", "title": "Stand up",
        "last_fetch": latest.fetched_at,
        "regions": regions, "management": management,
        "legend": _legend(),
    }

    sections = [board]
    for sec in (_counts_section(), _pulse_history_section(), _weekend_section(fid)):
        if sec:
            sections.append(sec)
    return sections


def _legend():
    """Role x ticket-kind colour matrix, for the board legend."""
    kinds = [("highest", "Highest"), ("pr_mp", "PR/MP"), ("ps5", "ps5-blocker"),
             ("regular", "Regular"), ("isdb", "ISDB")]
    rows = []
    for role, cells in coloring._TICKET_MATRIX.items():
        row = {"role": role.value}
        for key, label in kinds:
            row[label] = cells[key].value
        rows.append(row)
    return rows


def _counts_section():
    from cmdb.apps.standup.models import PulseSummary
    latest_pulse = (PulseSummary.objects.order_by("-pulse_number")
                    .values_list("pulse_number", flat=True).first())
    if latest_pulse is None:
        return None
    rows = []
    for s in PulseSummary.objects.filter(pulse_number=latest_pulse).values():
        rows.append({
            "region": s["region"],
            "new_highest": s["new_highest"], "new_ps5": s["new_ps5"],
            "new_pr_mp": s["new_pr_mp"], "new_total": s["new_total"],
            "closed_highest": s["closed_highest"], "closed_total": s["closed_total"],
            "isdb_closed": s["isdb_closed"],
            "alerts_ack": s["alerts_ack"], "alerts_resolved": s["alerts_resolved"],
            "alerts_total": s["alerts_total"],
        })
    if not rows:
        return None
    return {"type": "table", "title": f"Pulse counts — pulse {latest_pulse} (by region)", "data": rows}


def _pulse_history_section():
    """Historical per-pulse counts (summed across regions) — the trend over pulses."""
    from cmdb.apps.standup.models import PulseSummary
    from django.db.models import Sum
    agg = (PulseSummary.objects.values("pulse_number").annotate(
        new_total=Sum("new_total"), closed_total=Sum("closed_total"),
        new_highest=Sum("new_highest"), closed_highest=Sum("closed_highest"),
        new_ps5=Sum("new_ps5"), isdb_closed=Sum("isdb_closed"),
        alerts_total=Sum("alerts_total"), alerts_ack=Sum("alerts_ack"),
        alerts_resolved=Sum("alerts_resolved")).order_by("pulse_number"))
    rows = [{"pulse": a["pulse_number"], "new_total": a["new_total"],
             "closed_total": a["closed_total"], "new_highest": a["new_highest"],
             "closed_highest": a["closed_highest"], "new_ps5": a["new_ps5"],
             "isdb_closed": a["isdb_closed"], "alerts_total": a["alerts_total"],
             "alerts_ack": a["alerts_ack"], "alerts_resolved": a["alerts_resolved"]}
            for a in agg]
    if not rows:
        return None
    return {"type": "table", "title": "Pulse history", "data": rows}


def _weekend_section(fid):
    from cmdb.apps.standup.models import WeekendOncall
    from cmdb.apps.standup.roster import BY_EMAIL
    rows = []
    for w in WeekendOncall.objects.filter(fetch_id=fid).values("engineer_email", "weekend_start", "weekend_end"):
        eng = BY_EMAIL.get(w["engineer_email"], {})
        rows.append({"engineer": eng.get("name", w["engineer_email"]),
                     "weekend_start": w["weekend_start"], "weekend_end": w["weekend_end"]})
    if not rows:
        return None
    return {"type": "table", "title": "Weekend on-call", "data": rows}
