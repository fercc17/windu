"""Unified-shell API: identity + per-page sections + per-source refresh."""
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .identity import current_identity
from .sections import PAGES

# Each shell tab maps to a data source. "Refresh" re-pulls that domain from its
# legacy DB into the unified schema (etl_import --only <domain>); the legacy apps
# themselves sync upstream (GitHub/NetBox/Jira/PagerDuty) on their own schedule.
SOURCE_MAP = {
    "standup": {"domain": "standup", "label": "Standup (Jira · PagerDuty · GitHub)"},
    "jira": {"domain": "jira", "label": "Jira (ISReq / ISDB)"},
    "pd": {"domain": "pd", "label": "PagerDuty"},
    "cmdb": {"domain": "cmdb", "label": "CMDB (infrastructure · NetBox)"},
}


def _iso(v):
    return v if isinstance(v, str) or v is None else v.isoformat()


def get_last_updated(source):
    """Best available 'data freshness' timestamp for a source."""
    try:
        if source == "standup":
            from cmdb.apps.standup.models import FetchSnapshot
            f = FetchSnapshot.objects.order_by("-id").first()
            return _iso(f.fetched_at) if f else None
        if source == "jira":
            from cmdb.apps.jira.models import JiraSyncState
            s = JiraSyncState.objects.exclude(last_sync_at=None).order_by("-last_sync_at").first()
            return _iso(s.last_sync_at) if s else None
        if source == "pd":
            from cmdb.apps.pagerduty.models import PdSyncState
            s = PdSyncState.objects.exclude(last_sync_at=None).order_by("-last_sync_at").first()
            return _iso(s.last_sync_at) if s else None
        if source == "cmdb":
            from django.db.models import Max
            from cmdb.apps.environments.models import Environment
            return _iso(Environment.objects.aggregate(m=Max("declared_at"))["m"])
    except Exception:
        return None
    return None


ROLES = ["PVG", "BVG", "GEN", "Project", "OFF"]
WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI"]


@api_view(["GET", "POST"])
def standup_schedule(request):
    """GET the weekly role grid (engineer x weekday); POST {engineer_email,
    weekday, role} to upsert one cell (row-versioned: latest updated_at wins)."""
    from django.utils import timezone
    from cmdb.apps.standup.models import RoleSchedule
    from .standup_view import _dt

    if request.method == "POST":
        email = request.data.get("engineer_email")
        weekday = request.data.get("weekday")
        role = request.data.get("role")
        if not email or weekday not in WEEKDAYS or role not in ROLES:
            return Response({"error": "engineer_email, weekday, role required"}, status=400)
        now = timezone.now().isoformat()
        rs = (RoleSchedule.objects.filter(engineer_email=email, weekday=weekday)
              .order_by("-updated_at").first())
        if rs:
            rs.role, rs.updated_at = role, now
            rs.save(update_fields=["role", "updated_at"])
        else:
            RoleSchedule.objects.create(engineer_email=email, weekday=weekday,
                                        role=role, updated_at=now)
        return Response({"ok": True})

    # Rich grid like the original: per-region engineer columns, this-week (editable)
    # + next-week (read-only) day rows, weekly defaults, per-date notes, and the
    # active today-overrides (#71).
    from datetime import timedelta
    from cmdb.apps.standup.models import DayNote, RoleOverride
    from cmdb.apps.standup.roster import ROSTER, REGIONS

    sched_engs = [e for e in ROSTER if not e["manager"] and not e["global"]]
    regions = []
    for rk in REGIONS:
        engs = [{"email": e["email"], "name": e["name"]} for e in sched_engs if rk in e["regions"]]
        if engs:
            regions.append({"key": rk, "engineers": engs})

    now = timezone.now()
    today = now.date()
    monday = today - timedelta(days=today.weekday())
    week = []
    for wk in range(2):
        for i, slot in enumerate(WEEKDAYS):
            d = monday + timedelta(days=wk * 7 + i)
            week.append({"slot": slot, "dow": d.strftime("%a"), "date": d.strftime("%b %d"),
                         "iso": d.isoformat(), "role_editable": wk == 0,
                         "note_editable": d >= today, "new_week": wk == 1 and i == 0})

    grid = {}
    for rs in RoleSchedule.objects.order_by("updated_at").values("engineer_email", "weekday", "role"):
        grid[(rs["engineer_email"], rs["weekday"])] = rs["role"]
    defaults = {e["email"]: {wd: grid.get((e["email"], wd), "GEN") for wd in WEEKDAYS}
                for e in sched_engs}

    notes = {}
    for dn in DayNote.objects.order_by("updated_at").values("engineer_email", "note_date", "note"):
        if dn["note"]:
            notes.setdefault(dn["engineer_email"], {})[dn["note_date"]] = dn["note"]

    overrides = {}
    for ro in RoleOverride.objects.order_by("created_at").values("engineer_email", "role", "expires_at"):
        exp = _dt(ro["expires_at"])
        if exp is None or exp > now:
            overrides[ro["engineer_email"]] = ro["role"]

    return Response({
        "roles": ROLES, "regions": regions, "week": week,
        "defaults": defaults, "notes": notes, "overrides": overrides,
    })


