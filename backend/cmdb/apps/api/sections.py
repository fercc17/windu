"""Page section builders for the unified windu shell.

Each builder returns a list of ``sections`` in the wire format the React shell
renders generically: ``kv`` (metric cards), ``table`` (rows), ``chart``
(recharts, with ``series``). The PAGES registry maps a page id ->
(title, member_only, builder). Member-only pages are the IS-only tabs.

Phase 1 wires real data from the unified tables where it exists and shows a
clear placeholder for the net-new tabs (CCNG, PS7+ Ingress, the change form).
"""
import statistics
from collections import defaultdict
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

# Pragma/Vanilla palette
BLUE, GREEN, RED, AMBER, PURPLE = "#0066cc", "#0e8420", "#c7162b", "#f99b11", "#7764d8"

# Browse tables return the full dataset; the React DataTable searches/sorts/paginates
# client-side (matching the original django-tables2 + django-filter experience).
BROWSE = 5000


# --- section helpers ---------------------------------------------------------
def kv(title, values):
    return {"type": "kv", "title": title, "values": values}


def table(title, data):
    return {"type": "table", "title": title, "data": data}


def chart(title, x, data, series, **kw):
    return {"type": "chart", "title": title, "x": x, "data": data, "series": series, **kw}


def note(title, text):
    return {"type": "kv", "title": title, "values": {"status": text}}


def _iso_week(dt):
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def _weekly_counts(datetimes):
    out = {}
    for d in datetimes:
        if d:
            k = _iso_week(d)
            out[k] = out.get(k, 0) + 1
    return out


def _model_rows(model, limit=25, order_by=None, only=None, **filters):
    qs = model.objects.filter(**filters)
    if order_by:
        qs = qs.order_by(order_by)
    qs = qs.values(*only) if only else qs.values()
    return list(qs[:limit])


def _group_count(model, field, label, limit=15, **filters):
    rows = (model.objects.filter(**filters)
            .exclude(**{f"{field}__isnull": True}).exclude(**{field: ""})
            .values(field).annotate(count=Count("pk")).order_by("-count")[:limit])
    return [{label: r[field], "count": r["count"]} for r in rows]


def _median_seconds(pairs):
    vals = [(e - s).total_seconds() for s, e in pairs if s and e and e >= s]
    return statistics.median(vals) if vals else None


