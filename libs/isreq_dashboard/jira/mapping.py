"""Raw Jira JSON -> row dicts (research R-002/R-012, FR-012/028).

Pure functions (no network), so they are unit-testable directly. Field ids for
area / sub_area / pulse are configurable. The "Request area" field is a *cascading
select*: the parent ``.value`` is the area and the child ``.value`` is the sub-area,
both read from the same configured field id.
"""

from __future__ import annotations

from datetime import datetime, timezone

# stable per-field code so one changelog history with several consumed items still
# yields distinct, deterministic (idempotent) primary keys.
_FIELD_CODE = {"priority": 1, "status": 2}


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(s, fmt).astimezone(timezone.utc)
        except ValueError:
            continue
    # last resort: ISO 8601 with a colon-less offset
    iso = s
    if len(iso) >= 5 and (iso[-5] in "+-") and iso[-3] != ":":
        iso = iso[:-2] + ":" + iso[-2:]
    return datetime.fromisoformat(iso).astimezone(timezone.utc)


def _cascading(fields: dict, field_id: str) -> tuple[str | None, str | None]:
    """(parent.value, child.value) of a cascading-select custom field."""
    raw = fields.get(field_id)
    if not isinstance(raw, dict):
        return None, None
    parent = raw.get("value")
    child = raw.get("child", {})
    child_val = child.get("value") if isinstance(child, dict) else None
    return parent, child_val


def _latest_pulse(fields: dict, field_id: str) -> str | None:
    """Latest (most recent) sprint name from the sprint field (FR-012)."""
    raw = fields.get(field_id)
    if not raw:
        return None
    if isinstance(raw, list):
        sprints = [s for s in raw if isinstance(s, dict)]
        if sprints:
            # prefer most recent startDate; fall back to list order (chronological).
            def _key(s: dict):
                return (parse_dt(s.get("startDate")) or datetime.min.replace(tzinfo=timezone.utc))

            return max(sprints, key=_key).get("name")
        # older API: list of preformatted strings -> take the last
        return str(raw[-1])
    if isinstance(raw, dict):
        return raw.get("name")
    return None


def issue_row(raw: dict, cfg) -> dict:
    """Map a raw search issue to the ``issues`` row dict."""
    fields = raw.get("fields", {})
    area, _child_from_area = _cascading(fields, cfg.toml.field_area)
    _parent_from_sub, sub_area = _cascading(fields, cfg.toml.field_sub_area)
    assignee = fields.get("assignee") or {}
    # Reporter = who raised it (fall back to creator when reporter is unset).
    reporter = fields.get("reporter") or fields.get("creator") or {}
    title = fields.get("summary")
    return {
        "key": raw["key"],
        "jira_id": int(raw["id"]) if raw.get("id") is not None else None,
        "title": title,
        "created_at": parse_dt(fields.get("created")),
        "resolved_at": parse_dt(fields.get("resolutiondate")),
        "current_status": (fields.get("status") or {}).get("name"),
        "current_priority": (fields.get("priority") or {}).get("name"),
        "assignee_account_id": assignee.get("accountId"),
        "assignee_name": assignee.get("displayName"),
        "reporter_account_id": reporter.get("accountId"),
        "reporter_name": reporter.get("displayName"),
        "area": area,
        "sub_area": sub_area,
        "pulse": _latest_pulse(fields, cfg.toml.field_pulse),
        "is_pr_mp": bool(title and cfg.toml.pr_mp_title_substring in title),
        "labels": list(fields.get("labels") or []),
        "jira_updated_at": parse_dt(fields.get("updated")),
    }


def changelog_rows(raw: dict) -> list[dict]:
    """Priority/status transitions from an issue's expanded changelog.

    Only the consumed fields (priority, status) are stored. The primary key is
    derived deterministically from the history id and field code, so re-running
    the sync upserts the same rows (idempotent, SC-009).
    """
    key = raw["key"]
    out: list[dict] = []
    histories = (raw.get("changelog") or {}).get("histories", [])
    for h in histories:
        hid = int(h["id"])
        changed_at = parse_dt(h.get("created"))
        author = h.get("author") or {}
        for item in h.get("items", []):
            field = item.get("field")
            if field not in _FIELD_CODE:
                continue
            out.append(
                {
                    "id": hid * 8 + _FIELD_CODE[field],
                    "issue_key": key,
                    "field": field,
                    "from_value": item.get("fromString"),
                    "to_value": item.get("toString"),
                    "changed_at": changed_at,
                    "author_account_id": author.get("accountId"),
                    "author_name": author.get("displayName"),
                }
            )
    return out


def worklog_rows(issue_key: str, raw_worklogs: list[dict]) -> list[dict]:
    """Map worklog entries; bucket later by ``started``. No author stored (FR-018)."""
    out = []
    for w in raw_worklogs:
        out.append(
            {
                "id": int(w["id"]),
                "issue_key": issue_key,
                "time_spent_seconds": int(w.get("timeSpentSeconds", 0)),
                "started_at": parse_dt(w.get("started")),
            }
        )
    return out


def inline_worklogs(raw: dict) -> tuple[list[dict], int]:
    """Inline worklog entries returned by issue-search and the server-side total.

    The inline subset is COMPLETE only when ``total <= len(entries)`` (i.e. all
    entries fit inline); otherwise the caller must fetch the full set per issue
    (FR-004). Issues with no worklogs need no extra call.
    """
    wl = (raw.get("fields") or {}).get("worklog") or {}
    entries = wl.get("worklogs", []) or []
    total = wl.get("total", len(entries))
    return entries, total


def changelog_truncated(raw: dict) -> bool:
    """True if the inline changelog is incomplete and needs the per-issue endpoint."""
    cl = raw.get("changelog") or {}
    total = cl.get("total")
    histories = cl.get("histories", [])
    max_results = cl.get("maxResults", len(histories))
    return total is not None and total > max(len(histories), max_results)
