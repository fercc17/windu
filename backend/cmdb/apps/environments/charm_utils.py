"""
Helpers for interpreting ``Environment.charm_versions`` strings.

``charm_versions`` is a flat ``{charm_name: version_string}`` map written by the
parser / refresh commands. The version string is free-form and comes in a few
shapes, e.g. ``"14/stable"``, ``"4/edge (rev 344)"``, ``"rev 12"``,
``"service-deployed"`` (a placeholder when a real version could not be
resolved) or ``"unknown"``.

These helpers turn that string into a structured channel so views can compare
it against the Charmhub catalogue (:class:`~cmdb.apps.environments.models.CharmRelease`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Values that carry no usable version/channel information.
PLACEHOLDERS = {"", "service-deployed", "unknown"}

# Standard Charmhub risk levels. A bare ``stable``-style channel (no track)
# defaults onto the implicit ``latest`` track, matching Charmhub semantics.
RISKS = {"stable", "candidate", "beta", "edge"}

_REV_RE = re.compile(r"\brev(?:ision)?\s+(\d+)", re.IGNORECASE)
_REV_ONLY_RE = re.compile(r"^rev(?:ision)?\s+\d+$", re.IGNORECASE)
_NUMERIC_TRACK_RE = re.compile(r"\d+(?:\.\d+)*")


def track_tuple(track: str) -> Optional[tuple[int, ...]]:
    """
    Return a comparable tuple for a numeric track, or ``None`` if not numeric.

    Charmhub track ordering is *not* lexical: ``"16"`` is newer than ``"14"``
    and ``"latest"`` is a legacy track, not the newest. We therefore only order
    purely numeric tracks (``"14"`` -> ``(14,)``, ``"1.32"`` -> ``(1, 32)``) and
    treat anything else (e.g. ``"latest"``) as uncomparable.
    """
    if not track or not _NUMERIC_TRACK_RE.fullmatch(track):
        return None
    return tuple(int(part) for part in track.split("."))


@dataclass
class ParsedVersion:
    """A ``charm_versions`` value parsed into its channel components."""

    track: str          # e.g. "14", "latest"; "" when only a revision was given
    risk: str           # e.g. "stable", "edge"; "" when unknown
    revision: Optional[int]
    raw: str

    @property
    def has_channel(self) -> bool:
        return bool(self.track and self.risk)


def parse_charm_version(value: str) -> Optional[ParsedVersion]:
    """
    Parse a ``charm_versions`` value into a :class:`ParsedVersion`.

    Returns ``None`` for placeholder values that carry no version at all
    (``service-deployed``, ``unknown``, empty).
    """
    v = (value or "").strip()
    if v.lower() in PLACEHOLDERS:
        return None

    rev_match = _REV_RE.search(v)
    revision = int(rev_match.group(1)) if rev_match else None

    # Channel is everything before the optional " (rev N)" suffix.
    channel = re.split(r"\s*\(", v, maxsplit=1)[0].strip()

    # A bare "rev N" has no channel information.
    if _REV_ONLY_RE.match(channel):
        return ParsedVersion(track="", risk="", revision=revision, raw=v)

    if "/" in channel:
        track, _, risk = channel.partition("/")
        track, risk = track.strip(), risk.strip()
    else:
        token = channel.strip()
        if token.lower() in RISKS:
            track, risk = "latest", token.lower()
        else:
            # A bare track (e.g. "14") implies the stable risk.
            track, risk = token, "stable"

    return ParsedVersion(track=track, risk=risk, revision=revision, raw=v)
