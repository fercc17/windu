"""One-shot ETL: copy the three legacy databases into the unified windu schema.

Sources (same Postgres server, separate databases):
  * isreq_analytics  -> schemas ``isreq`` (Jira) and ``pd`` (PagerDuty, canonical)
  * standup_dashboard -> schema ``public`` (standup state)

De-duplication: PagerDuty is loaded ONLY from ``isreq_analytics.pd`` (the rich,
canonical model). standup-dashboard's own ``alert``/``incident`` tables and
cmdb's DORA incidents are NOT loaded here — they derive from the canonical
``pd_*`` tables. isreq's ``chg`` stub is skipped (canonical change management is
cmdb's ``changes``/``maintenance``).

Each target table is TRUNCATEd then bulk-loaded, so the command is re-runnable.

Usage:
    manage.py etl_import                 # load everything
    manage.py etl_import --only pd jira  # load a subset (pd|jira|standup)
"""
from __future__ import annotations

import psycopg2
from psycopg2.extras import execute_values
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


# (target_table, [target_cols...], source_sql, {json_cols})
# Source SQL must SELECT columns in the exact target order; jsonb columns are
# selected ::text and re-cast with %s::jsonb on insert.
PD_TABLES = [
    ("pd_user", ["id", "name", "email", "region"],
     "SELECT id,name,email,region FROM pd.pd_user", set()),
    ("pd_team", ["id", "name"],
     "SELECT id,name FROM pd.pd_team", set()),
    ("pd_escalation_policy", ["id", "name"],
     "SELECT id,name FROM pd.pd_escalation_policy", set()),
    ("pd_service", ["id", "name", "team_id", "escalation_policy_id"],
     "SELECT id,name,team_id,escalation_policy_id FROM pd.pd_service", set()),
    ("pd_incident",
     ["id", "incident_number", "title", "urgency", "status", "service_id",
      "escalation_policy_id", "team_id", "created_at", "acknowledged_at",
      "resolved_at", "assigned_user_id", "synced_at"],
     "SELECT id,incident_number,title,urgency,status,service_id,"
     "escalation_policy_id,team_id,created_at,acknowledged_at,resolved_at,"
     "assigned_user_id,synced_at FROM pd.pd_incident", set()),
    ("pd_alert",
     ["id", "incident_id", "summary", "alertname", "cloud", "juju_model",
      "juju_model_uuid", "charm", "juju_unit", "severity", "created_at",
      "raw_details", "synced_at"],
     "SELECT id,incident_id,summary,alertname,cloud,juju_model,juju_model_uuid,"
     "charm,juju_unit,severity,created_at,raw_details::text,synced_at "
     "FROM pd.pd_alert", {"raw_details"}),
    ("pd_log_entry", ["id", "incident_id", "type", "at", "agent_user_id"],
     "SELECT id,incident_id,type,at,agent_user_id FROM pd.pd_log_entry", set()),
    ("pd_sync_state", ["resource", "last_sync_at", "last_full_sync_at", "note"],
     "SELECT resource,last_sync_at,last_full_sync_at,note FROM pd.pd_sync_state", set()),
]

JIRA_TABLES = [
    ("jira_user", ["account_id", "display_name", "region", "is_external"],
     "SELECT account_id,display_name,region,is_external FROM isreq.users", set()),
    ("jira_issue",
     ["key", "jira_id", "title", "created_at", "resolved_at", "current_status",
      "current_priority", "assignee_account_id", "assignee_name",
      "reporter_account_id", "reporter_name", "area", "sub_area", "pulse",
      "is_pr_mp", "labels", "jira_updated_at", "synced_at"],
     "SELECT key,jira_id,title,created_at,resolved_at,current_status,"
     "current_priority,assignee_account_id,assignee_name,reporter_account_id,"
     "reporter_name,area,sub_area,pulse,is_pr_mp,labels::text,jira_updated_at,"
     "synced_at FROM isreq.issues", {"labels"}),
    ("jira_issue_label", ["issue_key", "label"],
     "SELECT issue_key,label FROM isreq.issue_labels", set()),
    ("jira_changelog",
     ["id", "issue_key", "field", "from_value", "to_value", "changed_at",
      "author_account_id", "author_name"],
     "SELECT id,issue_key,field,from_value,to_value,changed_at,"
     "author_account_id,author_name FROM isreq.changelog", set()),
    ("jira_worklog",
     ["id", "issue_key", "time_spent_seconds", "started_at", "synced_at"],
     "SELECT id,issue_key,time_spent_seconds,started_at,synced_at "
     "FROM isreq.worklogs", set()),
    ("jira_priority_interval", ["issue_key", "priority", "valid_from", "valid_to"],
     "SELECT issue_key,priority,valid_from,valid_to FROM isreq.priority_intervals", set()),
    ("jira_status_interval", ["issue_key", "status", "valid_from", "valid_to"],
     "SELECT issue_key,status,valid_from,valid_to FROM isreq.status_intervals", set()),
    ("jira_sync_state", ["resource", "last_sync_at", "last_full_sync_at", "note"],
     "SELECT resource,last_sync_at,last_full_sync_at,note FROM isreq.sync_state", set()),
]

