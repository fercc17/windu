"""Shared chart helpers (Altair, bundled with Streamlit).

Grouped breakdowns stack from the bottom and use a custom borderless legend
(area on the left, sub-areas to the right) because Streamlit's built-in legend
overflows with many categories. All period charts can overlay **sprint marks** —
a dashed rule + caption on configured periods (e.g. the Pulse 9 sprint, or its
two weeks "Roadmap sprint" / "Engineering sprint" in week view).
"""

from __future__ import annotations

import colorsys
import html

import altair as alt
import pandas as pd
import streamlit as st

_SEP = " ▸ "
MARK_COLOR = "#e8590c"


# --- colours / legend -------------------------------------------------------

def _palette(n: int) -> list[str]:
    out = []
    for i in range(max(n, 1)):
        h = (i * 0.6180339887498949) % 1.0
        s = 0.50 + 0.12 * (i % 3)
        v = 0.92 - 0.12 * (i % 2)
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        out.append(f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}")
    return out


def _swatch(color: str) -> str:
    return (
        f"<span style='display:inline-block;width:11px;height:11px;background:{color};"
        f"border-radius:2px;margin-right:6px;vertical-align:middle'></span>"
    )


def _legend_html(colors: dict[str, str]) -> str:
    if any(_SEP in g for g in colors):
        by_area: dict[str, list[tuple[str, str]]] = {}
        for g, c in colors.items():
            area, sub = g.split(_SEP, 1) if _SEP in g else (g, "")
            by_area.setdefault(area, []).append((sub, c))
        rows = ""
        for area, subs in by_area.items():
            chips = " ".join(
                f"<span style='display:inline-block;margin:2px 14px 2px 0;white-space:nowrap'>"
                f"{_swatch(c)}{html.escape(sub or '—')}</span>"
                for sub, c in subs
            )
            rows += (
                "<tr>"
                f"<td style='font-weight:600;vertical-align:top;padding:3px 18px 3px 0;"
                f"white-space:nowrap'>{html.escape(area)}</td>"
                f"<td style='padding:3px 0'>{chips}</td></tr>"
            )
        return (
            "<table style='border-collapse:collapse;border:none;font-size:0.85rem'>"
            f"<tbody>{rows}</tbody></table>"
        )
    chips = " ".join(
        f"<span style='display:inline-block;margin:2px 16px 2px 0;white-space:nowrap'>"
        f"{_swatch(c)}{html.escape(g)}</span>"
        for g, c in colors.items()
    )
    return f"<div style='font-size:0.85rem'>{chips}</div>"


def _render_legend(colors: dict[str, str]) -> None:
    st.markdown(_legend_html(colors), unsafe_allow_html=True)


# --- sprint marks -----------------------------------------------------------

def _marks_in_view(marks: dict[str, str] | None, periods: list) -> dict[str, str]:
    if not marks:
        return {}
    pset = set(periods)
    return {p: lbl for p, lbl in marks.items() if p in pset}


def _marks_rule(marks_in_view: dict[str, str], periods: list):
    if not marks_in_view:
        return None
    mdf = pd.DataFrame([{"period": p} for p in marks_in_view])
    return (
        alt.Chart(mdf)
        .mark_rule(color=MARK_COLOR, strokeDash=[4, 3], strokeWidth=2, opacity=0.85)
        .encode(x=alt.X("period:N", sort=periods))
    )


def _render_marks_caption(marks_in_view: dict[str, str]) -> None:
    if not marks_in_view:
        return
    parts = " · ".join(
        f"<b>{html.escape(p)}</b> → {html.escape(lbl)}" for p, lbl in marks_in_view.items()
    )
    st.markdown(
        f"<div style='font-size:0.82rem;color:{MARK_COLOR}'>◆ sprint marks: {parts}</div>",
        unsafe_allow_html=True,
    )


def _emit(base, marks_in_view, periods, *, height: int) -> None:
    rule = _marks_rule(marks_in_view, periods)
    chart = (base if rule is None else (base + rule)).properties(height=height)
    st.altair_chart(chart, width="stretch")
    _render_marks_caption(marks_in_view)


# --- public charts ----------------------------------------------------------

