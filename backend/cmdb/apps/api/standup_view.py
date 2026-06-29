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
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone as _tz
from zoneinfo import ZoneInfo

from django.db.models import Count

from cmdb.apps.standup.roster import ROSTER, REGIONS, REGION_TZ, BY_EMAIL

# Pure domain logic reused verbatim from the original app.
from standup_dashboard.domain.models import (
    Ticket, Role, TicketGroup, TouchEvent, hours_label, format_duration)
from standup_dashboard.domain import coloring, roles as role_logic
from standup_dashboard.services.classification import classify_for_engineer
from standup_dashboard.services.offenders import incident_signature

GROUP_ORDER = [TicketGroup.TODO, TicketGroup.WIP, TicketGroup.SUCCESS, TicketGroup.DISTRACTORS]
JIRA_BASE = "https://warthogs.atlassian.net/browse/"
PD_INCIDENT_BASE = "https://canonical.pagerduty.com/incidents/"

# Follow-the-sun on-call handover order (#handover): the PVG/BVG duty rotates
# APAC → EMEA → AMER, so each region hands over to the next and receives from the
# previous one.
HANDOVER_ORDER = ["APAC", "EMEA", "AMER"]

# UI-state key for the "Focus: Highest/PS5/PR-MP" toggle (#focus-toggle), persisted
# server-side in standup_ui_state so it survives reloads like the original.
FOCUS_KEY = "highest_focus"
# Repeat-offender thresholds (#146): chronic if still firing within RECENT_DAYS and
# fired more than YEAR_MIN times this calendar year.
OFFENDER_RECENT_DAYS = 10
OFFENDER_YEAR_MIN = 10
# Statuses excluded from Aging WIP though their category is "In Progress" (#147):
# a Blocked ticket isn't actively being worked, so it shouldn't age as WIP.
_AGING_EXCLUDED_STATUSES = frozenset({"blocked"})


def _ensure_httpx_stub():
    """The vendored ``services.counts``/``services.pulse`` modules top-import the
    Jira client (httpx-based), but windu only uses their pure date/counts logic
    and httpx isn't installed in this env. httpx is referenced only inside client
    methods (never at import — the modules use ``from __future__ import
    annotations``), so a stub module lets the import chain resolve."""
    import sys
    import types
    sys.modules.setdefault("httpx", types.ModuleType("httpx"))


