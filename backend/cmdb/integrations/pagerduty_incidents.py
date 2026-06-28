"""
Read-only PagerDuty *incident* fetch for DORA metrics.

Reuses the existing ``PagerDutyClient`` (``PAGERDUTY_API_TOKEN``, read scope) — no
new token required. The incidents endpoint is paginated offset/limit like the
rest of the API, so we lean on ``client.paginate``.

MTTR note: the list endpoint exposes ``created_at`` and ``last_status_change_at``
but not an explicit resolved timestamp. For a ``resolved`` incident the last
status change *is* the resolution, so we use it as ``resolved_at``. This is an
approximation (a late note could move it), good enough for aggregate MTTR.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterator, Optional

from .pagerduty_client import PagerDutyClient

logger = logging.getLogger(__name__)

# Pull every lifecycle state so we can count volume *and* compute MTTR; PagerDuty
# otherwise defaults to triggered+acknowledged only (no resolved → no MTTR).
ALL_STATUSES = ("triggered", "acknowledged", "resolved")


def fetch_incidents(
    since: datetime,
    until: datetime,
    *,
    client: Optional[PagerDutyClient] = None,
    statuses: tuple[str, ...] = ALL_STATUSES,
    team_ids: Optional[list[str]] = None,
    max_pages: Optional[int] = None,
) -> Iterator[dict[str, Any]]:
    """Yield raw PagerDuty incident dicts created in ``[since, until]``.

    ``since``/``until`` filter on incident creation; PagerDuty caps the range at
    6 months. Pass ``team_ids`` to scope to specific PD teams (e.g. the IS team).
    """
    client = client or PagerDutyClient()
    params: dict[str, Any] = {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "statuses[]": list(statuses),
        "sort_by": "created_at:asc",
        "time_zone": "UTC",
    }
    if team_ids:
        params["team_ids[]"] = team_ids
    yield from client.paginate("incidents", "incidents", params=params, max_pages=max_pages)


def resolved_at_of(incident: dict[str, Any]) -> Optional[str]:
    """ISO timestamp an incident was resolved, or None if not resolved."""
    if incident.get("status") == "resolved":
        return incident.get("last_status_change_at")
    return None
