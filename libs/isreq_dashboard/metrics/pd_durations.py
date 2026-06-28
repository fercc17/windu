"""Findings 2 + response-time core: MTTA/MTTR per period, and slowest alert types.

MTTA = first-acknowledge - trigger; MTTR = resolve - trigger (incident level, from the
derived ``acknowledged_at``/``resolved_at``). ``slowest_by_alertname`` attributes each
incident's MTTR to its dominant alert type and reports median + mean + sample-SD + CV
**and** the count, so "slow but rare" is distinguishable from "slow and frequent"
(honest stats via ``domain/stats.py``; never a lone average).
"""

from __future__ import annotations

import statistics
from collections import Counter

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from isreq_dashboard.db.pd_models import PdAlert, PdIncident
from isreq_dashboard.domain.regions import UNKNOWN
from isreq_dashboard.domain.stats import close_stats
from isreq_dashboard.metrics.pd_base import PdMetricConfig, incident_select, pd_period

_DAY = 86400.0


def _mtta_seconds(inc: PdIncident) -> float | None:
    if inc.created_at and inc.acknowledged_at:
        return (inc.acknowledged_at - inc.created_at).total_seconds()
    return None


def _mttr_seconds(inc: PdIncident) -> float | None:
    if inc.created_at and inc.resolved_at:
        return (inc.resolved_at - inc.created_at).total_seconds()
    return None


def incident_alertname(session: Session) -> dict[str, str]:
    """``{incident_id: dominant alert type}`` — the most frequent alertname among the
    incident's alerts (ties -> the first seen). Drives slow-by-type attribution; the
    modelling choice for mixed-type incidents is flagged in the page."""
    by_inc: dict[str, list[str]] = {}
    for inc_id, alertname in session.execute(select(PdAlert.incident_id, PdAlert.alertname)):
        by_inc.setdefault(inc_id, []).append(alertname or UNKNOWN)
    return {inc_id: Counter(names).most_common(1)[0][0] for inc_id, names in by_inc.items()}


def mtta_mttr_by_period(session: Session, cfg: PdMetricConfig, cadence: str) -> pd.DataFrame:
    """Per-period mean MTTA/MTTR in days plus the incident count: ``[period, mtta_days,
    mttr_days, n]``. Means skip incidents missing the relevant timestamp."""
    rows = []
    for inc in session.scalars(incident_select(cfg)):
        rows.append((pd_period(cadence, inc.created_at, cfg), _mtta_seconds(inc), _mttr_seconds(inc)))
    cols = ["period", "mtta_days", "mttr_days", "n"]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows, columns=["period", "mtta", "mttr"])

    def _mean_days(s: pd.Series) -> float | None:
        vals = s.dropna()
        return (vals.mean() / _DAY) if len(vals) else None

    return (
        df.groupby("period")
        .agg(mtta_days=("mtta", _mean_days), mttr_days=("mttr", _mean_days), n=("mttr", "size"))
        .reset_index()
    )


def slowest_by_alertname(session: Session, cfg: PdMetricConfig) -> pd.DataFrame:
    """Slowest alert types by resolve time: ``[alertname, count, median_days, mean_days,
    sd_days, cv, low_sample]`` sorted by median desc. Honest stats: median + mean +
    sample SD + CV, with the count so frequency is never hidden behind a duration."""
    names = incident_alertname(session)
    durations: dict[str, list[float]] = {}
    for inc in session.scalars(incident_select(cfg)):
        mttr = _mttr_seconds(inc)
        if mttr is None:
            continue
        durations.setdefault(names.get(inc.id, UNKNOWN), []).append(mttr)

    recs = []
    for name, secs in durations.items():
        cs = close_stats(secs, cfg.low_n_threshold)
        recs.append(
            {
                "alertname": name,
                "count": cs.n,
                "median_days": statistics.median(secs) / _DAY,
                "mean_days": (cs.mean / _DAY) if cs.mean is not None else None,
                "sd_days": (cs.stddev_sample / _DAY) if cs.stddev_sample is not None else None,
                "cv": cs.cv,
                "low_sample": cs.low_sample,
            }
        )
    df = pd.DataFrame(recs)
    if df.empty:
        return df
    return df.sort_values(["median_days", "count"], ascending=[False, False]).reset_index(drop=True)