# standup-dashboard public schema -> standup_* (alert/incident intentionally omitted)
STANDUP_TABLES = [
    ("standup_fetch_snapshot",
     ["id", "fetched_at", "jira_ok", "pagerduty_ok", "ical_ok", "raw_path", "partial"],
     "SELECT id,fetched_at,jira_ok,pagerduty_ok,ical_ok,raw_path,partial "
     "FROM fetch_snapshot", set()),
    ("standup_ticket",
     ["fetch_id", "ticket_key", "project_key", "title", "status", "priority",
      "labels_json", "assignee_email", "sprint_id", "is_done_date", "created",
      "status_category", "reporter_email", "wip_since", "estimate_seconds",
      "spent_seconds"],
     "SELECT fetch_id,id,project_key,title,status,priority,labels_json,"
     "assignee_email,sprint_id,is_done_date,created,status_category,"
     "reporter_email,wip_since,estimate_seconds,spent_seconds FROM ticket", set()),
    ("standup_touch_event",
     ["fetch_id", "ticket_id", "engineer_email", "kind", "at", "seconds"],
     "SELECT fetch_id,ticket_id,engineer_email,kind,at,seconds FROM touch_event", set()),
    ("standup_github_pr",
     ["fetch_id", "engineer_email", "created", "merged", "updated", "reviewed",
      "created_24h", "merged_24h", "updated_24h", "reviewed_24h", "created_today",
      "merged_today", "updated_today", "reviewed_today"],
     "SELECT fetch_id,engineer_email,created,merged,updated,reviewed,created_24h,"
     "merged_24h,updated_24h,reviewed_24h,created_today,merged_today,updated_today,"
     "reviewed_today FROM github_pr", set()),
    ("standup_calendar_avail",
     ["fetch_id", "engineer_email", "busy_seconds", "open_seconds", "pto_seconds",
      "sd_days", "busy_today", "open_today", "busy_24h", "open_24h", "pto_days"],
     "SELECT fetch_id,engineer_email,busy_seconds,open_seconds,pto_seconds,sd_days,"
     "busy_today,open_today,busy_24h,open_24h,pto_days FROM calendar_avail", set()),
    ("standup_pulse",
     ["fetch_id", "project_key", "sprint_id", "name", "start", "end", "state"],
     'SELECT fetch_id,project_key,sprint_id,name,"start","end",state FROM pulse', set()),
    ("standup_pulse_summary",
     ["pulse_number", "region", "new_highest", "new_pr_mp", "new_ps5", "new_regular",
      "new_total", "closed_highest", "closed_pr_mp", "closed_ps5", "closed_total",
      "isdb_closed", "alerts_triggered", "alerts_ack", "alerts_resolved",
      "alerts_total", "alert_mttr_sum", "alert_mttr_n", "alert_mtta_sum",
      "alert_mtta_n", "ticket_cycle_sum", "ticket_cycle_n", "breakdowns_json",
      "updated_at"],
     "SELECT pulse_number,region,new_highest,new_pr_mp,new_ps5,new_regular,new_total,"
     "closed_highest,closed_pr_mp,closed_ps5,closed_total,isdb_closed,alerts_triggered,"
     "alerts_ack,alerts_resolved,alerts_total,alert_mttr_sum,alert_mttr_n,"
     "alert_mtta_sum,alert_mtta_n,ticket_cycle_sum,ticket_cycle_n,breakdowns_json,"
     "updated_at FROM pulse_summary", set()),
    ("standup_open_summary_count",
     ["fetch_id", "highest", "ps5", "ps5_highest", "pr_mp", "escalated", "ongoing_alerts"],
     "SELECT fetch_id,highest,ps5,ps5_highest,pr_mp,escalated,ongoing_alerts "
     "FROM open_summary_count", set()),
    ("standup_weekend_oncall",
     ["fetch_id", "engineer_email", "weekend_start", "weekend_end"],
     "SELECT fetch_id,engineer_email,weekend_start,weekend_end FROM weekend_oncall", set()),
    ("standup_raw_snapshot", ["fetch_id", "name", "payload"],
     "SELECT fetch_id,name,payload::text FROM raw_snapshot", {"payload"}),
    ("standup_role_schedule", ["engineer_email", "weekday", "role", "updated_at"],
     "SELECT engineer_email,weekday,role,updated_at FROM role_schedule", set()),
    ("standup_role_override",
     ["engineer_email", "role", "effective_date", "expires_at", "created_at"],
     "SELECT engineer_email,role,effective_date,expires_at,created_at "
     "FROM role_override", set()),
    ("standup_day_note", ["engineer_email", "weekday", "note", "updated_at", "note_date"],
     "SELECT engineer_email,weekday,note,updated_at,note_date FROM day_note", set()),
    ("standup_roster_addition", ["email", "name", "region", "github_login", "created_at"],
     "SELECT email,name,region,github_login,created_at FROM roster_addition", set()),
    ("standup_region_override", ["email", "region", "updated_at"],
     "SELECT email,region,updated_at FROM region_override", set()),
    ("standup_ui_state", ["key", "value", "updated_at"],
     "SELECT key,value,updated_at FROM ui_state", set()),
    ("standup_jira_account", ["email", "account_id"],
     "SELECT email,account_id FROM jira_account", set()),
]