def _ensure_schedule_importable():
    """``services.schedule`` top-imports ``storage.db`` (psycopg v3 + pool), but
    windu only needs its pure paste parser and runs on psycopg2. Stub the psycopg
    modules so the import chain resolves — the stubbed symbols are only referenced
    inside ``Database`` methods, which the paste parser never calls."""
    import sys
    import types
    for name in ("psycopg", "psycopg.rows", "psycopg.types", "psycopg.types.json", "psycopg_pool"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["psycopg.rows"].dict_row = object
    sys.modules["psycopg.types.json"].Jsonb = object
    sys.modules["psycopg_pool"].ConnectionPool = object


def get_focus() -> bool:
    """Whether the Highest/PS5/PR-MP focus toggle is on (server-persisted)."""
    from cmdb.apps.standup.models import UiState
    row = UiState.objects.filter(key=FOCUS_KEY).order_by("-updated_at").first()
    return bool(row and row.value == "on")


def _is_pto_today(pto_days, tz, now):
    """True if the engineer's calendar marks today (region-local) as a day off
    (#cal-off). ``pto_days`` is the pipe-joined ``"%a %b %d"`` labels the card lists."""
    if not pto_days:
        return False
    return now.astimezone(ZoneInfo(tz)).strftime("%a %b %d") in pto_days


def _cell(value, color):
    """Wrap a value with a green/yellow/red band for the DataTable colored renderer."""
    return {"v": value, "c": color.value} if color is not None else value


def _summary_row(s, regions=1):
    """One counts row (per region/pulse) with the original colour bands applied."""
    return {
        "new_highest": s["new_highest"],
        "closed_highest": _cell(s["closed_highest"],
                                coloring.closed_vs_new_level(s["closed_highest"], s["new_highest"])),
        "new_ps5": s["new_ps5"],
        "closed_ps5": _cell(s["closed_ps5"],
                            coloring.closed_vs_new_level(s["closed_ps5"], s["new_ps5"])),
        "new_total": s["new_total"],
        "closed_total": _cell(s["closed_total"],
                              coloring.closed_vs_new_total_level(s["closed_total"], s["new_total"], regions)),
        "isdb_closed": s["isdb_closed"],
        "alerts_ack": s["alerts_ack"],
        "alerts_resolved": _cell(s["alerts_resolved"],
                                 coloring.resolve_rate_level(s["alerts_resolved"], s["alerts_ack"])),
        "alerts_total": s["alerts_total"],
    }


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


def _latest_full_fetch():
    """The latest *full* fetch — the one carrying pulse/sprint data. Incremental
    fetches in between only re-pull a handful of touched tickets (no sprints), so
    the absolute-latest snapshot is usually degenerate (matches the original's
    "last good fetch" fallback)."""
    from cmdb.apps.standup.models import FetchSnapshot, Pulse
    good = Pulse.objects.values_list("fetch_id", flat=True).distinct()
    return (FetchSnapshot.objects.filter(id__in=good).order_by("-id").first()
            or FetchSnapshot.objects.order_by("-id").first())


def _latest_fetch_now(now=None):
    """(fetch_id, now_ref) for the latest full fetch; (None, fallback now) if none."""
    latest = _latest_full_fetch()
    if not latest:
        return None, (now or datetime.now(_tz.utc))
    return latest.id, (_dt(latest.fetched_at) or (now or datetime.now(_tz.utc)))


def _domain_tickets(fid):
    """The fetch's tickets as pure-domain ``Ticket`` objects (matrix/grouping logic)."""
    from cmdb.apps.standup.models import StandupTicket
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
    return tickets


def build_standup_board(now=None):
    from cmdb.apps.standup.models import (
        FetchSnapshot, TouchEvent as TouchRow, StandupTicket, Pulse,
        RoleSchedule, RoleOverride, WeekendOncall, PulseSummary, OpenSummaryCount,
        CalendarAvail, GithubPr)
    from cmdb.apps.pagerduty.models import PdLogEntry, PdUser

    latest = _latest_full_fetch()
    if not latest:
        return [{"type": "kv", "title": "Stand up", "values": {"status": "No standup fetches imported yet."}}]
    fid = latest.id
    now_ref = _dt(latest.fetched_at) or (now or datetime.now(_tz.utc))
    since_24h = now_ref - timedelta(hours=24)
    focus_on = get_focus()

    # Tickets + pulse window come from the latest *full* fetch; worklog touches,
    # GitHub PR counts and calendar are spread across the pulse's incremental
    # fetches, so accumulate those across every fetch in the pulse (#88).
    tickets = _domain_tickets(fid)

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
    pulse_start = pw[0] if pw else since_24h.date()
    pstart_dt = datetime.combine(pulse_start, datetime.min.time(), tzinfo=_tz.utc)

    # Fetches in this pulse (oldest → newest); their union is the pulse's worklog.
    snaps = sorted(FetchSnapshot.objects.values("id", "fetched_at"),
                   key=lambda sn: sn["fetched_at"] or "")
    snap_ids = [sn["id"] for sn in snaps if (_dt(sn["fetched_at"]) or pstart_dt) >= pstart_dt]
    if fid not in snap_ids:
        snap_ids.append(fid)
    snap_order = {sid: i for i, sid in enumerate(snap_ids)}

    # Touches: union across the pulse's fetches (dedup by ticket/engineer/kind/at),
    # plus the rolling-24h touched subset per (engineer, ticket).
    touches_map, touched_24h = {}, set()
    for r in (TouchRow.objects.filter(fetch_id__in=snap_ids)
              .values("engineer_email", "ticket_id", "kind", "at", "seconds")):
        at = _dt(r["at"])
        touches_map[(r["ticket_id"], r["engineer_email"], r["kind"], r["at"])] = TouchEvent(
            ticket_id=r["ticket_id"], engineer_email=r["engineer_email"],
            kind=r["kind"], at=at or now_ref, seconds=r["seconds"] or 0)
        if at and at >= since_24h:
            touched_24h.add((r["engineer_email"], r["ticket_id"]))
    touches = list(touches_map.values())

    # Worklog seconds per (engineer, ticket) + per-engineer Jira totals by project.
    proj = {t.id: t.project_key for t in tickets}
    worklog = defaultdict(int)
    for tc in touches:
        if tc.seconds:
            worklog[(tc.engineer_email, tc.ticket_id)] += tc.seconds
    eng_jira = defaultdict(lambda: {"isdb": 0, "isreq": 0, "total": 0})
    for (em, tid), secs in worklog.items():
        pk = proj.get(tid, "")
        eng_jira[em]["total"] += secs
        if pk == "ISDB":
            eng_jira[em]["isdb"] += secs
        elif pk == "ISReq":
            eng_jira[em]["isreq"] += secs

    # Roles: weekly schedule + active overrides.
    weekly = {}
    for rs in RoleSchedule.objects.order_by("updated_at").values("engineer_email", "weekday", "role"):
        weekly[(rs["engineer_email"], rs["weekday"])] = rs["role"]
    overrides = {}
    for ro in RoleOverride.objects.order_by("created_at").values("engineer_email", "role", "expires_at"):
        exp = _dt(ro["expires_at"])
        if exp is None or exp > now_ref:
            overrides[ro["engineer_email"]] = ro["role"]

    # Calendar: per-email latest-wins across the pulse's fetches (a transient iCal
    # miss on one refresh keeps that person's last-good value). Day-off labels are
    # unioned across fetches instead (PTO is cumulative — any fetch that marked a
    # day off counts), which the calendar auto-OFF reads (#cal-off).
    cal_by_email, pto_union = {}, {}
    for c in sorted(CalendarAvail.objects.filter(fetch_id__in=snap_ids).values(),
                    key=lambda r: snap_order.get(r["fetch_id"], -1)):
        cal_by_email[c["engineer_email"]] = c
        if c["pto_days"]:
            pto_union.setdefault(c["engineer_email"], set()).update(c["pto_days"].split("|"))
    # GitHub PR counts are a current snapshot (not accumulated): the most recent
    # fetch that actually has PR rows wins.
    gh_rows = list(GithubPr.objects.filter(fetch_id__in=snap_ids).values())
    gh_by_email = {}
    if gh_rows:
        best = max((r["fetch_id"] for r in gh_rows), key=lambda f: snap_order.get(f, -1))
        gh_by_email = {r["engineer_email"]: r for r in gh_rows if r["fetch_id"] == best}

    # Alert ack/resolve counts per engineer, 24h + pulse, from canonical pd_log_entry.
    # Also accumulate per-incident earliest ack + earliest resolve (with resolver),
    # which drive each engineer's alert overlap/union handling time (#alert-time).
    email_by_uid = {u.id: (u.email or "").lower() for u in PdUser.objects.all()}
    alert_stats = {}
    alert_ack_at = {}   # incident_id -> earliest ack datetime (any handler)
    alert_res = {}      # incident_id -> (earliest resolve datetime, resolver email)
    for r in (PdLogEntry.objects.exclude(agent_user_id=None)
              .values("incident_id", "agent_user_id", "type", "at")):
        em = email_by_uid.get(r["agent_user_id"], "")
        if not em:
            continue
        t = (r["type"] or "").lower()
        kind = "ack" if "ack" in t else ("res" if "resolv" in t else None)
        if not kind:
            continue
        at = r["at"]
        if at is None:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=_tz.utc)
        st = alert_stats.setdefault(em, {"ack24": 0, "res24": 0, "ackP": 0, "resP": 0})
        if at >= since_24h:
            st[kind + "24"] += 1
        if at.date() >= pulse_start:
            st[kind + "P"] += 1
        iid = r["incident_id"]
        if kind == "ack":
            if iid not in alert_ack_at or at < alert_ack_at[iid]:
                alert_ack_at[iid] = at
        elif iid not in alert_res or at < alert_res[iid][0]:
            alert_res[iid] = (at, em)

    # Classify each rostered engineer once (role-independent), cache.
    rostered = {e["email"] for e in ROSTER}
    groups_by_email = {}
    for email in rostered:
        groups_by_email[email] = classify_for_engineer(email, tickets, touches, active_sprint_ids, pw)

    def ribbon(t):
        return {"Highest": "H2", "High": "H1", "Medium": "M", "Low": "L1", "Lowest": "L2"}.get(
            (t.priority or "").strip(), "")

    # --- per-window (24h / today / pulse) time helpers (#engineer-metrics) ---
    def ticket_time(email, since, project=None):
        """Worklog seconds ``email`` logged since ``since`` (optionally one project)."""
        return sum(tc.seconds for tc in touches
                   if tc.engineer_email == email and tc.seconds and tc.at >= since
                   and (project is None or proj.get(tc.ticket_id) == project))

    def worklog_on(email, since, ticket_ids):
        return sum(tc.seconds for tc in touches
                   if tc.engineer_email == email and tc.seconds and tc.at >= since
                   and tc.ticket_id in ticket_ids)

    def alert_intervals(email, since):
        """(ack, resolve) spans for incidents ``email`` resolved since ``since``."""
        spans = []
        for iid, (res_at, resolver) in alert_res.items():
            if resolver != email or res_at < since:
                continue
            acked = alert_ack_at.get(iid)
            if acked is not None and res_at >= acked:
                spans.append((acked, res_at))
        return spans

    def alert_overlap(email, since):
        """Per-incident handling time summed (concurrent incidents counted twice)."""
        return sum(int((r - a).total_seconds()) for a, r in alert_intervals(email, since))

    def alert_union(email, since):
        """Wall-clock alert time with overlapping spans merged once (#173)."""
        total, cs, ce = 0, None, None
        for a, r in sorted(alert_intervals(email, since)):
            if ce is None or a > ce:
                if ce is not None:
                    total += int((ce - cs).total_seconds())
                cs, ce = a, r
            elif r > ce:
                ce = r
        if ce is not None:
            total += int((ce - cs).total_seconds())
        return total

    def build_eng(e, region_key, is_mgmt=False):
        email = e["email"]
        tz = REGION_TZ.get(region_key, "UTC")
        role = role_logic.effective_role(email, tz, now_ref, weekly, overrides)
        # Calendar auto-OFF (#cal-off): a calendar day-off today forces OFF, unless
        # a manual today override is set (the override wins over the calendar).
        if email not in overrides:
            labels = pto_union.get(email)
            if labels and now_ref.astimezone(ZoneInfo(tz)).strftime("%a %b %d") in labels:
                role = Role.OFF
        groups = groups_by_email.get(email, {})
        out_groups, color_counts = {}, {"green": 0, "yellow": 0, "red": 0}
        assigned_open = completed = 0
        for g in GROUP_ORDER:
            items = groups.get(g, [])
            rows = []
            for t in items:
                assigned = (t.assignee_email == email)
                color = coloring.ticket_color(role, t, assigned=assigned, group=g).value
                # Focus toggle (#focus-toggle): flag in-progress ISReq that isn't
                # Highest / ps5-blocker / [PR/MP Review] so off-focus work is obvious
                # at a glance (forced red, like the original off-focus distractor).
                flagged = (focus_on and not is_mgmt and g is TicketGroup.WIP and t.is_isreq
                           and not (t.is_highest or t.is_pr_mp_review or t.has_ps5_blockers))
                if flagged:
                    color = "red"
                color_counts[color] = color_counts.get(color, 0) + 1
                secs = worklog.get((email, t.id), 0)
                est, sp = t.estimate_seconds, t.spent_seconds
                rows.append({
                    "key": t.id, "title": t.title, "color": color, "flagged": flagged,
                    "ribbon": ribbon(t), "priority": t.priority or "", "status": t.status,
                    "project": t.project_key, "is_review": t.is_pr_mp_review,
                    "url": JIRA_BASE + t.id,
                    "touched_24h": (email, t.id) in touched_24h,
                    "time_label": hours_label(secs) if secs else "",
                    "effort_label": (f"{hours_label(est or 0)} ▸ {hours_label(sp or 0)}"
                                     if (t.is_isdb and (est or sp)) else ""),
                })
            if g in (TicketGroup.TODO, TicketGroup.WIP) and items:
                assigned_open += sum(1 for t in items if t.assignee_email == email)
            if g is TicketGroup.SUCCESS:
                completed += sum(1 for t in items if t.assignee_email == email)
            out_groups[g.value] = rows
        tset = {tid for (em, tid) in touched_24h if em == email}
        all_touched = {tc.ticket_id for tc in touches if tc.engineer_email == email}
        st = alert_stats.get(email, {})
        ej = eng_jira.get(email, {"isdb": 0, "isreq": 0, "total": 0})
        c = cal_by_email.get(email)
        calendar = None
        if c:
            calendar = {
                "busy": hours_label(c["busy_seconds"]), "open": hours_label(c["open_seconds"]),
                "pto": hours_label(c["pto_seconds"]),
                "pto_days": c.get("pto_days") or "", "sd_days": c.get("sd_days") or "",
            }
        g = gh_by_email.get(email)
        github = ({"created": g["created"], "merged": g["merged"],
                   "updated": g["updated"], "reviewed": g["reviewed"]} if g else None)

        # Three-window (24h / today / pulse) rich metrics (#engineer-metrics):
        # calendar busy/open + GitHub counts are read from the stored per-window
        # fields; total/Jira/alert/distractor time is summed from touches + alerts.
        zone = ZoneInfo(REGION_TZ.get(region_key, "UTC"))
        today_start = datetime.combine(now_ref.astimezone(zone).date(),
                                       datetime.min.time(), tzinfo=zone)
        distractor_ids = {t.id for t in groups.get(TicketGroup.DISTRACTORS, [])}
        cc, gg = c or {}, g or {}

        def _win(since, busy_s, open_s, gh):
            jp = ticket_time(email, since, "ISDB")
            jt = ticket_time(email, since, "ISReq")
            ov = alert_overlap(email, since)
            un = alert_union(email, since)
            dist = worklog_on(email, since, distractor_ids)
            share = (f"{hours_label(dist)} · {round(dist / open_s * 100)}% of open"
                     if (not is_mgmt and open_s > 0) else "")
            return {
                # Total engaged time = alerts (no overlap) + Jira project + Jira ticket + busy.
                "total": hours_label(un + jp + jt + busy_s),
                "alerts_overlap": hours_label(ov),
                "alerts_no_overlap": hours_label(un),
                "jira_project": hours_label(jp),
                "jira_ticket": hours_label(jt),
                "gh_opened": gh[0], "gh_merged": gh[1], "gh_touched": gh[2], "gh_reviewed": gh[3],
                "busy": hours_label(busy_s), "open": hours_label(open_s),
                "distractors": share,
            }

        windows = {
            "24h": _win(since_24h, cc.get("busy_24h", 0), cc.get("open_24h", 0),
                        (gg.get("created_24h", 0), gg.get("merged_24h", 0),
                         gg.get("updated_24h", 0), gg.get("reviewed_24h", 0))),
            "today": _win(today_start, cc.get("busy_today", 0), cc.get("open_today", 0),
                          (gg.get("created_today", 0), gg.get("merged_today", 0),
                           gg.get("updated_today", 0), gg.get("reviewed_today", 0))),
            "pulse": _win(pstart_dt, cc.get("busy_seconds", 0), cc.get("open_seconds", 0),
                          (gg.get("created", 0), gg.get("merged", 0),
                           gg.get("updated", 0), gg.get("reviewed", 0))),
        }
        isreq_sprint = isdb_sprint = 0
        for t in tickets:
            if t.assignee_email != email or t.sprint_id not in active_sprint_ids:
                continue
            if t.project_key == "ISReq":
                isreq_sprint += 1
            elif t.project_key == "ISDB":
                isdb_sprint += 1

        return {
            "name": e["name"], "email": email, "role": role.value,
            "manager": e["manager"], "starred": e["starred"],
            "touched_24h": len(tset), "touched_pulse": len(all_touched),
            "assigned_open": assigned_open, "completed": completed,
            "alerts_ack_24h": st.get("ack24", 0), "alerts_res_24h": st.get("res24", 0),
            "alerts_ack_pulse": st.get("ackP", 0), "alerts_res_pulse": st.get("resP", 0),
            "jira_hours": hours_label(ej["total"]),
            "jira_isdb_hours": hours_label(ej["isdb"]),
            "jira_isreq_hours": hours_label(ej["isreq"]),
            "calendar": calendar, "github": github,
            "windows": windows,
            "sprint": {"isreq": isreq_sprint, "isdb": isdb_sprint},
            "sd_days": cc.get("sd_days") or "",
            "colors": color_counts, "groups": out_groups,
        }

    # Managers (Fernando, Javier) + global leads go to Management, not the regions.
    regions = []
    for rk in REGIONS:
        engs = [build_eng(e, rk) for e in ROSTER
                if rk in e["regions"] and not e["manager"] and not e["global"]]
        engs.sort(key=lambda x: (-(x["colors"]["red"] + x["colors"]["yellow"]),
                                 -x["touched_pulse"], x["name"]))
        regions.append({"key": rk, "engineers": engs})
    management = [build_eng(e, "EMEA", is_mgmt=True) for e in ROSTER if e["manager"] or e["global"]]

    # On-call handover (#handover): stamp each genuine PVG/BVG duty-holder with the
    # same-role counterpart in the next (hands-over-to) and previous (receives-from)
    # follow-the-sun region. The counterpart region shows even when nobody holds the
    # duty there yet (name → "" renders as "unassigned").
    holders = {}  # (region, role) -> [names]
    for r in regions:
        for e in r["engineers"]:
            if e["role"] in ("PVG", "BVG"):
                holders.setdefault((r["key"], e["role"]), []).append(e["name"])

    def _ho_region(key, offset):
        if key not in HANDOVER_ORDER:
            return ""
        return HANDOVER_ORDER[(HANDOVER_ORDER.index(key) + offset) % len(HANDOVER_ORDER)]

    for r in regions:
        for e in r["engineers"]:
            if e["role"] in ("PVG", "BVG"):
                to_region, from_region = _ho_region(r["key"], +1), _ho_region(r["key"], -1)
                e["handover"] = {
                    "to_region": to_region, "from_region": from_region,
                    "to": ", ".join(holders.get((to_region, e["role"]), [])),
                    "from": ", ".join(holders.get((from_region, e["role"]), [])),
                }

    # Open-work summary bar (icon + label + count + Jira/PagerDuty deep link), in
    # the original's order: PR/MPs · PS5 Highest · IS Highest · PS5-blocker ·
    # Escalated ISReq · Ongoing alerts.
    summary = _open_summary()

    # Weekend on-call — only upcoming weekends (drop already-finished ones), each
    # with the weekend's alert load split into the on-call's in-hours (09–17 local
    # to their region) vs off-hours, by alert trigger time.
    import datetime as _dt2
    from cmdb.apps.pagerduty.models import PdIncident as _PdInc
    real_today = datetime.now(_tz.utc).date()
    weekend = []
    for w in (WeekendOncall.objects.filter(fetch_id=fid)
              .order_by("weekend_start").values("engineer_email", "weekend_start", "weekend_end")):
        ws, we = _date(w["weekend_start"]), _date(w["weekend_end"])
        # Keep the current/just-finished weekend (3-day grace) and any upcoming;
        # drop older ones.
        if we is None or we < real_today - _dt2.timedelta(days=3):
            continue
        email = w["engineer_email"]
        eng = BY_EMAIL.get(email, {})
        regs = eng.get("regions") or []
        tz = ZoneInfo(REGION_TZ.get(regs[0], "UTC")) if regs else ZoneInfo("UTC")
        win0 = _dt2.datetime.combine(ws or we, _dt2.time.min, tzinfo=_dt2.timezone.utc)
        win1 = _dt2.datetime.combine(we + _dt2.timedelta(days=1), _dt2.time.min, tzinfo=_dt2.timezone.utc)
        in_h = off_h = 0
        for c in _PdInc.objects.filter(created_at__gte=win0, created_at__lt=win1).values_list("created_at", flat=True):
            h = c.astimezone(tz).hour
            if 9 <= h < 17:
                in_h += 1
            else:
                off_h += 1
        weekend.append({"name": eng.get("name", email), "region": regs[0] if regs else "",
                        "start": w["weekend_start"], "end": w["weekend_end"],
                        "alerts_in_hours": in_h, "alerts_off_hours": off_h,
                        "alerts_total": in_h + off_h})

    board = {
        "type": "standup", "title": "Stand up",
        "last_fetch": latest.fetched_at,
        "focus": focus_on,
        "summary": summary,
        "weekend": weekend,
        "pulse_counts": _counts_data(),  # rendered in-board, region-filtered, no search
        "regions": regions, "management": management,
        "legend": _legend(),
    }
    return [board]


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


