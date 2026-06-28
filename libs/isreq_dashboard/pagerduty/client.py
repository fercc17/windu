"""Read-only PagerDuty REST v2 client (mirrors ``jira/client.py``).

GET only — deliberately no method that acknowledges, resolves, edits or deletes
anything in PagerDuty. Pagination (``limit``/``offset`` until ``more=false``) is
handled internally; ``tenacity`` retries transient failures. ``httpx`` is imported
lazily so the module imports (and the sync is testable with the fixture client)
without httpx present.

Two implementations satisfy ``ReadOnlyPagerDutyClient``:
  - ``PagerDutyClient``       — the live httpx-backed client.
  - ``FixturePagerDutyClient`` — replays a recorded JSON so the sync runs with no
                                  token (fixture-first development).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol, Sequence

from tenacity import retry, stop_after_attempt, wait_exponential

PD_API_BASE = "https://api.pagerduty.com"
PD_ACCEPT = "application/vnd.pagerduty+json;version=2"
# Every status so the backfill captures triggered/acknowledged/resolved incidents.
ALL_STATUSES = ("triggered", "acknowledged", "resolved")


class ReadOnlyPagerDutyClient(Protocol):
    """The read surface the sync depends on (live client and fixture both implement it)."""

    def list_incidents(
        self,
        *,
        since: str,
        until: str,
        team_ids: Sequence[str],
        statuses: Sequence[str] = ALL_STATUSES,
    ) -> Iterator[dict]: ...
    def incident_alerts(self, incident_id: str) -> list[dict]: ...
    def incident_log_entries(self, incident_id: str) -> list[dict]: ...
    def list_teams(self) -> list[dict]: ...
    def list_services(self, *, team_ids: Sequence[str] | None = None) -> list[dict]: ...
    def list_users(self, *, team_ids: Sequence[str] | None = None) -> list[dict]: ...
    def list_escalation_policies(self, *, team_ids: Sequence[str] | None = None) -> list[dict]: ...
    def get_user(self, user_id: str) -> dict | None: ...


class PagerDutyClient:
    """Concrete httpx-backed read-only client."""

    def __init__(self, api_token: str, *, base_url: str = PD_API_BASE, page_size: int = 100) -> None:
        import httpx  # lazy: only needed when actually talking to PagerDuty

        self._page_size = page_size
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Token token={api_token}",
                "Accept": PD_ACCEPT,
            },
            timeout=httpx.Timeout(30.0),
        )

    # --- low level (GET only) ----------------------------------------------
    # More attempts than the Jira client: a full backfill is ~13k calls and PagerDuty
    # rate-limits (HTTP 429) per-minute, so we need enough exponential backoff
    # (cumulative ~2min) to ride out a limit window without aborting the run.
    @retry(stop=stop_after_attempt(8), wait=wait_exponential(multiplier=1, max=30), reraise=True)
    def _get(self, path: str, params: Any | None = None) -> dict:
        resp = self._http.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    def _paginate(self, path: str, key: str, base_params: list[tuple[str, Any]]) -> Iterator[dict]:
        """Offset-paginate a list endpoint, yielding each item until ``more=false``."""
        offset = 0
        while True:
            params = [*base_params, ("limit", self._page_size), ("offset", offset)]
            page = self._get(path, params)
            items = page.get(key, []) or []
            yield from items
            offset += len(items)
            if not page.get("more") or not items:
                return

    @staticmethod
    def _team_params(team_ids: Sequence[str] | None) -> list[tuple[str, Any]]:
        return [("team_ids[]", t) for t in (team_ids or [])]

    # --- high level --------------------------------------------------------
    def list_incidents(
        self,
        *,
        since: str,
        until: str,
        team_ids: Sequence[str],
        statuses: Sequence[str] = ALL_STATUSES,
    ) -> Iterator[dict]:
        params: list[tuple[str, Any]] = [
            ("since", since),
            ("until", until),
            ("time_zone", "UTC"),
            ("sort_by", "created_at:asc"),
        ]
        params += self._team_params(team_ids)
        params += [("statuses[]", s) for s in statuses]
        yield from self._paginate("/incidents", "incidents", params)

    def incident_alerts(self, incident_id: str) -> list[dict]:
        return list(self._paginate(f"/incidents/{incident_id}/alerts", "alerts", []))

    def incident_log_entries(self, incident_id: str) -> list[dict]:
        # is_overview=false returns the full timeline (trigger/ack/escalate/resolve/assign).
        return list(
            self._paginate(
                f"/incidents/{incident_id}/log_entries", "log_entries", [("is_overview", "false")]
            )
        )

    def list_teams(self) -> list[dict]:
        return list(self._paginate("/teams", "teams", []))

    def list_services(self, *, team_ids: Sequence[str] | None = None) -> list[dict]:
        return list(self._paginate("/services", "services", self._team_params(team_ids)))

    def list_users(self, *, team_ids: Sequence[str] | None = None) -> list[dict]:
        return list(self._paginate("/users", "users", self._team_params(team_ids)))

    def list_escalation_policies(self, *, team_ids: Sequence[str] | None = None) -> list[dict]:
        return list(
            self._paginate("/escalation_policies", "escalation_policies", self._team_params(team_ids))
        )

    def get_user(self, user_id: str) -> dict | None:
        """One user by id (to resolve cross-team responders not in the team roster).
        A deleted/unknown id (404) returns ``None`` rather than raising."""
        resp = self._http.get(f"/users/{user_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json().get("user")

    def close(self) -> None:
        self._http.close()


class FixturePagerDutyClient:
    """Replays a recorded JSON fixture so the sync runs with no token.

    The fixture embeds each incident's ``alerts`` and ``log_entries`` for convenience;
    this client serves them through the same separate-call interface the live API uses,
    so the sync code path is identical whether the source is live or recorded.
    """

    def __init__(self, fixture_path: str | Path) -> None:
        self._data = json.loads(Path(fixture_path).read_text())
        self._by_id = {inc["id"]: inc for inc in self._data.get("incidents", [])}

    def list_incidents(
        self,
        *,
        since: str,
        until: str,
        team_ids: Sequence[str],
        statuses: Sequence[str] = ALL_STATUSES,
    ) -> Iterator[dict]:
        wanted_teams = set(team_ids or [])
        for inc in self._data.get("incidents", []):
            created = inc.get("created_at", "")
            if since and created < since:
                continue
            if until and created > until:
                continue
            if wanted_teams:
                inc_teams = {t.get("id") for t in inc.get("teams", [])}
                if not (inc_teams & wanted_teams):
                    continue
            if statuses and inc.get("status") not in set(statuses):
                continue
            yield inc

    def incident_alerts(self, incident_id: str) -> list[dict]:
        return list(self._by_id.get(incident_id, {}).get("alerts", []))

    def incident_log_entries(self, incident_id: str) -> list[dict]:
        return list(self._by_id.get(incident_id, {}).get("log_entries", []))

    def list_teams(self) -> list[dict]:
        team = self._data.get("team")
        return [team] if team else list(self._data.get("teams", []))

    def list_services(self, *, team_ids: Sequence[str] | None = None) -> list[dict]:
        return list(self._data.get("services", []))

    def list_users(self, *, team_ids: Sequence[str] | None = None) -> list[dict]:
        return list(self._data.get("users", []))

    def list_escalation_policies(self, *, team_ids: Sequence[str] | None = None) -> list[dict]:
        return list(self._data.get("escalation_policies", []))

    def get_user(self, user_id: str) -> dict | None:
        return next((u for u in self._data.get("users", []) if u.get("id") == user_id), None)

    def close(self) -> None:  # parity with the live client
        return None