@api_view(["POST"])
def standup_role(request):
    """Set (or clear, with role='') an engineer's today-only role override."""
    from datetime import timedelta
    from django.utils import timezone
    from cmdb.apps.standup.models import RoleOverride
    from .standup_view import _dt

    email = request.data.get("engineer_email")
    role = request.data.get("role")
    if not email:
        return Response({"error": "engineer_email required"}, status=400)
    now = timezone.now()
    if role in ("", None):  # clear: expire any active override now
        for ro in RoleOverride.objects.filter(engineer_email=email):
            exp = _dt(ro.expires_at)
            if exp is None or exp > now:
                ro.expires_at = now.isoformat()
                ro.save(update_fields=["expires_at"])
        return Response({"ok": True, "cleared": True})
    if role not in ROLES:
        return Response({"error": "a valid role (or '' to clear) required"}, status=400)
    RoleOverride.objects.create(
        engineer_email=email, role=role, effective_date=now.date().isoformat(),
        expires_at=(now + timedelta(days=1)).isoformat(), created_at=now.isoformat())
    return Response({"ok": True})


@api_view(["POST"])
def standup_note(request):
    """Set/clear a free-text day note on a specific date (#day-notes)."""
    from django.utils import timezone
    from cmdb.apps.standup.models import DayNote

    from datetime import date as _date_cls
    email = request.data.get("engineer_email")
    note_date = request.data.get("note_date")
    note = (request.data.get("note") or "").strip()
    if not email or not note_date:
        return Response({"error": "engineer_email and note_date required"}, status=400)
    try:
        wd_idx = _date_cls.fromisoformat(note_date).weekday()
    except ValueError:
        return Response({"error": "invalid note_date"}, status=400)
    weekday = WEEKDAYS[wd_idx] if wd_idx < len(WEEKDAYS) else ""
    now = timezone.now().isoformat()
    dn = (DayNote.objects.filter(engineer_email=email, note_date=note_date)
          .order_by("-updated_at").first())
    if dn:
        dn.note, dn.updated_at = note, now
        dn.save(update_fields=["note", "updated_at"])
    else:
        DayNote.objects.create(engineer_email=email, weekday=weekday, note=note,
                               note_date=note_date, updated_at=now)
    return Response({"ok": True})


@api_view(["GET"])
def standup_offenders(request):
    """Repeat-offender alerts (#146): chronic alert signatures, year-history backed."""
    from .standup_view import build_offenders
    return Response({"offenders": build_offenders()})


@api_view(["GET"])
def standup_aging(request):
    """Aging WIP (#147): in-progress tickets for the selected region(s), oldest first."""
    from .standup_view import build_aging
    regions = request.GET.getlist("regions") or request.GET.getlist("region")
    return Response({"aging": build_aging(regions)})


@api_view(["GET"])
def standup_pulse_counts(request):
    """Per-day pulse counts (pre-computed), summed over the selected region(s)."""
    from .standup_view import build_pulse_counts
    regions = request.GET.getlist("regions") or request.GET.getlist("region")
    return Response(build_pulse_counts(regions) or {"rows": []})


