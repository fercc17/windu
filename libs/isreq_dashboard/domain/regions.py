"""Region derived two distinct, non-substitutable ways (Art. V, FR-026/027).

(a) ``region_from_timestamp`` — creation-time-of-day analysis: maps the UTC hour of
    a timestamp to AMER/EMEA/APAC via configurable, EMEA-anchored windows.
(b) ``region_from_user_map`` — per-user counts: reads a static account->region map.

These are deliberately separate functions with different signatures so one can never
be silently substituted for the other. Unmapped users / uncovered times -> "Unknown".

Pure logic: no database, no Jira.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping

UNKNOWN = "Unknown"
ALLOWED_REGIONS = ("AMER", "EMEA", "APAC")


def _to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _minute_of_day_utc(t: datetime) -> int:
    u = t.astimezone(timezone.utc) if t.tzinfo else t.replace(tzinfo=timezone.utc)
    return u.hour * 60 + u.minute


def _in_window(minute: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    # window wraps midnight (e.g. APAC 22:00-06:00)
    return minute >= start or minute < end


def region_from_timestamp(t: datetime, windows: Mapping[str, Mapping[str, str]]) -> str:
    """Region for creation-time-of-day analysis (FR-026a). Uncovered -> Unknown."""
    minute = _minute_of_day_utc(t)
    for region, w in windows.items():
        if _in_window(minute, _to_minutes(w["start"]), _to_minutes(w["end"])):
            return region
    return UNKNOWN


def region_from_user_map(account_id: str | None, user_region: Mapping[str, str]) -> str:
    """Region for per-user counts (FR-026b). Unmapped/None -> Unknown (never guessed)."""
    if account_id is None:
        return UNKNOWN
    return user_region.get(account_id, UNKNOWN)


def validate_windows_cover_24h(windows: Mapping[str, Mapping[str, str]]) -> None:
    """Raise ``ValueError`` unless the windows tile a full day with no gap/overlap."""
    spans: list[tuple[int, int]] = []
    for w in windows.values():
        s, e = _to_minutes(w["start"]), _to_minutes(w["end"])
        if s <= e:
            spans.append((s, e))
        else:  # split a wrapping window into two
            spans.append((s, 1440))
            spans.append((0, e))
    spans.sort()
    covered = 0
    cursor = 0
    for s, e in spans:
        if s != cursor:
            raise ValueError(f"region windows have a gap/overlap at minute {cursor} (next {s})")
        covered += e - s
        cursor = e
    if covered != 1440 or cursor != 1440:
        raise ValueError("region windows must cover exactly 24h")