def _counts_data():
    from cmdb.apps.standup.models import PulseSummary
    latest_pulse = (PulseSummary.objects.order_by("-pulse_number")
                    .values_list("pulse_number", flat=True).first())
    if latest_pulse is None:
        return None
    rows = [{"region": s["region"], **_summary_row(s, regions=1)}
            for s in PulseSummary.objects.filter(pulse_number=latest_pulse).order_by("region").values()]
    if not rows:
        return None
    return {"pulse": latest_pulse, "rows": rows}


def _open_summary():
    """Open-work bar: one icon+label+count+deep-link item per category, ordered as
    the original (PR/MPs · PS5 Highest · IS Highest · PS5-blocker · Escalated · alerts)."""
    from cmdb.apps.standup.models import OpenSummaryCount
    from standup_dashboard import config
    s = (OpenSummaryCount.objects.order_by("-fetch_id")
         .values("highest", "pr_mp", "ps5", "ps5_highest", "escalated", "ongoing_alerts").first())
    if not s:
        return None

    def furl(key):
        try:
            return config.jira_filter_url(config.JIRA_OPEN_FILTERS[key])
        except Exception:
            return ""
    try:
        esc_url = config.jira_jql_url(config.JIRA_ESCALATED_ISREQ_JQL)
    except Exception:
        esc_url = ""
    try:
        pd_url = config.pagerduty_open_incidents_url()
    except Exception:
        pd_url = ""
    items = [
        {"key": "pr_mp", "icon": "🔗", "label": "Open PR/MPs", "value": s["pr_mp"] or 0, "url": furl("pr_mp")},
        {"key": "ps5_highest", "icon": "🚨", "label": "Open PS5 Highest", "value": s["ps5_highest"] or 0, "url": furl("ps5_highest")},
        {"key": "highest", "icon": "🔥", "label": "Open IS Highest", "value": s["highest"] or 0, "url": furl("highest")},
        {"key": "ps5", "icon": "🚧", "label": "Open PS5-blocker", "value": s["ps5"] or 0, "url": furl("ps5")},
        {"key": "escalated", "icon": "🔺", "label": "Escalated ISReq", "value": s.get("escalated") or 0, "url": esc_url},
        {"key": "ongoing_alerts", "icon": "🔔", "label": "Ongoing alerts", "value": s["ongoing_alerts"] or 0,
         "url": pd_url, "alert": bool(s["ongoing_alerts"])},
    ]
    return {"items": items}


