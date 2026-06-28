# Contract: Metrics & Drill-down

Each metric is a pure function of the synced data plus `(cadence, scope, period)` selectors. Every metric has a paired **drill-down** that returns the exact underlying issues. These definitions are the testable contract the UI must satisfy; they are computed in SQL/`metrics/*` against the `isreq` schema, never by calling Jira.

Common selectors:
- `cadence ∈ {weekly, per_pulse}` — period key is `week(t)` (FR-011) or `pulse` (FR-012).
- `scope ∈ {all, ps5_blocker}` — `ps5_blocker` restricts to issues with the configured label; definitions otherwise identical (FR-013).
- Drill-down rows always include **`key`, `title`, `assignee_name`** (FR-020).

---

## M1 — Highest create-vs-close (north star, FR-006/007/009)

**Inputs**: `priority_intervals`, `status_intervals`, `highest_priority_name`.

- `became_highest(period)` = count of **entry events** into Highest (interval starts where `priority=Highest`, including creation-at-Highest) with `valid_from` in period. Multiple entries for one issue are each counted (clarified 2026-06-12).
- `highest_closed(period)` = count of close events in period for issues that were Highest at the moment of close.
- `highest_exits(period)` = Highest entries that ended in period by dropping below Highest **or** closing.
- `highest_backlog(t)` = issues currently in a `Highest` interval covering `t` and open at `t` = `Σ became_highest − Σ highest_exits` up to `t`.

**Legibility requirement**: the primary view plots `became_highest` vs `highest_closed` per period and the cumulative `highest_backlog`, so create-outpaces-close is visible at a glance.

**Drill-downs**:
- "became Highest in period N" → issues with a Highest entry whose `valid_from` ∈ N.
- "Highest closed in period N" → close events ∈ N where issue was Highest at close.
- "Highest open at end of N" → issues in a Highest interval open at period-N end.

## M2 — Intake (FR-014)

`intake(period, group)` = count of issues with `created_at` in period, grouped by `group ∈ {area, sub_area, region_time_of_day}`.
Drill-down "created in period N" → `week(created_at)=N` (or pulse), **never** filtered by close (FR-021).

## M3 — Throughput (FR-015)

`throughput(period)` = count of **close events** with `closed_at` in period. A closed→reopened→closed issue contributes one event per close.
Drill-down "closed in period N" → close events with `week(closed_at)=N`, joined to issues — independent of `created_at`.

## M4 — Backlog (FR-016)

`backlog(t)` = count of issues with `created_at ≤ t` and not in a closed status as of `t` (from `status_intervals`; reopened ⇒ open again until re-closed).
Drill-down "open at T" → those issues.

## M5 — Time invested (FR-017/018)

`time_invested(period, area)` = Σ `time_spent_seconds` of worklogs with `started_at` in period, grouped by issue/area only.
**Presentation contract**: labeled *best-effort, depends on logging discipline*; **no per-person breakdown** is offered or computable. Drill-down lists issues with their summed logged time (not who logged it).

## M6 — Time-to-close statistics (FR-022–024)

For the set of close events in a selection, `time_to_close = closed_at − created_at`. The contract returns **all of**:
- `mean`
- `stddev_sample` (n−1)
- `cv = stddev_sample / mean`
- `basis = "sample"`
- `n`, and `low_sample = (n < low_n_threshold)`

A caller MUST NOT render the mean without the stddev, CV, and basis. No distorting smoothing/rounding (FR-025).

## M7 — Region (FR-026/027)

- Time-of-day region (M2 grouping) uses `region_from_timestamp(created_at, region_windows_utc)`.
- Per-user region uses `region_from_user_map(assignee_account_id)`.
The two are computed by separate functions and labeled distinctly in the UI; they are never substituted for one another. Default reference tz = EMEA.

---

## Cross-cutting invariants (tested)

- **I-1 (subset)**: for every metric/period, `ps5_blocker` value ≤ `all` value (SC-005).
- **I-2 (traceability)**: every rendered aggregate maps to a drill-down whose row count equals the aggregate (FR-019).
- **I-3 (period disjointness)**: `created-in-N` and `closed-in-N` drill-downs are computed from different columns and can return different sets for the same issue (SC-003 fixture: created wk2 / Highest-raised wk5 / closed wk6).
- **I-4 (point-in-time)**: no metric reads `issues.current_priority` for a historical period; all use intervals (Art. VII).
- **I-5 (freshness)**: every view exposes `sync_state.last_sync_at` (SC-008).