def stacked_bar(
    df: pd.DataFrame,
    value_col: str,
    *,
    period_col: str = "period",
    group_col: str = "group",
    marks: dict[str, str] | None = None,
    group_order: list[str] | None = None,
    normalize: bool | None = None,
    value_title: str | None = None,
) -> None:
    """Stacked bar + area-grouped legend + sprint marks.

    ``df`` is the long frame ``[period, group, value_col]``.

    Stack order (issue #1/#5): by default each bar is sorted **independently**, largest
    segment at the bottom and descending upward — so every bar reads "biggest first" on
    its own, not on a single global order. The legend/colours are ordered by each group's
    total across the view (most-requested area/sub-area first). Passing an explicit
    ``group_order`` (e.g. the Cycle-Times triage→close→review decomposition) keeps that
    fixed semantic sequence instead.

    Counts vs % (issue #2): ``normalize=True`` renders each bar as 100%-normalised shares.
    When ``normalize`` is ``None`` the shared sidebar toggle (``st.session_state
    ['stack_as_pct']``) decides, so every stacked chart follows one control."""
    if df is None or df.empty:
        st.info("No data for this selection.")
        return

    if normalize is None:
        normalize = bool(st.session_state.get("stack_as_pct", False))

    present = list(df[group_col].unique())
    if group_order:
        # Explicit semantic sequence wins: fixed bottom→top order, same for every bar.
        groups = [g for g in group_order if g in present] + sorted(
            g for g in present if g not in group_order
        )
        per_bar_sort = False
    else:
        # Legend/colour order: biggest total across the view first (most→least requested).
        totals = df.groupby(group_col)[value_col].sum().sort_values(ascending=False)
        groups = list(totals.index)
        per_bar_sort = True
    colors = dict(zip(groups, _palette(len(groups))))
    periods = list(dict.fromkeys(df.sort_values(period_col)[period_col]))

    if per_bar_sort:
        # Largest value first in the stack ⇒ bottom; evaluated per bar (per period).
        order_enc = alt.Order(f"{value_col}:Q", sort="descending")
    else:
        order_idx = {g: i for i, g in enumerate(groups)}
        df = df.assign(_ord=df[group_col].map(order_idx))
        order_enc = alt.Order("_ord:Q", sort="ascending")

    if normalize:
        y = alt.Y(f"{value_col}:Q", stack="normalize", title=None, axis=alt.Axis(format="%"))
    else:
        y = alt.Y(f"{value_col}:Q", stack="zero", title=value_title)

    bars = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X(f"{period_col}:N", sort=periods, title=None),
            y=y,
            color=alt.Color(
                f"{group_col}:N",
                scale=alt.Scale(domain=groups, range=[colors[g] for g in groups]),
                legend=None,
            ),
            order=order_enc,
            tooltip=[
                alt.Tooltip(f"{period_col}:N", title="Period"),
                alt.Tooltip(f"{group_col}:N", title="Group"),
                # ".2~f": fixed 2 decimals, trailing zeros trimmed (counts stay integers)
                alt.Tooltip(f"{value_col}:Q", title=value_title or value_col.title(), format=".2~f"),
            ],
        )
    )
    _emit(bars, _marks_in_view(marks, periods), periods, height=380)
    if normalize:
        st.caption("Showing **% share** of each bar (100%-normalised). Toggle in the sidebar.")
    _render_legend(colors)


def series_chart(
    wide: pd.DataFrame,
    *,
    kind: str = "bar",
    stack: bool = False,
    marks: dict[str, str] | None = None,
    height: int = 360,
) -> None:
    """Bar/line chart for a wide frame (period index, one column per series), with
    optional sprint marks. Grouped (side-by-side) bars unless ``stack``."""
    if wide is None or wide.empty:
        st.info("No data for this selection.")
        return

    w = wide.reset_index()
    period_col = w.columns[0]
    periods = list(w[period_col])
    long = w.melt(id_vars=period_col, var_name="series", value_name="value")

    enc = dict(
        x=alt.X(f"{period_col}:N", sort=periods, title=None),
        y=alt.Y("value:Q", title=None, stack=("zero" if stack else None)),
        color=alt.Color("series:N", title=None),
        tooltip=[
            alt.Tooltip(f"{period_col}:N", title="Period"),
            alt.Tooltip("series:N", title="Series"),
            alt.Tooltip("value:Q", title="Value", format=".2~f"),
        ],
    )
    if kind == "line":
        base = alt.Chart(long).mark_line(point=True).encode(**enc)
    else:
        if not stack:
            enc["xOffset"] = alt.XOffset("series:N")
        base = alt.Chart(long).mark_bar().encode(**enc)

    _emit(base, _marks_in_view(marks, periods), periods, height=height)


def area_drilldown(
    df_sub: pd.DataFrame,
    value_col: str,
    *,
    marks: dict[str, str] | None = None,
    key: str = "area",
) -> None:
    """Area breakdown with click-to-drill into sub-areas.

    ``df_sub`` is the SUB-area long frame ``[period, "Area ▸ Sub", value]``. The
    area-level view is derived by summing sub-areas per area; clicking an area row
    switches the chart to just that area's sub-areas.
    """
    if df_sub is None or df_sub.empty:
        st.info("No data for this selection.")
        return

    areas = df_sub["group"].str.split(_SEP, n=1).str[0]
    area_long = (
        df_sub.assign(_area=areas)
        .groupby(["period", "_area"], as_index=False)[value_col]
        .sum()
        .rename(columns={"_area": "group"})
    )
    totals = (
        area_long.groupby("group", as_index=False)[value_col]
        .sum()
        .sort_values(value_col, ascending=False)
        .reset_index(drop=True)
    )
    totals.columns = ["Area", value_col.title()]

    st.caption("Click an area to drill into its sub-areas (click the row again to clear).")
    event = st.dataframe(
        totals,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key=key,
    )
    rows = list(getattr(event.selection, "rows", []) or []) if event is not None else []

    if rows and rows[0] < len(totals):
        area = totals.iloc[rows[0]]["Area"]
        sub = df_sub[df_sub["group"].str.startswith(f"{area}{_SEP}")].copy()
        sub["group"] = sub["group"].str.split(_SEP, n=1).str[1]
        st.markdown(f"**▸ {html.escape(str(area))} — by sub-area**")
        stacked_bar(sub, value_col, marks=marks)
    else:
        stacked_bar(area_long, value_col, marks=marks)
