"""Human-readable formatting filters for IS-CMDB templates."""
from __future__ import annotations

import datetime as _dt

from django import template
from django.template.defaultfilters import filesizeformat
from django.utils import dateformat
from django.utils.dateparse import parse_datetime
from django.utils.html import format_html

register = template.Library()


@register.filter
def utclocal(value: object, fmt: str = "Y-m-d H:i") -> object:
    """Render a datetime as **UTC**, marked so client JS appends the viewer's
    local time inline.

    Output::

        <time class="cmdb-dt" datetime="2026-06-21T03:20:00+00:00">2026-06-21 03:20 UTC</time>

    The base-template converter then appends " (23:20 local)" right after it, so
    every timestamp shows both. Accepts an aware/naive ``datetime`` or an ISO
    string (Redis placement timestamps come through as strings); anything that
    isn't a datetime is returned unchanged, and empty values render as "—".
    """
    if not value:
        return "—"
    if isinstance(value, str):
        parsed = parse_datetime(value)
        if parsed is None:
            return value  # not a datetime string — leave as-is
        value = parsed
    if not hasattr(value, "tzinfo"):  # e.g. a plain date
        return value
    value = (value.replace(tzinfo=_dt.timezone.utc) if value.tzinfo is None
             else value.astimezone(_dt.timezone.utc))
    shown = dateformat.format(value, fmt)  # formats in the value's tz (UTC), no active-tz shift
    return format_html(
        '<time class="cmdb-dt" datetime="{}">{} UTC</time>', value.isoformat(), shown
    )


@register.filter
def mb_to_human(value: object) -> str:
    """Render a value expressed in **megabytes** as a human-readable size.

    Django's built-in ``filesizeformat`` assumes *bytes*, so feeding it a MB
    figure (e.g. 73728) yields a nonsense "72.0 KB". This converts MB -> bytes
    first so 73728 renders as "72.0 GB".
    """
    try:
        mb = float(value)
    except (TypeError, ValueError):
        return "—"
    return filesizeformat(mb * 1024 * 1024)
