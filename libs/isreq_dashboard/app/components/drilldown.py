"""Drill-down ticket table (FR-019/020): every aggregate opens to its underlying
tickets, each showing Jira key, title, and assignee — with the key AND title
hyperlinked to the ticket in Jira."""

from __future__ import annotations

import html
import urllib.parse
from collections.abc import Callable, Sequence

import streamlit as st

from isreq_dashboard.app.data import get_settings


def render_jql(keys: Sequence[str], *, label: str = "JQL for this table") -> None:
    """Show a copy/paste-able JQL that reproduces this ticket set in Jira + an open link
    (issue #25). Uses an exact ``issuekey in (…)`` list so any table can be cross-checked."""
    keys = [k for k in dict.fromkeys(keys) if k]
    if not keys:
        return
    settings = get_settings()
    base = settings.jira_base_url.rstrip("/")
    jql = (f"project = {settings.toml.project_key} AND issuekey in "
           f"({', '.join(keys)}) ORDER BY key ASC")
    url = f"{base}/issues/?jql={urllib.parse.quote(jql)}"
    st.caption(f":orange[**⚠️ experimental**] · {label} — {len(keys)} tickets · "
               f"[open in Jira]({url}) (copy the JQL to cross-check; if the link is too long, "
               "paste the JQL in Jira):")
    st.code(jql, language="text")

# (header, accessor) where accessor is a row key or a callable(row) -> value
ExtraColumn = tuple[str, "str | Callable[[dict], object]"]


def _browse_url(base: str, key: str) -> str:
    return f"{base.rstrip('/')}/browse/{key}"


def _link(url: str, text: str) -> str:
    return f'<a href="{html.escape(url)}" target="_blank" rel="noopener">{html.escape(text)}</a>'


def render_tickets(
    rows: list[dict],
    *,
    caption: str | None = None,
    extra_columns: Sequence[ExtraColumn] = (),
) -> None:
    """Render a ticket list with the key and title linked to Jira.

    ``extra_columns`` appends further plain-text columns (used by the Data Quality
    page for priority/status/date)."""
    if caption:
        st.markdown(f"**{caption}** — {len(rows)} ticket(s)")
    if not rows:
        st.info("No underlying tickets for this selection.")
        return

    base = get_settings().jira_base_url
    headers = "".join(
        f"<th style='text-align:left;padding:4px 10px 4px 0'>{html.escape(h)}</th>"
        for h in ("Key", "Title", "Assignee", *[h for h, _ in extra_columns])
    )

    body = []
    for r in rows:
        url = _browse_url(base, r["key"])
        cells = [
            _link(url, r["key"]),
            _link(url, r.get("title") or ""),
            html.escape(r.get("assignee_name") or "Unassigned"),
        ]
        for _, acc in extra_columns:
            val = acc(r) if callable(acc) else r.get(acc)
            cells.append(html.escape("" if val is None else str(val)))
        tds = "".join(f"<td style='padding:4px 10px 4px 0'>{c}</td>" for c in cells)
        body.append(
            f"<tr style='border-bottom:1px solid rgba(128,128,128,0.25)'>{tds}</tr>"
        )

    table = (
        "<table style='width:100%;border-collapse:collapse;font-size:0.9rem'>"
        f"<thead><tr style='border-bottom:1px solid rgba(128,128,128,0.5)'>{headers}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
    )
    st.markdown(table, unsafe_allow_html=True)
