"""Finding 7: SRE on-call load + per-engineer handling-time distribution.

Load, three distinct measures (deliberately not conflated):
  - received: incidents assigned/paged to each SRE (incident ``assigned_user_id``).
  - handled:  acknowledge/resolve log entries acted on by each SRE.
  - time_spent: summed handling time (ack -> resolve) attributed to the SRE who
                resolved the incident (else the one who acknowledged it).
Plus each SRE's share of the team total (``*_pct``) and ``disproportion`` (% time − %
alerts). ``sre_time_stats`` reports the honest distribution of an SRE's per-incident
handling time: n, mean, sample SD, CV, p50/p75/p95.

Count-based measures need no constant; the time measures are summed/derived straight
from the timeline.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from isreq_dashboard.db.pd_models import PdIncident, PdLogEntry, PdUser
from isreq_dashboard.domain.stats import close_stats
from isreq_dashboard.metrics.pd_base import PdMetricConfig

_HANDLING_TYPES = ("acknowledge", "resolve")
_HOUR = 3600.0


def _collect(session: Session, cfg: PdMetricConfig):
    """Shared windowed scan: ``(users, received, handled, durations)``.

    ``durations`` maps each SRE -> the list of their incident handling times (ack→resolve
    seconds), the SRE being the resolver (else acknowledger, else assignee). Only the
    needed columns are loaded (the timeline is ~64k entries).
    """
    users = {u.id: (u.name or u.id) for u in session.scalars(select(PdUser))}

    iq = select(PdIncident.id, PdIncident.assigned_user_id,
                PdIncident.acknowledged_at, PdIncident.resolved_at)
    if cfg.start is not None:
        iq = iq.where(PdIncident.created_at >= cfg.start)
    if cfg.end is not None:
        iq = iq.where(PdIncident.created_at < cfg.end)
    incidents = session.execute(iq).all()
    inc_ids = {r[0] for r in incidents}

    received: dict[str, int] = {}
    for _id, assigned, _ack, _res in incidents:
        if assigned:
            received[assigned] = received.get(assigned, 0) + 1

    handled: dict[str, int] = {}
    resolver: dict[str, str] = {}
    acker: dict[str, str] = {}
    leq = (
        select(PdLogEntry.incident_id, PdLogEntry.type, PdLogEntry.agent_user_id)
        .where(PdLogEntry.agent_user_id.is_not(None))
        .order_by(PdLogEntry.at)  # first ack / last resolve per incident win
    )
    for iid, typ, agent in session.execute(leq):
        if iid not in inc_ids:
            continue
        if typ in _HANDLING_TYPES:
            handled[agent] = handled.get(agent, 0) + 1
        if typ == "resolve":
            resolver[iid] = agent
        elif typ == "acknowledge":
            acker.setdefault(iid, agent)

    durations: dict[str, list[float]] = {}
    for _id, assigned, ack, res in incidents:
        if not (ack and res):
            continue
        sre = resolver.get(_id) or acker.get(_id) or assigned
        if not sre:
            continue
        dur = (res - ack).total_seconds()
        if dur > 0:
            durations.setdefault(sre, []).append(dur)

    return users, received, handled, durations


def sre_load(session: Session, cfg: PdMetricConfig) -> pd.DataFrame:
    """Per-SRE load: ``[user_id, name, received, handled, time_spent_hours, received_pct,
    handled_pct, time_spent_pct, disproportion]``."""
    users, received, handled, durations = _collect(session, cfg)
    time_spent = {sre: sum(secs) for sre, secs in durations.items()}

    uids = set(received) | set(handled) | set(time_spent)
    cols = ["user_id", "name", "received", "handled", "time_spent_hours",
            "received_pct", "handled_pct", "time_spent_pct", "disproportion"]
    if not uids:
        return pd.DataFrame(columns=cols)

    tot_received = sum(received.values()) or 1
    tot_handled = sum(handled.values()) or 1
    tot_time = sum(time_spent.values()) or 1.0

    recs = []
    for u in uids:
        rp = received.get(u, 0) / tot_received * 100
        hp = handled.get(u, 0) / tot_handled * 100
        tp = time_spent.get(u, 0.0) / tot_time * 100
        recs.append({
            "user_id": u,
            "name": users.get(u, u),
            "received": received.get(u, 0),
            "handled": handled.get(u, 0),
            "time_spent_hours": round(time_spent.get(u, 0.0) / 3600.0, 2),
            "received_pct": round(rp, 1),
            "handled_pct": round(hp, 1),
            "time_spent_pct": round(tp, 1),
            # +ve: more time-share than alert-share -> this SRE carries the long incidents
            "disproportion": round(tp - hp, 1),
        })
    return pd.DataFrame(recs).sort_values(["handled", "time_spent_hours"], ascending=False).reset_index(drop=True)


def sre_time_stats(session: Session, cfg: PdMetricConfig) -> pd.DataFrame:
    """Per-engineer handling-time distribution (hours): ``[name, n, mean_h, sd_h, cv,
    p50_h, p75_h, p95_h, low_sample]``, slowest median first.

    Time on alert = each incident's ack→resolve span attributed to its resolver (else
    acknowledger). Honest stats: sample SD + CV (via ``stats.close_stats``) with the count
    and a low-sample flag, alongside the p50/p75/p95 percentiles.
    """
    users, _received, _handled, durations = _collect(session, cfg)
    cols = ["name", "n", "mean_h", "sd_h", "cv", "p50_h", "p75_h", "p95_h", "low_sample"]
    if not durations:
        return pd.DataFrame(columns=cols)

    rows = []
    for sre, secs in durations.items():
        cs = close_stats(secs, cfg.low_n_threshold)
        arr = np.array(secs, dtype=float)
        rows.append({
            "name": users.get(sre, sre),
            "n": cs.n,
            "mean_h": round(cs.mean / _HOUR, 2) if cs.mean is not None else None,
            "sd_h": round(cs.stddev_sample / _HOUR, 2) if cs.stddev_sample is not None else None,
            "cv": round(cs.cv, 2) if cs.cv is not None else None,
            "p50_h": round(float(np.percentile(arr, 50)) / _HOUR, 2),
            "p75_h": round(float(np.percentile(arr, 75)) / _HOUR, 2),
            "p95_h": round(float(np.percentile(arr, 95)) / _HOUR, 2),
            "low_sample": cs.low_sample,
        })
    # volume first: the engineers who actually carry load (robust stats) lead; the
    # 1-incident outliers (often non-team agents who touched one incident) fall to the
    # bottom rather than dominating a median sort.
    return pd.DataFrame(rows).sort_values(["n", "p50_h"], ascending=[False, False]).reset_index(drop=True)
