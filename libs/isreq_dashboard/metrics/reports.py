"""Reports (issue #17): who files the most tickets — overall, Highest, ps5-blocker.

Counts tickets by **reporter** (who raised them). Reporter is captured on sync
(issue #7); tickets with no reporter are grouped as "No reporter". These are filing
counts (volume), not per-person effort — effort attribution stays forbidden (Art. VI).
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import func, select

from isreq_dashboard.db.models import Issue
from isreq_dashboard.metrics.base import (
    MetricConfig,
    _ever_highest_keys,
    _ps5_keys,
    anchor_datetime,
)


def reporter_counts(
    session, keys: set[str] | None = None, top: int = 20, *, cfg: MetricConfig | None = None
) -> pd.DataFrame:
    """``[reporter, count]`` of tickets filed per reporter, biggest first (top ``top``).

    ``keys=None`` counts every ticket; pass a key set to restrict (e.g. ever-Highest).
    With ``cfg`` set, pre-inception tickets (before the anchor, e.g. ISREQ-1) are excluded.
    """
    stmt = select(Issue.reporter_name, func.count()).group_by(Issue.reporter_name)
    if keys is not None:
        if not keys:
            return pd.DataFrame(columns=["reporter", "count"])
        stmt = stmt.where(Issue.key.in_(keys))
    if cfg is not None:
        stmt = stmt.where(Issue.created_at >= anchor_datetime(cfg.anchor))
    rows = session.execute(stmt).all()
    data = [{"reporter": name or "No reporter", "count": int(n)} for name, n in rows]
    if not data:
        return pd.DataFrame(columns=["reporter", "count"])
    return (
        pd.DataFrame(data).sort_values("count", ascending=False).head(top).reset_index(drop=True)
    )


def top_reporters_all(session, cfg: MetricConfig, top: int = 20) -> pd.DataFrame:
    return reporter_counts(session, None, top, cfg=cfg)


def top_reporters_highest(session, cfg: MetricConfig, top: int = 20) -> pd.DataFrame:
    return reporter_counts(session, _ever_highest_keys(session, cfg), top, cfg=cfg)


def top_reporters_ps5(session, cfg: MetricConfig, top: int = 20) -> pd.DataFrame:
    return reporter_counts(session, _ps5_keys(session, cfg), top, cfg=cfg)
