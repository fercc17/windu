"""Worklog completeness (Art. VI, FR-004, research R-003).

The issue-search response inlines at most ~20 worklog entries; that is treated as
INCOMPLETE. Every issue's worklogs are fetched in full via the per-issue endpoint
(``client.issue_worklogs``). For large incremental runs the bulk
``worklog/updated`` -> ``worklog/list`` pair can pull only changed worklogs; the
per-issue path used here is always correct (it just costs one call per touched issue).
"""

from __future__ import annotations

from isreq_dashboard.jira.client import ReadOnlyJiraClient


def fetch_complete_worklogs(client: ReadOnlyJiraClient, issue_key: str) -> list[dict]:
    """All worklog entries for an issue — never the truncated inline subset."""
    return client.issue_worklogs(issue_key)