# CMDB lives in its own legacy DB with a schema IDENTICAL to windu's (windu was
# lifted from the same is-cmdb models), so these are copied by column
# introspection rather than hand-written SELECTs. Loaded in one transaction with
# Django's DEFERRABLE INITIALLY DEFERRED FKs, so table order doesn't matter.
CMDB_TABLES = [
    "environments", "environment_dependencies", "cloud_capacity",
    "placement_history", "charm_release",
    "nodes", "node_interfaces", "node_cables", "node_switch_connections",
    "cloud_stakeholders",
    "changes", "change_targets", "change_affected_environments",
    "change_approvals", "change_notifications", "change_templates",
    "standard_maintenance_windows", "maintenance_windows",
    "maintenance_notification_channels", "maintenance_window_environments",
    "storage_resources", "storage_environment_access",
    "dora_incidents", "dora_deployment_events",
]

DOMAINS = {
    "pd": ("isreq_analytics", PD_TABLES),
    "jira": ("isreq_analytics", JIRA_TABLES),
    "standup": ("standup_dashboard", STANDUP_TABLES),
    "cmdb": ("cmdb", None),  # None -> dynamic column-introspecting copy
}


class Command(BaseCommand):
    help = "Copy the three legacy databases into the unified windu schema."

    def add_arguments(self, parser):
        parser.add_argument("--only", nargs="+", choices=list(DOMAINS),
                            help="Limit to these domains (default: all).")
        parser.add_argument("--source-host", default="127.0.0.1")
        parser.add_argument("--source-port", default="5432")
        parser.add_argument("--source-user", default="cmdb")
        parser.add_argument("--source-password", default="cmdb")

    def handle(self, *args, **opts):
        domains = opts["only"] or list(DOMAINS)
        src_conf = dict(host=opts["source_host"], port=opts["source_port"],
                        user=opts["source_user"], password=opts["source_password"])

        dst = settings.DATABASES["default"]
        dst_conn = psycopg2.connect(
            host=dst["HOST"] or "127.0.0.1", port=dst["PORT"] or "5432",
            user=dst["USER"], password=dst["PASSWORD"], dbname=dst["NAME"])

        grand_total = 0
        try:
            for domain in domains:
                src_db, tables = DOMAINS[domain]
                self.stdout.write(self.style.MIGRATE_HEADING(
                    f"\n== {domain}  (source db: {src_db}) =="))
                src_conn = psycopg2.connect(dbname=src_db, **src_conf)
                try:
                    if tables is None:  # dynamic copy (cmdb)
                        grand_total += self._copy_dynamic(src_conn, dst_conn, CMDB_TABLES)
                    else:
                        for target, cols, sql, json_cols in tables:
                            n = self._copy(src_conn, dst_conn, sql, target, cols, json_cols)
                            grand_total += n
                            self.stdout.write(f"  {target:<32} {n:>8,} rows")
                finally:
                    src_conn.close()
            self.stdout.write(self.style.SUCCESS(
                f"\nETL complete — {grand_total:,} rows loaded into '{dst['NAME']}'."))
        finally:
            dst_conn.close()

        # The per-day pulse counts are derived (not copied), so recompute & store
        # them whenever standup or PagerDuty data changed (they feed the table).
        if {"standup", "pd"} & set(domains):
            from django.core.management import call_command
            self.stdout.write(self.style.MIGRATE_HEADING("\n== derived: pulse day counts =="))
            try:
                call_command("standup_compute_day_counts")
            except Exception as exc:  # don't fail the whole ETL on a derived step
                self.stdout.write(self.style.WARNING(f"  pulse day counts skipped: {exc}"))

    def _copy_dynamic(self, src_conn, dst_conn, tables):
        """Copy same-named tables by column introspection, in one deferred-FK txn."""
        def columns(conn, table):
            with conn.cursor() as c:
                c.execute(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
                    (table,))
                return c.fetchall()

        present = [t for t in tables if columns(src_conn, t) and columns(dst_conn, t)]
        total = 0
        try:  # dst_conn is already non-autocommit; one txn, deferred FKs
            with dst_conn.cursor() as dc:
                dc.execute("SET CONSTRAINTS ALL DEFERRED")
                dc.execute("TRUNCATE " + ",".join(present) + " RESTART IDENTITY CASCADE")
                for t in present:
                    dst_cols = columns(dst_conn, t)
                    src_names = {n for n, _ in columns(src_conn, t)}
                    cols = [(n, dt) for n, dt in dst_cols if n in src_names]
                    names = [n for n, _ in cols]
                    json_cols = {n for n, dt in cols if dt == "jsonb"}
                    sel = ",".join(f'"{n}"::text' if n in json_cols else f'"{n}"'
                                   for n in names)
                    with src_conn.cursor() as sc:
                        sc.execute(f'SELECT {sel} FROM "{t}"')
                        rows = sc.fetchall()
                    if rows:
                        placeholders = ",".join(
                            "%s::jsonb" if n in json_cols else "%s" for n in names)
                        execute_values(
                            dc, f'INSERT INTO "{t}" ({",".join(chr(34)+n+chr(34) for n in names)}) '
                                f"VALUES %s", rows, template=f"({placeholders})", page_size=1000)
                    total += len(rows)
                    self.stdout.write(f"  {t:<32} {len(rows):>8,} rows")
            dst_conn.commit()
        except Exception:
            dst_conn.rollback()
            raise
        return total

    @staticmethod
    def _copy(src_conn, dst_conn, src_sql, target, cols, json_cols):
        with src_conn.cursor() as sc:
            sc.execute(src_sql)
            rows = sc.fetchall()
        placeholders = ",".join(
            "%s::jsonb" if c in json_cols else "%s" for c in cols)
        template = f"({placeholders})"
        collist = ",".join(f'"{c}"' for c in cols)  # quote reserved words (start/end/key)
        with dst_conn.cursor() as dc:
            dc.execute(f"TRUNCATE {target} RESTART IDENTITY")
            if rows:
                execute_values(
                    dc, f"INSERT INTO {target} ({collist}) VALUES %s",
                    rows, template=template, page_size=1000)
        dst_conn.commit()
        return len(rows)
