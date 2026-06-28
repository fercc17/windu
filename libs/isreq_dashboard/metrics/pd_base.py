"""Shared scaffolding for the PagerDuty metrics (mirrors ``metrics/base.py``).

Each PD metric is a pure function of the synced ``pd`` rows plus a small selector.
Bucketing by week/pulse reuses the shared calendar (``domain/weeks`` via
``metrics.base.event_period``) and region splits reuse ``domain/regions`` with the
PD-specific windows. Nothing here reads the ``isreq`` schema (Art. VIII isolation).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer

from isreq_dashboard.db.pd_models import PdAlert, PdIncident
from isreq_dashboard.domain.regions import UNKNOWN, region_from_timestamp
from isreq_dashboard.metrics.base import PER_PULSE, WEEKLY, event_period

# re-exported so PD metric modules and pages import cadence constants from one place
WEEKLY = WEEKLY
PER_PULSE = PER_PULSE


@dataclass(frozen=True)
class PdMetricConfig:
    """Config for the PagerDuty metrics.

    ``anchor`` drives week/pulse numbering. PD data starts 2026-01-01, so the
    provisional anchor is that date (the cadence-anchor vs ISReq's 2026-02-09 is the
    open decision in issue #42); weekly numbering is well-defined either way, only
    the per-pulse *labels* depend on the choice.

    ``start``/``end`` optionally restrict every metric to a trigger-time window
    (``start <= created_at < end``); ``None`` means unbounded on that side.
    """

    anchor: date
    region_windows: Mapping[str, Mapping[str, str]] | None = None
    pulse_prefix: str = "IS Pulse 2026#"
    low_n_threshold: int = 5
    start: datetime | None = None
    end: datetime | None = None


def alert_select(cfg: PdMetricConfig):
    """``select(PdAlert)`` restricted to the configured trigger-time window.

    ``raw_details`` (the full alert payload, several KB of JSONB per row) is deferred:
    the metrics only read the derived columns, so loading the payload on every scan was
    the dominant cost. It is fetched lazily only if a row's ``raw_details`` is accessed
    (e.g. a re-derive), never during normal metric reads.
    """
    q = select(PdAlert).options(defer(PdAlert.raw_details))
    if cfg.start is not None:
        q = q.where(PdAlert.created_at >= cfg.start)
    if cfg.end is not None:
        q = q.where(PdAlert.created_at < cfg.end)
    return q


def incident_select(cfg: PdMetricConfig):
    """``select(PdIncident)`` restricted to the configured trigger-time window."""
    q = select(PdIncident)
    if cfg.start is not None:
        q = q.where(PdIncident.created_at >= cfg.start)
    if cfg.end is not None:
        q = q.where(PdIncident.created_at < cfg.end)
    return q


def data_window(session: Session) -> tuple[datetime | None, datetime | None]:
    """(earliest, latest) alert trigger time across ALL synced data (unfiltered),
    so the UI can show the full extent the analysis is drawn from."""
    return tuple(session.execute(select(func.min(PdAlert.created_at), func.max(PdAlert.created_at))).one())


def pd_period(cadence: str, t: datetime, cfg: PdMetricConfig) -> str:
    """Period key for an alert/incident time: week (weekly) or pulse (per-pulse).

    Per-pulse labels are generated from the shared 2-week calendar with no ISReq
    cross-query (an empty name map + the configured prefix -> ``IS Pulse 2026#NN``).
    """
    return event_period(cadence, t, cfg.anchor, ({}, cfg.pulse_prefix))


def region_of(t: datetime, cfg: PdMetricConfig) -> str:
    """Region of an alert by trigger time-of-day (the locked definition), EMEA-baseline
    PD windows. No windows configured -> ``Unknown``."""
    if not cfg.region_windows:
        return UNKNOWN
    return region_from_timestamp(t, cfg.region_windows)