def _fmt_dur(seconds):
    if seconds is None:
        return "—"
    m = int(seconds // 60)
    if m < 60:
        return f"{m}m"
    h, m = m // 60, m % 60
    if h < 24:
        return f"{h}h{m:02d}m"
    d, h = h // 24, h % 24
    return f"{d}d{h}h"


# --- Standup -----------------------------------------------------------------
def _ticket_category(status_category):
    s = (status_category or "").lower()
    if "done" in s or "complete" in s:
        return "done"
    if "progress" in s or "indeterminate" in s or "review" in s:
        return "wip"
    return "todo"


def standup_board(request, p):
    """Full-parity region board — delegates to the reused-domain builder."""
    from .standup_view import build_standup_board
    return build_standup_board()


# --- ISReq (jira_issue, ISREQ project) --------------------------------------
def isreq_overview(request, p):
    from cmdb.apps.jira.models import JiraIssue
    qs = JiraIssue.objects.filter(key__startswith="ISREQ-")
    new = _weekly_counts(qs.values_list("created_at", flat=True))
    closed = _weekly_counts(qs.exclude(resolved_at=None).values_list("resolved_at", flat=True))
    data, backlog = [], 0
    for w in sorted(set(new) | set(closed)):
        backlog += new.get(w, 0) - closed.get(w, 0)
        data.append({"week": w, "created": new.get(w, 0),
                     "closed": closed.get(w, 0), "backlog": backlog})
    data = data[-26:]

    ttc = [(r - c).total_seconds() / 86400
           for c, r in qs.exclude(resolved_at=None).values_list("created_at", "resolved_at")
           if c and r and r >= c]
    med = round(statistics.median(ttc), 1) if ttc else "—"
    p90 = round(statistics.quantiles(ttc, n=10)[8], 1) if len(ttc) >= 10 else "—"

    recent = _model_rows(JiraIssue, 25, order_by="-created_at",
                         only=["key", "title", "current_status", "current_priority",
                               "area", "assignee_name"], key__startswith="ISREQ-")
    return [
        kv("ISReq overview", {
            "issues": qs.count(),
            "open": qs.filter(resolved_at=None).count(),
            "highest_priority": qs.filter(current_priority="Highest").count(),
            "median_days_to_close": med,
            "p90_days_to_close": p90,
        }),
        chart("Created vs closed + open backlog (weekly)", "week", data, [
            {"key": "created", "label": "Created", "color": BLUE, "mark": "bar"},
            {"key": "closed", "label": "Closed", "color": GREEN, "mark": "bar"},
            {"key": "backlog", "label": "Open backlog", "color": RED, "mark": "line"},
        ]),
        table("By area", _group_count(JiraIssue, "area", "area", 15, key__startswith="ISREQ-")),
        table("Recent ISReq issues", recent),
    ]


# --- ISDB (standup_ticket, ISDB project, latest fetch) ----------------------
def isdb_overview(request, p):
    from cmdb.apps.standup.models import FetchSnapshot, StandupTicket
    latest = FetchSnapshot.objects.order_by("-id").first()
    if not latest:
        return [note("ISDB", "No standup fetches imported yet.")]
    qs = StandupTicket.objects.filter(fetch_id=latest.id, project_key="ISDB")
    done = qs.exclude(is_done_date=None).count()
    by_status = _group_count(StandupTicket, "status", "status", 15,
                             fetch_id=latest.id, project_key="ISDB")
    recent = _model_rows(StandupTicket, 25, order_by="-created",
                         only=["ticket_key", "title", "status", "priority",
                               "assignee_email"], fetch_id=latest.id, project_key="ISDB")
    return [
        kv("ISDB overview (latest fetch)", {
            "tickets": qs.count(), "done": done, "in_flight": qs.count() - done,
        }),
        table("By status", by_status),
        table("ISDB tickets", recent),
    ]


# --- PagerDuty ---------------------------------------------------------------
def pagerduty_overview(request, p):
    from cmdb.apps.pagerduty.models import PdIncident, PdAlert, PdLogEntry, PdUser
    incs = list(PdIncident.objects.values("created_at", "acknowledged_at", "resolved_at"))
    mtta = _median_seconds([(i["created_at"], i["acknowledged_at"]) for i in incs])
    mttr = _median_seconds([(i["created_at"], i["resolved_at"]) for i in incs])

    weekly = _weekly_counts(PdIncident.objects.values_list("created_at", flat=True))
    wk = [{"week": k, "incidents": v} for k, v in sorted(weekly.items())][-26:]

    # Weekly median MTTA/MTTR (minutes).
    wk_ack, wk_res = defaultdict(list), defaultdict(list)
    for i in incs:
        if not i["created_at"]:
            continue
        w = _iso_week(i["created_at"])
        if i["acknowledged_at"]:
            wk_ack[w].append((i["acknowledged_at"] - i["created_at"]).total_seconds())
        if i["resolved_at"]:
            wk_res[w].append((i["resolved_at"] - i["created_at"]).total_seconds())
    weeks = sorted(set(wk_ack) | set(wk_res))[-26:]
    dur = [{"week": w,
            "mtta_min": round(statistics.median(wk_ack[w]) / 60, 1) if wk_ack[w] else 0,
            "mttr_min": round(statistics.median(wk_res[w]) / 60, 1) if wk_res[w] else 0}
           for w in weeks]

    # SRE action load from the canonical log entries (ack/resolve/assign events).
    name_by_uid = {u.id: (u.name or u.email or u.id) for u in PdUser.objects.all()}
    load = {}
    for r in (PdLogEntry.objects.exclude(agent_user_id=None)
              .values("agent_user_id").annotate(n=Count("pk"))):
        nm = name_by_uid.get(r["agent_user_id"], r["agent_user_id"])
        load[nm] = load.get(nm, 0) + r["n"]
    sre = sorted(({"sre": k, "actions": v} for k, v in load.items()),
                 key=lambda x: -x["actions"])[:25]

    return [
        kv("PagerDuty load", {
            "incidents": PdIncident.objects.count(),
            "alerts": PdAlert.objects.count(),
            "resolved": PdIncident.objects.exclude(resolved_at=None).count(),
            "median_mtta": _fmt_dur(mtta),
            "median_mttr": _fmt_dur(mttr),
        }),
        chart("Incidents per week", "week", wk, [
            {"key": "incidents", "label": "Incidents", "color": RED, "mark": "bar"}]),
        chart("Median MTTA / MTTR (minutes, weekly)", "week", dur, [
            {"key": "mtta_min", "label": "MTTA (min)", "color": BLUE, "mark": "line"},
            {"key": "mttr_min", "label": "MTTR (min)", "color": AMBER, "mark": "line"}]),
        table("Top alert types", _group_count(PdAlert, "alertname", "alert", 15)),
        table("Alerts by cloud", _group_count(PdAlert, "cloud", "cloud", 12)),
        table("SRE action load (ack/resolve/assign events)", sre),
    ]


# --- CMDB --------------------------------------------------------------------
ENV_COLS = ["name", "region", "cloud", "env_type", "criticality_tier", "status",
            "team", "owner", "consumer_team", "service_class", "k8s_distribution",
            "gitops_enabled", "juju_controller", "updated_at"]


def cmdb_juju_models(request, p):
    from cmdb.apps.environments.models import Environment
    rows = _model_rows(Environment, BROWSE, order_by="name", only=ENV_COLS)
    return [
        kv("Juju models / environments", {
            "environments": Environment.objects.count(),
            "active": Environment.objects.filter(status="active").count(),
            "note": "Pull-from-GitHub button + last-fetch time: later phase",
        }),
        table("Environments", rows),
    ]


def cmdb_clouds(request, p):
    from cmdb.apps.environments.models import Environment
    return [
        kv("Clouds", {"distinct_clouds":
                      Environment.objects.exclude(cloud="").values("cloud").distinct().count(),
                      "note": "Pull-from-NetBox button + last-fetch time: later phase"}),
        table("Environments per cloud", _group_count(Environment, "cloud", "cloud", 30)),
        table("Environments per region", _group_count(Environment, "region", "region", 30)),
    ]


def cmdb_charms(request, p):
    from cmdb.apps.environments.models import CharmRelease
    return [
        kv("Charms", {"charm_releases": CharmRelease.objects.count()}),
        table("Charm releases", _model_rows(CharmRelease, BROWSE, order_by="-id")),
    ]


def cmdb_cia(request, p):
    from cmdb.apps.environments.models import Environment
    with_owner = Environment.objects.exclude(cia_owner="").exclude(cia_owner=None)
    return [
        kv("CIA assessment", {
            "environments": Environment.objects.count(),
            "with_cia_owner": with_owner.count(),
        }),
        table("Environments with CIA owners", _model_rows(
            Environment, BROWSE, order_by="name", only=["name", "cia_owner",
            "cia_risk_owner", "cia_custodian", "data_classification", "criticality_tier"])),
    ]


def cmdb_teams(request, p):
    from cmdb.apps.environments.models import Environment
    return [
        kv("Teams", {"distinct_teams":
                     Environment.objects.exclude(team="").values("team").distinct().count()}),
        table("Environments per team", _group_count(Environment, "team", "team", 40)),
    ]


def cmdb_ps6(request, p):
    from cmdb.apps.environments.models import Environment
    k8s = Environment.objects.exclude(k8s_distribution="").exclude(k8s_distribution=None)
    return [
        kv("PS6 ManSol K8s", {"k8s_environments": k8s.count()}),
        table("K8s distributions", _group_count(Environment, "k8s_distribution", "distribution", 20)),
        table("K8s environments", _model_rows(Environment, BROWSE, order_by="name",
              only=["name", "cloud", "region", "k8s_distribution", "status"],
              k8s_distribution__isnull=False)),
    ]


# --- IS Services -------------------------------------------------------------
def is_overview(request, p):
    from cmdb.apps.environments.models import Environment
    return [
        kv("IS Services overview", {"environments": Environment.objects.count()}),
        table("By service class", _group_count(Environment, "service_class", "service_class", 30)),
        table("By service primitive", _group_count(Environment, "service_primitive", "primitive", 20)),
    ]


def _service_class(title, service_class):
    def builder(request, p):
        from cmdb.apps.environments.models import Environment
        qs = Environment.objects.filter(service_class=service_class)
        return [
            kv(title, {"environments": qs.count()}),
            table(f"{title} environments", _model_rows(
                Environment, BROWSE, order_by="name",
                only=["name", "cloud", "region", "status", "team"],
                service_class=service_class)),
        ]
    return builder


def is_storage(request, p):
    from cmdb.apps.storage.models import StorageResource
    return [
        kv("Storage", {"storage_resources": StorageResource.objects.count()}),
        table("Storage resources", _model_rows(StorageResource, BROWSE)),
    ]


# --- GitOps ------------------------------------------------------------------
def gitops_juju(request, p):
    from cmdb.apps.environments.models import Environment
    qs = Environment.objects.filter(gitops_enabled=True)
    return [
        kv("GitOps-managed", {"environments": qs.count()}),
        table("GitOps environments", _model_rows(
            Environment, BROWSE, order_by="name",
            only=["name", "gitops_repo", "gitops_path", "cloud", "status"],
            gitops_enabled=True)),
    ]


def gitops_dora(request, p):
    # DORA now derives from the CANONICAL PagerDuty store (pd_incident) rather than
    # a separate dora_incidents copy — finishing the PagerDuty de-duplication.
    from cmdb.apps.pagerduty.models import PdIncident
    incs = list(PdIncident.objects.values("created_at", "resolved_at"))
    weekly = _weekly_counts(PdIncident.objects.values_list("created_at", flat=True))
    wk = [{"week": k, "incidents": v} for k, v in sorted(weekly.items())][-26:]
    mttr = _median_seconds([(i["created_at"], i["resolved_at"]) for i in incs])
    return [
        note("DORA source",
             "Derived from the canonical PagerDuty store (pd_incident) — no separate "
             "dora_incidents copy."),
        kv("DORA (incident-based)", {
            "incidents": PdIncident.objects.count(),
            "resolved": PdIncident.objects.exclude(resolved_at=None).count(),
            "median_time_to_restore": _fmt_dur(mttr),
        }),
        chart("Incidents per week", "week", wk, [
            {"key": "incidents", "label": "Incidents", "color": AMBER, "mark": "bar"}]),
        table("Recent incidents", _model_rows(
            PdIncident, 25, order_by="-created_at",
            only=["incident_number", "title", "urgency", "status",
                  "created_at", "resolved_at"])),
    ]


# --- Change management -------------------------------------------------------
def change_cab(request, p):
    from cmdb.apps.changes.models import Change
    return [
        kv("CAB", {"changes": Change.objects.count()}),
        table("By status", _group_count(Change, "status", "status", 20)),
        table("Change requests", _model_rows(Change, BROWSE, order_by="-id")),
    ]


def change_maintenance(request, p):
    from cmdb.apps.maintenance.models import MaintenanceWindow
    from cmdb.apps.changes.models import StandardMaintenanceWindow
    return [
        kv("Maintenance windows", {
            "windows": MaintenanceWindow.objects.count(),
            "standard_windows": StandardMaintenanceWindow.objects.count(),
        }),
        table("Standard maintenance windows", _model_rows(StandardMaintenanceWindow, BROWSE)),
        table("Scheduled windows", _model_rows(MaintenanceWindow, BROWSE, order_by="-id")),
    ]


def change_new(request, p):
    return [note("New change request",
                 "The CR form is being ported from the classic CMDB UI — wired in a later phase.")]


def _placeholder(msg):
    def builder(request, p):
        return [note("Coming soon", msg)]
    return builder


# --- registry: id -> (title, member_only, builder) --------------------------
PAGES = {
    # IS-only
    "standup": ("Stand up", True, standup_board),
    "isreq": ("ISReq", True, isreq_overview),
    "isdb": ("ISDB", True, isdb_overview),
    "pagerduty": ("PagerDuty", True, pagerduty_overview),
    # CMDB
    "cmdb_juju": ("Juju models", False, cmdb_juju_models),
    "cmdb_clouds": ("Clouds", False, cmdb_clouds),
    "cmdb_charms": ("Charms", False, cmdb_charms),
    "cmdb_cia": ("CIA Assessment", False, cmdb_cia),
    "cmdb_teams": ("Teams", False, cmdb_teams),
    "cmdb_ps6": ("PS6 ManSol K8s", False, cmdb_ps6),
    # IS Services
    "is_overview": ("Overview", False, is_overview),
    "is_juju": ("Juju", False, _service_class("Juju", "machine_model")),
    "is_vmaas": ("VM aaS", False, _service_class("VM aaS", "machine_model")),
    "is_dbaas": ("DBaaS", False, _service_class("DBaaS", "database")),
    "is_ck8saas": ("Ck8s aaS", False, _service_class("Ck8s aaS", "kubernetes_cluster")),
    "is_jenkinsaas": ("Jenkins aaS", False, _service_class("Jenkins aaS", "kubernetes_cluster")),
    "is_builders": ("Builders", False, _service_class("Builders", "machine_model")),
    "is_storage": ("Storage", False, is_storage),
    "is_ccng": ("CCNG", False, _placeholder(
        "Cross Cloud Network Gateway — NEW capability, not in the legacy apps. To be built.")),
    "is_ps7ingress": ("PS7+ Ingress", False, _placeholder(
        "PS7+ Ingress — NEW. The unowned juju models that are these will be classified here.")),
    # GitOps
    "gitops_juju": ("Juju models", False, gitops_juju),
    "gitops_dora": ("DORA Metrics", False, gitops_dora),
    # Change management
    "change_maintenance": ("Maintenance windows", False, change_maintenance),
    "change_new": ("New change requests", False, change_new),
    "change_cab": ("CAB", False, change_cab),
}
