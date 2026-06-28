"""
Single Netbox API client for IS-CMDB.

All Netbox access (audit scripts, nightly reconciliation, switch-graph build)
goes through this client so the base URL, token, auth header and pagination /
rate-limiting behaviour live in one place.

Credentials are read from the environment (``NETBOX_URL``, ``NETBOX_TOKEN``).
Inside Django these are populated from ``.env`` by ``django-environ`` at
settings import; standalone scripts should set up Django (or otherwise export
the vars) before instantiating the client.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Iterator, Optional

import requests

logger = logging.getLogger(__name__)


class NetboxClient:
    """Thin wrapper over the Netbox REST API with cursor pagination."""

    def __init__(
        self,
        url: Optional[str] = None,
        token: Optional[str] = None,
        *,
        page_delay: float = 0.1,
        timeout: int = 30,
    ) -> None:
        raw_url = (url or os.environ.get("NETBOX_URL", "")).strip()
        self.url = raw_url.rstrip("/") + "/" if raw_url else ""
        self.token = token or os.environ.get("NETBOX_TOKEN", "")
        if not self.url or not self.token:
            raise RuntimeError(
                "NETBOX_URL and NETBOX_TOKEN must be set to use NetboxClient"
            )
        self.page_delay = page_delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Token {self.token}", "Accept": "application/json"}
        )

    def get(self, path: str, params: Optional[dict] = None) -> dict[str, Any]:
        """Single GET returning the parsed JSON body."""
        resp = self.session.get(
            self.url + path.lstrip("/"), params=params, timeout=self.timeout
        )
        resp.raise_for_status()
        return resp.json()

    def count(self, path: str, params: Optional[dict] = None) -> int:
        """Return the total object count for a list endpoint (limit=1)."""
        params = dict(params or {})
        params["limit"] = 1
        return int(self.get(path, params).get("count", 0))

    def paginate(
        self,
        path: str,
        params: Optional[dict] = None,
        *,
        max_pages: Optional[int] = None,
    ) -> Iterator[dict[str, Any]]:
        """
        Yield every object across all pages, following Netbox's ``next`` link.

        Sleeps ``page_delay`` seconds between pages to avoid overloading Netbox.
        """
        params = dict(params or {})
        params.setdefault("limit", 100)
        url: Optional[str] = self.url + path.lstrip("/")
        pages = 0
        while url:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("results", []):
                yield item
            url = data.get("next")
            params = None  # the ``next`` URL already carries the querystring
            pages += 1
            if max_pages is not None and pages >= max_pages:
                break
            if url:
                time.sleep(self.page_delay)
