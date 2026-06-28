"""Raw PagerDuty JSON -> ``pd`` row dicts (pure, unit-testable, no network).

Ack/resolve times and the on-call SRE are derived from the incident timeline
(``log_entries``); the cloud/model/charm/alert-type signal is derived from each
alert payload via ``domain.pd_classify``. The raw alert payload is kept on the row
(``raw_details``) so the classifier can be re-run as rules improve, no re-sync.
"""

from __future__ import annotations

from datetime import datetime, timezone

from isreq_dashboard.domain.pd_classify import classify
from isreq_dashboard.domain.regions import UNKNOWN

# PagerDuty log-entry agents are typed; only user agents are SREs we attribute to.
_USER_REF = "user_reference"


def parse_dt(s: str | None) -> datetime | None:
    """Parse a PagerDuty ISO-8601 timestamp to tz-aware UTC. ``None``/'' -> None."""
    if not s:
        return None
    iso = s.strip()
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    return datetime.fromisoformat(iso).astimezone(timezone.utc)


def _ref_id(obj: dict | None) -> str | None:
    return obj.get("id") if isinstance(obj, dict) else None


def _norm_log_type(t: str | None) -> str | None:
    """``acknowledge_log_entry`` -> ``acknowledge``; leave already-short types alone."""
    if not t:
        return None
    return t[:-10] if t.endswith("_log_entry") else t


def _assigned_user_id(raw: dict) -> str | None:
    """First current assignee (the paged/responsible SRE), if any."""
    for a in raw.get("assignments", []) or []:
        uid = _ref_id(a.get("assignee"))
        if uid:
            return uid
    return None


def incident_row(raw: dict) -> dict:
    """Base incident row. ``acknowledged_at``/``resolved_at`` are filled by the sync
    from the derived log-entry times (``derive_times``)."""
    teams = raw.get("teams") or []
    return {
        "id": raw["id"],
        "incident_number": raw.get("incident_number"),
        "title": raw.get("title"),
        "urgency": raw.get("urgency"),
        "status": raw.get("status"),
        "service_id": _ref_id(raw.get("service")),
        "escalation_policy_id": _ref_id(raw.get("escalation_policy")),
        "team_id": _ref_id(teams[0]) if teams else None,
        "created_at": parse_dt(raw.get("created_at")),
        "assigned_user_id": _assigned_user_id(raw),
    }


def log_entry_rows(incident_id: str, raw_entries: list[dict]) -> list[dict]:
    """Normalized timeline rows. ``agent_user_id`` is set only for user agents (the
    SRE who acted); service/trigger agents map to ``None``."""
    out: list[dict] = []
    for e in raw_entries:
        agent = e.get("agent") or {}
        out.append(
            {
                "id": e["id"],
                "incident_id": incident_id,
                "type": _norm_log_type(e.get("type")),
                "at": parse_dt(e.get("created_at")),
                "agent_user_id": agent.get("id") if agent.get("type") == _USER_REF else None,
            }
        )
    return out


def derive_times(log_rows: list[dict]) -> tuple[datetime | None, datetime | None]:
    """(first acknowledge, last resolve) from normalized log rows -> MTTA/MTTR inputs."""
    acks = [r["at"] for r in log_rows if r["type"] == "acknowledge" and r["at"]]
    resolves = [r["at"] for r in log_rows if r["type"] == "resolve" and r["at"]]
    return (min(acks) if acks else None, max(resolves) if resolves else None)


def alert_row(incident_id: str, raw_alert: dict) -> dict:
    """One alert row, with cloud/model/charm/alertname derived by the classifier and
    the full payload retained in ``raw_details`` for re-derivation."""
    c = classify(raw_alert)
    return {
        "id": raw_alert["id"],
        "incident_id": incident_id,
        "summary": raw_alert.get("summary"),
        "alertname": c.alertname,
        "cloud": c.cloud,
        "juju_model": c.juju_model,
        "juju_model_uuid": c.juju_model_uuid,
        "charm": c.charm,
        "juju_unit": c.juju_unit,
        "severity": c.severity,
        "created_at": parse_dt(raw_alert.get("created_at")),
        "raw_details": raw_alert,
    }


def service_row(raw: dict) -> dict:
    teams = raw.get("teams") or []
    return {
        "id": raw["id"],
        "name": raw.get("name"),
        "team_id": _ref_id(teams[0]) if teams else None,
        "escalation_policy_id": _ref_id(raw.get("escalation_policy")),
    }


def team_row(raw: dict) -> dict:
    return {"id": raw["id"], "name": raw.get("name")}


def escalation_policy_row(raw: dict) -> dict:
    return {"id": raw["id"], "name": raw.get("name")}


def user_row(raw: dict, *, region: str = UNKNOWN) -> dict:
    """User row. ``region`` defaults to Unknown (no PD user->region map yet); the
    locked region metric uses trigger time-of-day, not this column."""
    return {
        "id": raw["id"],
        "name": raw.get("name"),
        "email": raw.get("email"),
        "region": region,
    }