def _members_for_regions(regions=None):
    """Rostered emails for the selected regions; all rostered if none selected."""
    sel = {r for r in (regions or []) if r}
    if not sel:
        return {e["email"] for e in ROSTER}
    return {e["email"] for e in ROSTER if sel & set(e["regions"])}


def build_offenders(now=None):
    """Repeat-offender alerts (#146): signatures still firing in the last
    OFFENDER_RECENT_DAYS days that fired > OFFENDER_YEAR_MIN times this calendar
    year, from the canonical pd_incident history. No extra fetching."""
    from cmdb.apps.pagerduty.models import PdIncident, PdLogEntry, PdUser
    now = now or datetime.now(_tz.utc)
    year0 = datetime(now.year, 1, 1, tzinfo=_tz.utc)
    cutoff = now - timedelta(days=OFFENDER_RECENT_DAYS)

    title_by_incident, groups = {}, {}
    for r in (PdIncident.objects.filter(created_at__gte=year0)
              .values("id", "incident_number", "title", "created_at")):
        fired = r["created_at"]
        if fired is None:
            continue
        display, sig = incident_signature(r["title"])
        if not sig:
            continue
        title_by_incident[r["id"]] = r["title"]
        g = groups.get(sig)
        if g is None:
            g = groups[sig] = {"year": 0, "recent": 0, "latest": fired,
                               "title": display, "number": r["incident_number"], "id": r["id"]}
        g["year"] += 1
        if fired >= cutoff:
            g["recent"] += 1
        if fired >= g["latest"]:  # representative = the most-recent incident
            g["latest"], g["title"] = fired, display
            g["number"], g["id"] = r["incident_number"], r["id"]

    # Handlers in the recent window (ack/resolve log entries → roster/PD name).
    email_by_uid = {u.id: (u.email or "").lower() for u in PdUser.objects.all()}
    pd_name_by_uid = {u.id: (u.name or u.email or u.id) for u in PdUser.objects.all()}
    name_by_email = {e["email"].lower(): e["name"] for e in ROSTER}
    handlers = {}
    for r in (PdLogEntry.objects.filter(at__gte=cutoff).exclude(agent_user_id=None)
              .values("incident_id", "type", "agent_user_id")):
        t = (r["type"] or "").lower()
        if "ack" not in t and "resolv" not in t:
            continue
        title = title_by_incident.get(r["incident_id"])
        if not title:
            continue
        _, sig = incident_signature(title)
        if not sig:
            continue
        em = email_by_uid.get(r["agent_user_id"], "")
        name = name_by_email.get(em) or pd_name_by_uid.get(r["agent_user_id"]) or em or "?"
        handlers.setdefault(sig, set()).add(name)

    rows = [
        {"title": g["title"] or "(untitled alert)", "year_count": g["year"],
         "recent_count": g["recent"], "number": g["number"],
         "url": f"{PD_INCIDENT_BASE}{g['id']}" if g["id"] else "",
         "handlers": sorted(handlers.get(sig, []))}
        for sig, g in groups.items()
        if g["recent"] >= 1 and g["year"] > OFFENDER_YEAR_MIN
    ]
    rows.sort(key=lambda r: (-r["year_count"], -r["recent_count"], r["title"].lower()))
    return rows


