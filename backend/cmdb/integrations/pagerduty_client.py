"""
Read-only PagerDuty API client for IS-CMDB.

Uses ``PAGERDUTY_API_TOKEN`` (read scope). Write operations (create/cancel
maintenance windows) live in ``pagerduty.py`` and require a separate
``PAGERDUTY_WRITE_TOKEN`` — see #33.

PagerDuty uses classic offset/limit pagination with a ``more`` flag; results sit
under a resource-named key (``teams``, ``services``, ...). ``paginate`` sleeps
``page_delay`` seconds between pages so we never overload the API.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Iterator, Optional

import requests

logger = logging.getLogger(__name__)


class PagerDutyClient:
    BASE = "https://api.pagerduty.com"

    def __init__(
        self,
        token: Optional[str] = None,
        *,
        page_delay: float = 0.2,
        timeout: int = 30,
    ) -> None:
        self.token = token or os.environ.get("PAGERDUTY_API_TOKEN", "")
        if not self.token:
            raise RuntimeError("PAGERDUTY_API_TOKEN must be set to use PagerDutyClient")
        self.page_delay = page_delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Token token={self.token}",
                "Accept": "application/vnd.pagerduty+json;version=2",
                "Content-Type": "application/json",
            }
        )

    def get(self, path: str, params: Optional[dict] = None) -> dict[str, Any]:
        resp = self.session.get(
            f"{self.BASE}/{path.lstrip('/')}", params=params, timeout=self.timeout
        )
        resp.raise_for_status()
        return resp.json()

    def paginate(
        self,
        path: str,
        key: str,
        params: Optional[dict] = None,
        *,
        max_pages: Optional[int] = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield every object under ``key`` across all offset/limit pages."""
        params = dict(params or {})
        params.setdefault("limit", 100)
        offset = 0
        pages = 0
        while True:
            page_params = dict(params, offset=offset)
            data = self.get(path, page_params)
            for item in data.get(key, []):
                yield item
            if not data.get("more"):
                break
            offset += data.get("limit", 100)
            pages += 1
            if max_pages is not None and pages >= max_pages:
                break
            time.sleep(self.page_delay)
