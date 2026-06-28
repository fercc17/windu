"""Streamlit data access — cached engine/session/config (read-only, Postgres only).

The app reads ONLY from Postgres and NEVER calls Jira at render time (Art. X). It
deliberately does not import ``isreq_dashboard.jira`` anywhere.
"""

from __future__ import annotations

import streamlit as st

from isreq_dashboard.config import Settings
from isreq_dashboard.db.engine import make_engine
from isreq_dashboard.db.session import make_session_factory
from isreq_dashboard.metrics.base import PER_PULSE, MetricConfig, Selector, last_sync_at


@st.cache_resource
def get_settings() -> Settings:
    return Settings.load()


@st.cache_resource
def get_session_factory():
    return make_session_factory(make_engine(get_settings()))


# --- Precomputed/memoized metric frames (issue #5) --------------------------
# The app already never computes from Jira at render (sync-then-read, Art. X): every
# number comes from synced Postgres. On top of that, identical selections are memoized
# here so they are computed once per *data version* and reused across reruns, instead of
# recomputed on every widget interaction. The cache key includes the last-sync watermark,
# so a new sync transparently invalidates it.


def data_version() -> str:
    """Cache-busting token: the issues sync watermark (changes only when new data lands)."""
    with get_session_factory()() as s:
        ts = last_sync_at(s)
    return ts.isoformat() if ts else "none"


@st.cache_data(show_spinner=False)
def _cached_backlog(cadence: str, scope: str, pr_mp: str, group: str | None, _version: str):
    from isreq_dashboard.metrics.backlog import backlog_series

    sel = Selector(cadence=cadence, scope=scope, pr_mp=pr_mp)
    with get_session_factory()() as s:
        return backlog_series(s, get_metric_config(), sel, group=group)


def backlog_frame(sel: Selector, group: str | None = None):
    """Memoized Backlog series (the heaviest metric: open-at-T across every period)."""
    return _cached_backlog(sel.cadence, sel.scope, sel.pr_mp, group, data_version())


@st.cache_data(show_spinner=False)
def _cached_burndown(cadence: str, scope: str, pr_mp: str, _version: str):
    from isreq_dashboard.metrics.predictions import backlog_burndown

    sel = Selector(cadence=cadence, scope=scope, pr_mp=pr_mp)
    with get_session_factory()() as s:
        return backlog_burndown(s, get_metric_config(), sel)


def burndown_frame(sel: Selector):
    """Memoized year-long backlog burndown projection (precomputed per data version)."""
    return _cached_burndown(sel.cadence, sel.scope, sel.pr_mp, data_version())


@st.cache_data(show_spinner=False)
def _cached_baseline(scope: str, pr_mp: str, _version: str):
    from isreq_dashboard.metrics.predictions import backlog_baseline

    sel = Selector(scope=scope, pr_mp=pr_mp)
    with get_session_factory()() as s:
        return backlog_baseline(s, get_metric_config(), sel)


def backlog_baseline_frame(sel: Selector):
    """Memoized staffing-model baseline (backlog + intake/close rates) per data version."""
    return _cached_baseline(sel.scope, sel.pr_mp, data_version())


def get_metric_config() -> MetricConfig:
    t = get_settings().toml
    return MetricConfig(
        anchor=t.anchor_date,
        closed_statuses=t.closed_statuses,
        highest_priority_name=t.highest_priority_name,
        ps5_blocker_label=t.ps5_blocker_label,
        region_windows=t.region_windows_utc,
        low_n_threshold=t.low_n_threshold,
        untriaged_status=t.untriaged_status,
        in_review_status=t.in_review_status,
    )


def get_period_marks(cadence: str) -> dict[str, str]:
    """Sprint marks for the active cadence (period -> label)."""
    marks = get_settings().toml.period_marks or {}
    return marks.get("per_pulse" if cadence == PER_PULSE else "weekly", {})


# --- PagerDuty analysis (co-tenant, own `pd` schema, never touches isreq) -----

@st.cache_resource
def get_pd_session_factory():
    """Session factory bound to the ``pd`` schema (separate search_path, same DB)."""
    s = get_settings()
    return make_session_factory(make_engine(s, schema=s.pd_db_schema))


def get_pd_metric_config():
    """``PdMetricConfig`` from the ``[pd]`` block: provisional anchor = pd.since
    (cadence-anchor decision still open, issue #42), EMEA-baseline PD windows."""
    from isreq_dashboard.metrics.pd_base import PdMetricConfig

    pd_cfg = get_settings().toml.pd
    return PdMetricConfig(
        anchor=pd_cfg.since,
        region_windows=pd_cfg.region_windows_utc,
        low_n_threshold=get_settings().toml.low_n_threshold,
    )


def pd_last_sync():
    """Freshness watermark for the PagerDuty sync (the ``pd_sync_state`` row)."""
    from isreq_dashboard.db.pd_models import PdSyncState

    with get_pd_session_factory()() as s:
        row = s.get(PdSyncState, "pd_incidents")
        return row.last_sync_at if row else None


# --- Change Management (interactive; own `chg` schema, writes our own data) ----

@st.cache_resource
def get_chg_session_factory():
    """Session factory bound to the ``chg`` schema (Change Management; writable)."""
    return make_session_factory(make_engine(get_settings(), schema="chg"))
