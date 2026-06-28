"""Finding 5: Pareto (cumulative-share) of alerts by type, service, or model.

"X% of alert types cause Y% of all pages." Returns a ranked frame with the running
cumulative share, the same shape the ISReq noisy-service Pareto uses.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from isreq_dashboard.db.pd_models import PdAlert, PdIncident, PdService
from isreq_dashboard.domain.regions import UNKNOWN
from isreq_dashboard.metrics.pd_base import PdMetricConfig, alert_select

DIM_ALERTNAME = "alertname"
DIM_MODEL = "juju_model"
DIM_SERVICE = "service"


def _values(session: Session, cfg: PdMetricConfig, dimension: str) -> list[str]:
    if dimension == DIM_SERVICE:
        # service is incident-level: attribute each alert to its incident's service name.
        svc_name = {s.id: (s.name or s.id) for s in session.scalars(select(PdService))}
        inc_svc = {i.id: i.service_id for i in session.scalars(select(PdIncident))}
        out = []
        for a in session.scalars(alert_select(cfg)):
            sid = inc_svc.get(a.incident_id)
            out.append(svc_name.get(sid, UNKNOWN) if sid else UNKNOWN)
        return out
    return [getattr(a, dimension, None) or UNKNOWN for a in session.scalars(alert_select(cfg))]


def pareto(session: Session, cfg: PdMetricConfig, dimension: str = DIM_ALERTNAME) -> pd.DataFrame:
    """``[rank, label, count, cum_count, cum_share]`` over ``dimension`` (alertname /
    juju_model / service), most frequent first."""
    vals = _values(session, cfg, dimension)
    cols = ["rank", "label", "count", "cum_count", "cum_share"]
    if not vals:
        return pd.DataFrame(columns=cols)
    counts = pd.Series(vals).value_counts()
    df = counts.reset_index()
    df.columns = ["label", "count"]
    df.insert(0, "rank", range(1, len(df) + 1))
    total = int(df["count"].sum())
    df["cum_count"] = df["count"].cumsum()
    df["cum_share"] = df["cum_count"] / total
    return df
