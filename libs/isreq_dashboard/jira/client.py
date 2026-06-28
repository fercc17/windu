"""Read-only Jira Cloud REST v3 client (Art. IX, research R-002/R-003/R-012).

GET only — there is deliberately no method that creates, edits, transitions,
comments on, or deletes anything in Jira. Pagination is handled internally;
``tenacity`` retries transient failures. ``httpx`` is imported lazily so this module
can be imported (and the sync logic tested with a fake client) without httpx present.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

from tenacity import retry, stop_after_attempt, wait_exponential

# Atlassian retired GET /rest/api/3/search (410 Gone). The enhanced-search endpoint
# /rest/api/3/search/jql paginates with nextPageToken/isLast (no startAt/total).
SEARCH_ENDPOINT = "/rest/api/3/search/jql"
# *navigable covers summary/status/priority/assignee/labels + navigable custom fields
# (area customfield_13027, sprint customfield_10020); worklog is added explicitly.
SEARCH_FIELDS = ["*navigable", "worklog"]


class ReadOnlyJiraClient(Protocol):
    """The read surface the sync depends on (real client and test fakes implement it)."""

    def search_issues(self, jql: str) -> Iterator[dict]: ...
    def issue_changelog(self, key: str) -> list[dict]: ...
    def issue_worklogs(self, key: str) -> list[dict]: ...


class JiraClient:
    """Concrete httpx-backed read-only client."""

    def __init__(self, base_url: str, email: str, api_token: str, *, page_size: int = 100) -> None:
        import httpx  # lazy: only needed when actually talking to Jira

        self._page_size = page_size
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            auth=(email, str(api_token)),
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(30.0),
        )

    # --- low level (GET only) ----------------------------------------------
    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, max=30), reraise=True)
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        resp = self._http.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    # --- high level --------------------------------------------------------
    def search_issues(self, jql: str) -> Iterator[dict]:
        """Yield every issue matching ``jql`` with its (inline) changelog expanded.

        Uses the enhanced-search endpoint with ``nextPageToken``/``isLast`` paging.
        """
        token: str | None = None
        while True:
            params = {
                "jql": jql,
                "maxResults": self._page_size,
                "expand": "changelog",
                "fields": ",".join(SEARCH_FIELDS),
            }
            if token:
                params["nextPageToken"] = token
            page = self._get(SEARCH_ENDPOINT, params=params)
            yield from page.get("issues", [])
            token = page.get("nextPageToken")
            if page.get("isLast") or not token:
                return

    def issue_changelog(self, key: str) -> list[dict]:
        """Full changelog histories for one issue (used when inline is truncated)."""
        out: list[dict] = []
        start = 0
        while True:
            page = self._get(
                f"/rest/api/3/issue/{key}/changelog",
                params={"startAt": start, "maxResults": self._page_size},
            )
            values = page.get("values", [])
            out.extend(values)
            start += len(values)
            if start >= page.get("total", 0) or not values:
                return out

    def issue_worklogs(self, key: str) -> list[dict]:
        """Complete worklog set for one issue (never the truncated inline <=20, FR-004)."""
        out: list[dict] = []
        start = 0
        while True:
            page = self._get(
                f"/rest/api/3/issue/{key}/worklog",
                params={"startAt": start, "maxResults": self._page_size},
            )
            values = page.get("worklogs", [])
            out.extend(values)
            start += len(values)
            if start >= page.get("total", 0) or not values:
                return out

    def list_fields(self) -> list[dict]:
        """One-shot field discovery (R-012): resolve area/sub-area/pulse customfield ids."""
        return self._get("/rest/api/3/field")  # returns a list

    def close(self) -> None:
        self._http.close()