def build_aging(regions=None, now=None):
    """WIP tickets owned by the selected region(s)' members, most-aged first (#147).
    Blocked tickets are excluded — they're not active work."""
    fid, now_ref = _latest_fetch_now(now)
    if fid is None:
        return []
    members = _members_for_regions(regions)
    rows = []
    for t in _domain_tickets(fid):
        if t.assignee_email not in members or t.group is not TicketGroup.WIP:
            continue
        if (t.status or "").strip().lower() in _AGING_EXCLUDED_STATUSES:
            continue
        age = t.wip_age_seconds(now_ref)
        eng = BY_EMAIL.get(t.assignee_email)
        lvl = coloring.wip_age_level(age)
        rows.append({
            "key": t.id, "title": t.title,
            "assignee": eng["name"] if eng else (t.assignee_email or "—"),
            "status": t.status, "age_label": format_duration(age),
            "age_seconds": age, "url": JIRA_BASE + t.id,
            "level": lvl.value if lvl else None,
        })
    rows.sort(key=lambda r: (r["age_seconds"] is None, -(r["age_seconds"] or 0)))
    return rows


# Aggregatable numeric columns stored per (pulse, region, day).
_PDC_NUM = ["new_highest", "new_pr_mp", "new_ps5", "new_regular", "new_total",
            "closed_highest", "closed_pr_mp", "closed_ps5", "closed_total", "isdb_closed",
            "alerts_triggered", "alerts_ack", "alerts_resolved", "alerts_total",
            "mttr_sum", "mttr_n", "mtta_sum", "mtta_n"]

