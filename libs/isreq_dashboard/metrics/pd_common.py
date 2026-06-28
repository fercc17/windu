"""Finding 1 + 3/4: most-common alerts, and alert volume per period by dimension.

``alert_frequency`` is the headline ranking ("the top alert fires ~N/day and is X%
of all alerts"): per alertname, the count, the per-day rate over the data window,
and the share of total. ``alert_volume`` is the weekly/per-pulse time series, split
by any dimension (cloud, model, region, alertname) — the same frame shape the ISReq
charts consume.

Pure read of the ``pd`` schema; honest about coverage (``Unknown`` is a real bucket,
never dropped, so a thin cloud/model breakdown is visible as such).
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from isreq_dashboard.db.pd_models import PdAlert
from isreq_dashboard.domain.regions import UNKNOWN
from isreq_dashboard.metrics.pd_base import PdMetricConfig, alert_select, pd_period, region_of


def alerts_in_period(session: Session, cfg: PdMetricConfig, cadence: str, period: str) -> list[dict]:
    """Underlying alerts whose trigger time falls in ``period`` (drill-down: an
    aggregate you cannot open is a rumour, ISReq Art. II). Newest first."""
    rows = []
    for a in session.scalars(alert_select(cfg)):
        if pd_period(cadence, a.created_at, cfg) == period:
            rows.append(
                {
                    "created_at": a.created_at,
                    "alertname": a.alertname,
                    "cloud": a.cloud,
                    "juju_model": a.juju_model,
                    "charm": a.charm,
                    "severity": a.severity,
                    "region": region_of(a.created_at, cfg),
                    "summary": a.summary,
                    "incident_id": a.incident_id,
                }
            )
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return rows

# Dimensions an alert volume/most-common view can split by.
GROUP_CLOUD = "cloud"
GROUP_MODEL = "juju_model"
GROUP_CHARM = "charm"
GROUP_ALERTNAME = "alertname"
GROUP_REGION = "region"


def _window_days(times: list) -> float:
    """Span of the data window in days (>=1), the shared denominator for per-day rates."""
    if not times:
        return 1.0
    span = (max(times) - min(times)).total_seconds() / 86400.0
    return max(1.0, span)


def alert_frequency(session: Session, cfg: PdMetricConfig, *, region: str | None = None,
                    alerts: list | None = None) -> pd.DataFrame:
    """Most-common alerts: ``[alertname, count, per_day, share]`` sorted desc.

    ``per_day`` uses the full data window as denominator (same for every alertname, so
    rates are comparable); ``share`` is count / total alerts in the (optionally
    region-filtered) selection. Region filter uses trigger time-of-day.
    """
    alerts = alerts if alerts is not None else session.scalars(alert_select(cfg)).all()
    all_times = [a.created_at for a in alerts]
    window_days = _window_days(all_times)

    rows = [
        (a.alertname or UNKNOWN,)
        for a in alerts
        if region is None or region_of(a.created_at, cfg) == region
    ]
    cols = ["alertname", "count", "per_day", "share"]
    if not rows:
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(rows, columns=["alertname"])
    total = len(df)
    g = df.groupby("alertname").size().reset_index(name="count")
    g["per_day"] = g["count"] / window_days
    g["share"] = g["count"] / total
    return g.sort_values(["count", "alertname"], ascending=[False, True]).reset_index(drop=True)


def dimension_totals(session: Session, cfg: PdMetricConfig, dimension: str = GROUP_CLOUD,
                     *, alerts: list | None = None) -> pd.DataFrame:
    """Total alert counts by a dimension (cloud / model / charm / alertname / region):
    ``[label, count, share]`` desc. The headline per-cloud / per-model / per-charm view."""
    alerts = alerts if alerts is not None else session.scalars(alert_select(cfg)).all()
    if dimension == GROUP_REGION:
        vals = [region_of(a.created_at, cfg) for a in alerts]
    else:
        vals = [getattr(a, dimension, None) or UNKNOWN for a in alerts]
    cols = ["label", "count", "share"]
    if not vals:
        return pd.DataFrame(columns=cols)
    counts = pd.Series(vals).value_counts()
    df = counts.reset_index()
    df.columns = ["label", "count"]
    df["share"] = df["count"] / int(df["count"].sum())
    return df.reset_index(drop=True)


def classification_coverage(session: Session, cfg: PdMetricConfig,
                            *, alerts: list | None = None) -> dict[str, float]:
    """Fraction of stored alerts with a parseable (non-``Unknown``) value per field, so a
    thin cloud/charm breakdown is shown honestly rather than as if complete."""
    alerts = alerts if alerts is not None else session.scalars(alert_select(cfg)).all()
    fields = ("alertname", "cloud", "juju_model", "charm")
    n = len(alerts)
    if not n:
        return {f: 0.0 for f in fields}
    return {f: sum(1 for a in alerts if (getattr(a, f) or UNKNOWN) != UNKNOWN) / n for f in fields}


def alert_volume(session: Session, cfg: PdMetricConfig, cadence: str, *, group: str = GROUP_CLOUD,
                 alerts: list | None = None) -> pd.DataFrame:
    """Alert volume per period, split by ``group``: long frame ``[period, group, count]``.

    ``group`` is one of cloud / juju_model / charm / alertname / region. The weekly vs
    per-pulse toggle is ``cadence``; the region split is ``group=region``.
    """
    alerts = alerts if alerts is not None else session.scalars(alert_select(cfg)).all()
    recs = []
    for a in alerts:
        period = pd_period(cadence, a.created_at, cfg)
        if group == GROUP_REGION:
            gval = region_of(a.created_at, cfg)
        else:
            gval = getattr(a, group, None) or UNKNOWN
        recs.append((period, gval))
    cols = ["period", "group", "count"]
    if not recs:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(recs, columns=["period", "group"])
    return df.groupby(["period", "group"]).size().reset_index(name="count")
