"""
Pure DORA-metric computations.

No Django/DB/network imports — everything here operates on plain dataclass
records so it is trivially unit-testable and reusable from the view, the API, or
a future report job. The view adapts ORM rows into these records.

The four DORA metrics and their sources:

    deployment frequency   ← DeployRecord      (Flux; pending)
    lead time for changes  ← DeployRecord      (Flux; pending)
    change failure rate    ← Deploy + Incident (Flux + PagerDuty; partial)
    mean time to recovery  ← IncidentRecord    (PagerDuty; live now)

Deploy-sourced metrics return ``None`` when there are no deploy records — that is
the "pending Flux" signal, distinct from a real zero.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import mean, median
from typing import Optional, Sequence


@dataclass(frozen=True)
class IncidentRecord:
    created_at: datetime
    resolved_at: Optional[datetime] = None
    cloud: Optional[str] = None
    team: Optional[str] = None

    @property
    def resolution_seconds(self) -> Optional[float]:
        if self.resolved_at and self.created_at:
            return max(0.0, (self.resolved_at - self.created_at).total_seconds())
        return None


@dataclass(frozen=True)
class DeployRecord:
    reconciled_at: datetime
    committed_at: Optional[datetime] = None
    succeeded: bool = True
    cloud: Optional[str] = None
    environment: Optional[str] = None

    @property
    def lead_time_seconds(self) -> Optional[float]:
        if self.committed_at and self.reconciled_at:
            return max(0.0, (self.reconciled_at - self.committed_at).total_seconds())
        return None


@dataclass(frozen=True)
class DoraSummary:
    """The four DORA metrics over one window. ``None`` = no data (pending)."""
    window_days: int
    deployment_frequency_per_day: Optional[float]
    lead_time_seconds: Optional[float]
    change_failure_rate: Optional[float]
    mttr_seconds: Optional[float]
    median_ttr_seconds: Optional[float]
    deploy_count: int = 0
    incident_count: int = 0
    resolved_count: int = 0
    flux_pending: bool = True


def mttr_seconds(incidents: Sequence[IncidentRecord]) -> Optional[float]:
    """Mean time to recovery: average resolution time over *resolved* incidents."""
    vals = [i.resolution_seconds for i in incidents if i.resolution_seconds is not None]
    return mean(vals) if vals else None


def median_ttr_seconds(incidents: Sequence[IncidentRecord]) -> Optional[float]:
    vals = [i.resolution_seconds for i in incidents if i.resolution_seconds is not None]
    return median(vals) if vals else None


def deployment_frequency_per_day(
    deploys: Sequence[DeployRecord], window_days: int
) -> Optional[float]:
    """Deploys per day over the window. None when there are no deploys (pending)."""
    if not deploys or window_days <= 0:
        return None
    return len(deploys) / window_days


def lead_time_seconds(deploys: Sequence[DeployRecord]) -> Optional[float]:
    """Median commit→reconcile time. None when no deploy has a commit timestamp."""
    vals = [d.lead_time_seconds for d in deploys if d.lead_time_seconds is not None]
    return median(vals) if vals else None


def change_failure_rate(
    deploys: Sequence[DeployRecord],
    incidents: Sequence[IncidentRecord] = (),
    *,
    correlation_window: timedelta = timedelta(hours=24),
) -> Optional[float]:
    """Fraction of deploys that resulted in a failure.

    A deploy counts as a failure if its reconcile failed, OR a high-level
    incident on the same cloud started within ``correlation_window`` after it.
    Heuristic by design — refine once deploy/incident attribution sharpens.
    None when there are no deploys (pending Flux).
    """
    if not deploys:
        return None
    incidents = list(incidents)
    failures = 0
    for d in deploys:
        if not d.succeeded:
            failures += 1
            continue
        for inc in incidents:
            if d.cloud and inc.cloud and d.cloud != inc.cloud:
                continue
            if d.reconciled_at <= inc.created_at <= d.reconciled_at + correlation_window:
                failures += 1
                break
    return failures / len(deploys)


def compute_summary(
    incidents: Sequence[IncidentRecord],
    deploys: Sequence[DeployRecord],
    window_days: int,
) -> DoraSummary:
    """Roll the four metrics into one summary for a window."""
    resolved = [i for i in incidents if i.resolution_seconds is not None]
    return DoraSummary(
        window_days=window_days,
        deployment_frequency_per_day=deployment_frequency_per_day(deploys, window_days),
        lead_time_seconds=lead_time_seconds(deploys),
        change_failure_rate=change_failure_rate(deploys, incidents),
        mttr_seconds=mttr_seconds(incidents),
        median_ttr_seconds=median_ttr_seconds(incidents),
        deploy_count=len(deploys),
        incident_count=len(incidents),
        resolved_count=len(resolved),
        flux_pending=not deploys,
    )


# --- attribution helpers (pure) ------------------------------------------------

def parse_cloud(title: Optional[str], clouds: Sequence[str]) -> Optional[str]:
    """Best-effort cloud slug from an incident title.

    Matches whole-word against the known cloud slugs, longest first so
    ``microcloud-drs`` wins over a bare ``ps`` substring. Returns None when no
    slug is present — the caller then falls back to the matched env's cloud.
    """
    if not title:
        return None
    for slug in sorted(set(clouds), key=len, reverse=True):
        if re.search(rf'(?<![\w-]){re.escape(slug)}(?![\w-])', title, re.IGNORECASE):
            return slug
    return None


def humanize_seconds(seconds: Optional[float]) -> str:
    """Render a duration as ``2d 3h`` / ``4h 12m`` / ``45m`` / ``30s`` / ``—``."""
    if seconds is None:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    mins, _ = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


# --- DORA performance levels --------------------------------------------------
# Thresholds follow the DORA / Accelerate "State of DevOps" benchmarks (Elite /
# High / Medium / Low). They are the single source of truth for both the per-card
# badge and the reference table on /dora/ — tune them here and both update.
#
# Boundaries are monotonic for a usable UI; the official reports collapse some of
# change-failure-rate's High/Medium/Low into one band, so the 30%/45% splits here
# are a pragmatic interpolation.

_HOUR = 3600
_DAY = 86400
_WEEK = 7 * _DAY
_MONTH = 30 * _DAY

LEVELS = ("elite", "high", "medium", "low")

LEVEL_META = {
    "elite":  {"label": "Elite",  "badge": "success"},
    "high":   {"label": "High",   "badge": "primary"},
    "medium": {"label": "Medium", "badge": "warning"},
    "low":    {"label": "Low",    "badge": "danger"},
}


def classify_deploy_frequency(per_day: Optional[float]) -> Optional[str]:
    """Higher is better. Elite ≥1/day, High ≥1/week, Medium ≥1/month, else Low."""
    if per_day is None:
        return None
    if per_day >= 1:
        return "elite"
    if per_day >= 1 / 7:
        return "high"
    if per_day >= 1 / 30:
        return "medium"
    return "low"


def classify_lead_time(seconds: Optional[float]) -> Optional[str]:
    """Lower is better. Elite <1d, High <1w, Medium <1mo, else Low."""
    if seconds is None:
        return None
    if seconds < _DAY:
        return "elite"
    if seconds < _WEEK:
        return "high"
    if seconds < _MONTH:
        return "medium"
    return "low"


def classify_cfr(rate: Optional[float]) -> Optional[str]:
    """Lower is better. Elite ≤15%, High ≤30%, Medium ≤45%, else Low."""
    if rate is None:
        return None
    if rate <= 0.15:
        return "elite"
    if rate <= 0.30:
        return "high"
    if rate <= 0.45:
        return "medium"
    return "low"


def classify_mttr(seconds: Optional[float]) -> Optional[str]:
    """Lower is better. Elite <1h, High <1d, Medium <1w, else Low."""
    if seconds is None:
        return None
    if seconds < _HOUR:
        return "elite"
    if seconds < _DAY:
        return "high"
    if seconds < _WEEK:
        return "medium"
    return "low"


_CLASSIFIERS = {
    "deploy_freq": classify_deploy_frequency,
    "lead_time": classify_lead_time,
    "cfr": classify_cfr,
    "mttr": classify_mttr,
}


def classify_metric(metric: str, value: Optional[float]) -> Optional[str]:
    """Dispatch to the right classifier; returns a level slug or None (no data)."""
    return _CLASSIFIERS[metric](value)


# Reference table rows (one per metric), rendered on /dora/ and the single source
# describing what each level means. ``key`` matches the card/classifier keys so
# the view can highlight the band the current value falls into.
DORA_LEVELS_TABLE = [
    {"key": "deploy_freq", "metric": "Deployment frequency", "better": "higher", "bands": [
        {"level": "elite", "label": "On-demand (≥ 1 / day)"},
        {"level": "high", "label": "≥ 1 / week"},
        {"level": "medium", "label": "≥ 1 / month"},
        {"level": "low", "label": "< 1 / month"},
    ]},
    {"key": "lead_time", "metric": "Lead time for changes", "better": "lower", "bands": [
        {"level": "elite", "label": "< 1 day"},
        {"level": "high", "label": "< 1 week"},
        {"level": "medium", "label": "< 1 month"},
        {"level": "low", "label": "≥ 1 month"},
    ]},
    {"key": "cfr", "metric": "Change failure rate", "better": "lower", "bands": [
        {"level": "elite", "label": "≤ 15%"},
        {"level": "high", "label": "≤ 30%"},
        {"level": "medium", "label": "≤ 45%"},
        {"level": "low", "label": "> 45%"},
    ]},
    {"key": "mttr", "metric": "Mean time to recovery", "better": "lower", "bands": [
        {"level": "elite", "label": "< 1 hour"},
        {"level": "high", "label": "< 1 day"},
        {"level": "medium", "label": "< 1 week"},
        {"level": "low", "label": "≥ 1 week"},
    ]},
]
