"""Pragma / Canonical visual theme for the Streamlit dashboard (issue #40).

We keep the existing, feature-complete Streamlit UI and apply Canonical's **Pragma**
design-system styling on top as CSS: the Ubuntu Sans font, Pragma's real brand token
(authentic ``oklch`` orange from ``@canonical/design-tokens``), a Canonical accent bar,
and matching headings / links / buttons.

The CSS here is **theme-agnostic** — it sets the font, the orange accent and link colour
but never pins background/text, so Streamlit's built-in **Light/Dark** switch keeps
working (Settings ▸ choose Light or Dark). The native ``.streamlit/config.toml`` only
pins the brand accent + font for the same reason.

``inject_pragma_theme()`` is called once per page from ``render_freshness`` (which every
page calls), so the whole app is themed from a single hook.
"""

from __future__ import annotations

import streamlit as st

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Ubuntu+Sans:ital,wght@0,300..700;1,400&family=Ubuntu+Sans+Mono:wght@400..600&display=swap');

:root {
  --p-orange: oklch(64.05% 0.1941 37.76);          /* Pragma brand primary */
  --p-orange-strong: oklch(54.94% 0.1746 37.97);
}

/* Ubuntu Sans across the app (theme-agnostic) */
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stSidebar"],
button, input, select, textarea, [class^="st-"], [class*=" st-"] {
  font-family: "Ubuntu Sans", "Ubuntu", -apple-system, BlinkMacSystemFont, sans-serif !important;
}
code, pre, kbd, [data-testid="stCode"], .stCodeBlock {
  font-family: "Ubuntu Sans Mono", "Ubuntu Mono", monospace !important;
}

/* Canonical signature top accent bar (pointer-events:none so it never blocks
   Streamlit's top-right menu / toolbar) */
[data-testid="stAppViewContainer"]::before {
  content: ""; position: fixed; top: 0; left: 0; right: 0; height: 3px;
  background: var(--p-orange); z-index: 1; pointer-events: none;
}

/* Headings — weight/spacing only; colour inherits so it adapts to light/dark */
h1 { font-weight: 400; letter-spacing: -0.015em; }
h2, h3 { font-weight: 450; letter-spacing: -0.01em; }

/* Sidebar — neutral divider that works in both themes */
[data-testid="stSidebar"] { border-right: 1px solid rgba(128, 128, 128, 0.25); }

/* Buttons — Pragma square corners; primary = Canonical orange */
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {
  border-radius: 2px; font-weight: 500;
}
.stButton > button[kind="primary"] {
  background: var(--p-orange); border-color: var(--p-orange); color: #fff;
}
.stButton > button[kind="primary"]:hover {
  background: var(--p-orange-strong); border-color: var(--p-orange-strong);
}

/* Metric emphasis */
[data-testid="stMetricValue"] { font-weight: 600; }
</style>
"""


def inject_pragma_theme() -> None:
    """Apply the Pragma/Canonical look to the current Streamlit page (idempotent per run)."""
    st.markdown(_CSS, unsafe_allow_html=True)