_PDC_GROUPS = [
    {"label": "ISReq New", "cols": [["new_highest", "Highest"], ["new_pr_mp", "PR/MP"],
                                    ["new_ps5", "ps5"], ["new_regular", "Regular"], ["new_total", "Total"]]},
    {"label": "ISReq Closed", "cols": [["closed_highest", "Highest"], ["closed_pr_mp", "PR/MP"],
                                       ["closed_ps5", "ps5"], ["closed_total", "Total"], ["closed_pct", "%"]]},
    {"label": "ISDB", "cols": [["isdb_closed", "Closed"], ["isdb_pct", "%"]]},
    {"label": "Alerts", "cols": [["alerts_triggered", "Trig"], ["alerts_ack", "Ack"],
                                 ["alerts_resolved", "Res"], ["alerts_total", "Total"],
                                 ["mttr", "MTTR"], ["mtta", "MTTA"]]},
    {"label": "", "cols": [["region_pct", "Region %"]]},
]


def build_pulse_counts(regions=None):
    """Per-day counts for the current pulse, summed over the selected region(s).

    Pure read: aggregates the pre-computed ``standup_pulse_day_count`` rows
    (selected regions for the value, all regions for the % denominators) and
    applies the original colour bands. No recompute — selecting AMER vs AMER+EMEA
    is just a different sum of stored rows."""
    from cmdb.apps.standup.models import PulseDayCount
    from standup_dashboard.domain.coloring import (
        count_level, ack_vs_triggered_level, resolve_rate_level, mttr_level, mtta_level,
        closed_vs_new_level, closed_vs_new_total_level, pr_mp_review_level)
    _ensure_httpx_stub()
    from standup_dashboard.services.counts import (
        ALERT_FATIGUE_WEEKDAY, ALERT_FATIGUE_WEEKEND, ALERT_FATIGUE_PULSE)
    from standup_dashboard.services.pulse import current_pulse

    pulse = (PulseDayCount.objects.order_by("-pulse_number")
             .values_list("pulse_number", flat=True).first())
    if pulse is None:
        return None
    all_rows = list(PulseDayCount.objects.filter(pulse_number=pulse).values())
    if not all_rows:
        return None
    available = {r["region"] for r in all_rows}
    sel = {r for r in (regions or []) if r} & available or available
    region_count = max(1, len(sel))

    # Merge by label (stable across regions) — regions can have a different number
    # of day-rows when their local "today" cap differs, so sort_order isn't aligned.
    by_label = {}
    for r in all_rows:
        slot = by_label.get(r["label"])
        if slot is None:
            slot = by_label[r["label"]] = {
                "label": r["label"], "is_weekend": r["is_weekend"], "is_total": r["is_total"],
                "ord": r["sort_order"], "sel": {k: 0 for k in _PDC_NUM},
                "glob": {k: 0 for k in _PDC_NUM}}
        slot["ord"] = min(slot["ord"], r["sort_order"])
        in_sel = r["region"] in sel
        for k in _PDC_NUM:
            v = r[k] or 0
            slot["glob"][k] += v
            if in_sel:
                slot["sel"][k] += v
    ordered = sorted(by_label.values(), key=lambda s: (s["is_total"], s["ord"]))

    def lvl(c):
        return c.value if c is not None else None

    def cell(v, c=None):
        return {"v": v, "c": c}

    def pct(x):
        return f"{round(x)}%" if x is not None else "—"

    def dur(x):
        return hours_label(int(x)) if x is not None else "—"

    rows_out = []
    prev_mttr = prev_mtta = None
    for s in ordered:
        v, g = s["sel"], s["glob"]
        cap = (ALERT_FATIGUE_PULSE if s["is_total"] else
               ALERT_FATIGUE_WEEKEND if s["is_weekend"] else ALERT_FATIGUE_WEEKDAY) * region_count
        trig, ack, res, tot = (v["alerts_triggered"], v["alerts_ack"],
                               v["alerts_resolved"], v["alerts_total"])
        mttr = (v["mttr_sum"] / v["mttr_n"]) if v["mttr_n"] else None
        mtta = (v["mtta_sum"] / v["mtta_n"]) if v["mtta_n"] else None
        closed_pct = (100.0 * v["closed_total"] / g["closed_total"]) if g["closed_total"] else None
        isdb_pct = (100.0 * v["isdb_closed"] / g["isdb_closed"]) if g["isdb_closed"] else None
        region_pct = (None if s["is_total"] else
                      ((100.0 * v["alerts_total"] / g["alerts_total"]) if g["alerts_total"] else None))

        def dcell(label, color, cur, prev):
            d = None
            if not s["is_total"] and cur is not None and prev is not None and abs(cur - prev) >= 1:
                d = {"dir": "down" if cur < prev else "up", "label": dur(abs(cur - prev))}
            return {"v": label, "c": color, "delta": d}

        values = {
            "new_highest": cell(v["new_highest"]), "new_pr_mp": cell(v["new_pr_mp"]),
            "new_ps5": cell(v["new_ps5"]), "new_regular": cell(v["new_regular"]),
            "new_total": cell(v["new_total"]),
            "closed_highest": cell(v["closed_highest"], lvl(closed_vs_new_level(v["closed_highest"], v["new_highest"]))),
            "closed_pr_mp": cell(v["closed_pr_mp"], lvl(pr_mp_review_level(v["new_pr_mp"], v["closed_pr_mp"]))),
            "closed_ps5": cell(v["closed_ps5"], lvl(closed_vs_new_level(v["closed_ps5"], v["new_ps5"]))),
            "closed_total": cell(v["closed_total"], lvl(closed_vs_new_total_level(v["closed_total"], v["new_total"], region_count))),
            "closed_pct": cell(pct(closed_pct)),
            "isdb_closed": cell(v["isdb_closed"]), "isdb_pct": cell(pct(isdb_pct)),
            "alerts_triggered": cell(trig, lvl(count_level(trig, cap))),
            "alerts_ack": cell(ack, lvl(ack_vs_triggered_level(trig, ack, region_count))),
            "alerts_resolved": cell(res, lvl(resolve_rate_level(res, ack))),
            "alerts_total": cell(tot, lvl(count_level(tot, cap))),
            "mttr": dcell(dur(mttr), lvl(mttr_level(mttr)), mttr, prev_mttr),
            "mtta": dcell(dur(mtta), lvl(mtta_level(mtta)), mtta, prev_mtta),
            "region_pct": cell("" if s["is_total"] else pct(region_pct)),
        }
        rows_out.append({"label": s["label"], "is_total": s["is_total"],
                         "is_weekend": s["is_weekend"], "values": values})
        if not s["is_total"]:
            if mttr is not None:
                prev_mttr = mttr
            if mtta is not None:
                prev_mtta = mtta

    today = datetime.now(_tz.utc).date()
    try:
        _, pstart, pend_excl = current_pulse(today)
        rng = f"{pstart:%a %d %b} – {(pend_excl - timedelta(days=1)):%a %d %b}"
    except Exception:
        rng = ""
    return {"type": "pulse_counts_daily", "title": "Pulse counts",
            "pulse": pulse, "range": rng, "groups": _PDC_GROUPS, "rows": rows_out,
            "regions": sorted(available), "selected": sorted(sel)}


def build_pulse_history():
    """Per-pulse, per-region counts with colour bands — its own Operations subtab,
    with region toggle buttons (consistent with the Stand up board)."""
    from cmdb.apps.standup.models import PulseSummary
    rows = []
    for s in PulseSummary.objects.order_by("-pulse_number", "region").values():
        rows.append({"pulse": s["pulse_number"], "region": s["region"], **_summary_row(s, regions=1)})
    if not rows:
        return [{"type": "kv", "title": "Pulse history", "values": {"status": "No pulse data."}}]
    regions = sorted({r["region"] for r in rows})
    return [{"type": "pulse_history", "title": "Pulse history (by pulse · region)",
             "rows": rows, "regions": regions}]


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