@api_view(["GET", "POST"])
def standup_focus(request):
    """GET -> {focus}; POST {value:'on'|'off'} to persist the Highest/PS5/PR-MP focus."""
    from django.utils import timezone
    from cmdb.apps.standup.models import UiState
    from .standup_view import FOCUS_KEY, get_focus

    if request.method == "POST":
        on = request.data.get("value") in ("on", True, "true", 1, "1")
        now = timezone.now().isoformat()
        row = UiState.objects.filter(key=FOCUS_KEY).order_by("-updated_at").first()
        if row:
            row.value, row.updated_at = ("on" if on else "off"), now
            row.save(update_fields=["value", "updated_at"])
        else:
            UiState.objects.create(key=FOCUS_KEY, value=("on" if on else "off"), updated_at=now)
        return Response({"ok": True, "focus": on})
    return Response({"focus": get_focus()})


@api_view(["POST"])
def standup_paste(request):
    """Parse a tab-separated schedule paste (the manager's spreadsheet) and apply
    the weekly roles + day notes it encodes (#71). Returns a small summary."""
    from django.utils import timezone
    from standup_dashboard import config
    from cmdb.apps.standup.models import RoleSchedule, DayNote
    from .standup_view import _ensure_schedule_importable

    text = request.data.get("paste") or ""
    if not text.strip():
        return Response({"error": "empty paste"}, status=400)
    if not config.REGIONS:
        config._set_roster(config._SEED_ROSTER)
    _ensure_schedule_importable()
    from standup_dashboard.services.schedule import parse_schedule_paste

    now = timezone.now()
    now_iso = now.isoformat()
    actions, errors = parse_schedule_paste(text, now)
    roles = notes = 0
    for a in actions:
        if a.role is not None and a.weekday in WEEKDAYS and a.role in ROLES:
            rs = (RoleSchedule.objects.filter(engineer_email=a.email, weekday=a.weekday)
                  .order_by("-updated_at").first())
            if rs:
                rs.role, rs.updated_at = a.role, now_iso
                rs.save(update_fields=["role", "updated_at"])
            else:
                RoleSchedule.objects.create(engineer_email=a.email, weekday=a.weekday,
                                            role=a.role, updated_at=now_iso)
            roles += 1
        if a.note is not None and a.note_date:
            dn = (DayNote.objects.filter(engineer_email=a.email, note_date=a.note_date)
                  .order_by("-updated_at").first())
            if dn:
                dn.note, dn.updated_at = a.note, now_iso
                dn.save(update_fields=["note", "updated_at"])
            else:
                DayNote.objects.create(engineer_email=a.email, weekday=a.weekday,
                                       note=a.note, note_date=a.note_date, updated_at=now_iso)
            notes += 1
    return Response({"roles": roles, "notes": notes, "errors": errors})


@api_view(["GET", "POST"])
def refresh(request, source):
    """GET -> {last_updated}; POST -> re-sync this source into the unified schema."""
    conf = SOURCE_MAP.get(source)
    if conf is None:
        return Response({"error": f"unknown source '{source}'"}, status=404)
    if request.method == "POST":
        from io import StringIO
        from django.core.management import call_command
        try:
            call_command("etl_import", only=[conf["domain"]], stdout=StringIO())
        except Exception as exc:
            return Response({"ok": False, "error": str(exc)}, status=500)
        return Response({"ok": True, "source": source, "last_updated": get_last_updated(source)})
    return Response({"source": source, "label": conf["label"],
                     "last_updated": get_last_updated(source)})


@api_view(['GET'])
def me(request):
    """Stub identity for the React shell (drives IS-only tab visibility)."""
    return Response(current_identity(request))


@api_view(['GET'])
def page(request, page_id):
    """Return {title, sections:[...]} for a page id, gating IS-only pages."""
    entry = PAGES.get(page_id)
    if entry is None:
        return Response({"error": f"unknown page '{page_id}'"}, status=404)
    title, member_only, builder = entry

    if member_only and not current_identity(request)['is_is_member']:
        return Response({"error": "forbidden", "title": title,
                         "sections": [{"type": "kv", "title": title,
                                       "values": {"access": "IS members only"}}]},
                        status=403)
    try:
        sections = builder(request, request.GET)
    except Exception as exc:  # keep one bad page from 500-ing the shell
        sections = [{"type": "kv", "title": title,
                     "values": {"error": str(exc)}}]
    return Response({"title": title, "sections": sections})
