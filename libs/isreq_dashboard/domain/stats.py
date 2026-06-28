"""Statistically honest time-to-close (Art. III, FR-022-025, R-008).

Never a lone mean. Every time-to-close summary returns mean + sample standard
deviation (n-1) + coefficient of variation, is labelled sample-based, and flags
low sample sizes. No smoothing/rounding that distorts the data.

Pure logic: no database, no Jira.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

DEFAULT_LOW_N_THRESHOLD = 5


@dataclass(frozen=True)
class CloseStats:
    n: int
    mean: float | None              # seconds; None when n == 0
    stddev_sample: float | None     # n-1; None when n < 2
    cv: float | None                # stddev_sample / mean; None when undefined
    basis: str                      # always "sample"
    low_sample: bool

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "mean": self.mean,
            "stddev_sample": self.stddev_sample,
            "cv": self.cv,
            "basis": self.basis,
            "low_sample": self.low_sample,
        }


def close_stats(
    durations_seconds: list[float],
    low_n_threshold: int = DEFAULT_LOW_N_THRESHOLD,
) -> CloseStats:
    """Summarise a set of time-to-close durations honestly.

    - ``n == 0``: everything None, flagged low-sample.
    - ``n == 1``: mean only; stddev/cv undefined (None), flagged low-sample.
    - ``n >= 2``: mean, sample stddev (n-1), cv = stddev/mean.
    Always ``basis == "sample"`` (we observe a sample of an ongoing process).
    """
    n = len(durations_seconds)
    low = n < low_n_threshold
    if n == 0:
        return CloseStats(0, None, None, None, "sample", True)

    mean = statistics.fmean(durations_seconds)
    if n < 2:
        return CloseStats(n, mean, None, None, "sample", low)

    stddev = statistics.stdev(durations_seconds)  # sample stddev (n-1)
    cv = (stddev / mean) if mean else None
    return CloseStats(n, mean, stddev, cv, "sample", low)
