"""
Single Charmhub API client for IS-CMDB.

All Charmhub access (the ``refresh_charmhub`` command that caches the latest
published revision per channel) goes through this client so the base URL,
timeout and channel-map parsing live in one place.

Charmhub's storefront API is public and unauthenticated, so unlike
``NetboxClient`` / ``PagerDutyClient`` there is no token to configure. The base
URL can be overridden with ``CHARMHUB_API_URL`` for testing but defaults to the
production endpoint.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE = "https://api.charmhub.io/v2"

# The four standard Charmhub risk levels, most-to-least stable. Used to default
# a bare ``stable``-style channel onto the ``latest`` track.
RISKS = ("stable", "candidate", "beta", "edge")


@dataclass
class ChannelRelease:
    """Latest published release in one (track, risk) channel of a charm."""

    track: str
    risk: str
    revision: Optional[int]
    version: str
    released_at: Optional[str] = None


class CharmNotFound(Exception):
    """Raised when Charmhub has no charm by the requested name (HTTP 404)."""


class CharmhubClient:
    """Thin wrapper over the public Charmhub charm-info API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        timeout: int = 30,
    ) -> None:
        raw = (base_url or os.environ.get("CHARMHUB_API_URL", DEFAULT_BASE)).strip()
        self.base_url = raw.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def get_channel_map(self, charm: str) -> list[dict]:
        """
        Return the raw ``channel-map`` array for ``charm``.

        Raises :class:`CharmNotFound` on a 404 so callers can distinguish
        "this charm is not on Charmhub" (e.g. an internal charm) from a
        transient error, which is re-raised.
        """
        url = f"{self.base_url}/charms/info/{charm}"
        resp = self.session.get(
            url, params={"fields": "channel-map"}, timeout=self.timeout
        )
        if resp.status_code == 404:
            raise CharmNotFound(charm)
        resp.raise_for_status()
        return resp.json().get("channel-map", [])

    def latest_releases(self, charm: str) -> dict[tuple[str, str], ChannelRelease]:
        """
        Return the latest release per ``(track, risk)`` channel for ``charm``.

        Charmhub lists ``channel-map`` entries per base and architecture, so a
        single channel appears several times with potentially different
        revisions. We keep the highest revision seen for each channel as "the
        latest published there", which is what Juju would resolve on refresh.
        """
        latest: dict[tuple[str, str], ChannelRelease] = {}
        for entry in self.get_channel_map(charm):
            channel = entry.get("channel") or {}
            revision = entry.get("revision") or {}
            track = channel.get("track")
            risk = channel.get("risk")
            if not track or not risk:
                continue
            rev_num = revision.get("revision")
            key = (track, risk)
            current = latest.get(key)
            if current is None or (rev_num is not None and (current.revision or -1) < rev_num):
                latest[key] = ChannelRelease(
                    track=track,
                    risk=risk,
                    revision=rev_num,
                    version=str(revision.get("version", "")),
                    released_at=channel.get("released-at"),
                )
        return latest
