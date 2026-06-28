"""Status mix: count of issues per period broken down by their current status.

Answers "for pulse N (or week W), how many tickets are Untriaged / Triaged / In
Progress / In Review / BLOCKED / Done / Rejected / …". Status is the issue's
*current* status, not point-in-time.

Per-pulse cadence buckets **sprinted** tickets by their sprint name (the issue's
``pulse``). **Unsprinted** tickets carry no sprint, so instead of dumping them in
an ``Unknown`` bar they are placed in the pulse whose 2-week window contained the
moment they *entered their current status* — e.g. a ticket escalated during Pulse
9 and still in that status lands on Pulse 9 (see ``domain.weeks.pulse_number_at``).
Weekly cadence buckets by the week the issue was created (consistent with Intake).
PR/MP and scope selectors apply as elsewhere.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from isreq_dashboard.metrics.base import (
    PER_PULSE,
    UNKNOWN_GROUP,
    MetricConfig,
    Selector,
    learn_pulse_naming,
    load_scoped_issues,
    load_status_intervals,
    period_for,
    pulse_label_for_time,
)

__all__ = ["status_mix_series"]


def _status_entry_times(session, keys: set[str]) -> dict[str, datetime]:
    """For each key, the ``valid_from`` of its open (current) status interval — i.e.
    when the ticket most recently entered the status it holds now."""
    entry: dict[str, datetime] = {}
    for key, ivs in load_status_intervals(session, keys).items():
        spell = [iv for iv in ivs if iv.valid_to is None] or ivs
        if spell:
            entry[key] = max(spell, key=lambda iv: iv.valid_from).valid_from
    return entry


def _per_pulse_periods(session, cfg: MetricConfig, issues) -> dict[str, str]:
    """Pulse label per issue: sprinted -> its sprint name; unsprinted -> the pulse
    that contained its current-status entry (``Unknown`` only if undatable)."""
    unsprinted = {k for k, v in issues.items() if not v.pulse}
    entry = _status_entry_times(session, unsprinted)
    naming = learn_pulse_naming(issues.values())

    periods: dict[str, str] = {}
    for key, issue in issues.items():
        if issue.pulse:
            periods[key] = issue.pulse
            continue
        t = entry.get(key)
        label = pulse_label_for_time(t, cfg.anchor, naming) if t is not None else None
        periods[key] = label if label is not None else UNKNOWN_GROUP
    return periods


def status_mix_series(session, cfg: MetricConfig, sel: Selector) -> pd.DataFrame:
    """Long frame ``[period, group, count]`` where ``group`` is the current status."""
    issues = load_scoped_issues(session, cfg, sel)
    if sel.cadence == PER_PULSE:
        periods = _per_pulse_periods(session, cfg, issues)
    else:
        periods = {k: period_for(sel.cadence, v, v.created_at, cfg.anchor) for k, v in issues.items()}
    records = [
        {"period": periods[k], "group": i.current_status or UNKNOWN_GROUP}
        for k, i in issues.items()
    ]
    if not records:
        return pd.DataFrame(columns=["period", "group", "count"])
    df = pd.DataFrame(records)
    return (
        df.groupby(["period", "group"]).size().reset_index(name="count").sort_values(["period", "group"])
    )
