# Phase 1 Data Model: ISReq Analytics Dashboard

All objects live in the **`isreq`** PostgreSQL schema, owned by `isreq_app`, created additively (Alembic, `version_table_schema="isreq"`). No weekly/pulse snapshot tables — period aggregates and backlog are computed at query time (FR-016). Two **derived** tables (`priority_intervals`, `status_intervals`) are rebuilt from the changelog to make point-in-time queries indexable.

Legend: PK = primary key, FK = foreign key, **derived** = recomputed from source rows during sync.

---

## Source tables (synced from Jira)

### `issues`
The current snapshot of each ticket.

| Column | Type | Notes |
|---|---|---|
| `key` | text PK | `ISREQ-NNN`; matches `^ISREQ-\d+$` (Jira key is uppercase, resolved 2026-06-12) |
| `jira_id` | bigint | Jira numeric id (stable across renames) |
| `title` | text | |
| `created_at` | timestamptz | creation time (source of truth for intake & week of creation) |
| `resolved_at` | timestamptz null | current resolution time; **not** used alone for throughput (see `status_intervals`) |
| `current_status` | text | latest status |
| `current_priority` | text | latest priority — **display only**; never used for historical priority questions (Art. VII) |
| `assignee_account_id` | text null | |
| `assignee_name` | text null | display name; shown in drill-downs (FR-020) |
| `area` | text null | from configured custom field; `Unknown` if absent |
| `sub_area` | text null | from configured custom field; `Unknown` if absent |
| `pulse` | text null | latest pulse from the sprint field (multi-sprint ⇒ latest, FR-012) |
| `is_pr_mp` | boolean | derived: title contains the configured PR/MP substring (FR-028) |
| `labels` | jsonb | raw label array (denormalized convenience) |
| `jira_updated_at` | timestamptz | drives incremental sync watermark comparison |
| `synced_at` | timestamptz | last time this row was upserted |

Indexes: `(created_at)`, `(current_status)`, `(area, sub_area)`, `(is_pr_mp)`.

### `issue_labels`
Normalized labels for scope filtering (`ps5-blocker`).

| Column | Type | Notes |
|---|---|---|
| `issue_key` | text FK→issues.key | |
| `label` | text | |

PK `(issue_key, label)`. Index `(label)` for the `ps5-blocker` scope filter.

### `changelog`
Every recorded field transition (priority and status are the ones we consume; others stored for traceability).

| Column | Type | Notes |
|---|---|---|
| `id` | bigint PK | Jira changelog history id (stable upsert key, idempotency) |
| `issue_key` | text FK→issues.key | |
| `field` | text | e.g. `priority`, `status` |
| `from_value` | text null | |
| `to_value` | text null | |
| `changed_at` | timestamptz | |
| `author_account_id` | text null | traceability only |
| `author_name` | text null | |

Indexes: `(issue_key, field, changed_at)`.

### `worklogs`
Time entries; **bucketed by `started_at`** (Art. VI). Author intentionally **not** stored for attribution.

| Column | Type | Notes |
|---|---|---|
| `id` | bigint PK | Jira worklog id (idempotent upsert key) |
| `issue_key` | text FK→issues.key | |
| `time_spent_seconds` | integer | |
| `started_at` | timestamptz | the bucketing timestamp |
| `synced_at` | timestamptz | |

Index `(started_at)`, `(issue_key)`. (No `author` column — per-person attribution is forbidden, FR-018.)

### `users`
Static user→region map (loaded from CSV, not from Jira).

| Column | Type | Notes |
|---|---|---|
| `account_id` | text PK | |
| `display_name` | text null | |
| `region` | text | one of `AMER`,`EMEA`,`APAC`,`Unknown` |

### `sync_state`
Single-row (or per-resource) watermark for incremental, idempotent sync.

| Column | Type | Notes |
|---|---|---|
| `resource` | text PK | e.g. `issues`, `worklogs` |
| `last_sync_at` | timestamptz null | watermark; only advanced on success (R-010) |
| `last_full_sync_at` | timestamptz null | |
| `note` | text null | |

---

## Derived tables (rebuilt from `changelog` during sync)

### `priority_intervals` (derived; Art. VII, R-004)
Non-overlapping, contiguous spans of a single priority per issue.

| Column | Type | Notes |
|---|---|---|
| `id` | bigserial PK | |
| `issue_key` | text FK→issues.key | |
| `priority` | text | |
| `valid_from` | timestamptz | first interval starts at `issues.created_at` with the creation priority |
| `valid_to` | timestamptz null | `null` = still in effect |

Build rule: seed with `(created_at, creation_priority)`; for each `changelog` row where `field='priority'` ordered by `changed_at`, close the open interval at `changed_at` and open a new one with `to_value`. Indexes: `(issue_key, valid_from)`, `(priority, valid_from, valid_to)` for "was `Highest` during W".

Validation: intervals per issue are contiguous (no gaps/overlaps); exactly one open interval (`valid_to is null`) per issue.

### `status_intervals` (derived; R-005)
Same shape as `priority_intervals`, for status. Used to derive **close events** (entry into the configured closed-status set) and **open/closed state at T** for backlog with reopen support.

| Column | Type | Notes |
|---|---|---|
| `id` | bigserial PK | |
| `issue_key` | text FK→issues.key | |
| `status` | text | |
| `valid_from` | timestamptz | |
| `valid_to` | timestamptz null | |

Derived **close events** = each interval whose `status` ∈ closed-status set, keyed by its `valid_from` (the moment of closing). A reopened-then-reclosed issue yields ≥2 close events ⇒ counted at each close (FR-015).

---

## Derivation logic (computed, not stored)

| Concept | Definition | Spec ref |
|---|---|---|
| **Week of t** | `floor((t − anchor_date)/7d) + 1`; `≤0` ⇒ pre-inception bucket | FR-011 |
| **Pulse of issue** | `issues.pulse` (latest sprint when multiple) | FR-012 |
| **Scope filter** | `all` = every issue; `ps5-blocker` = issues with that label | FR-013 |
| **Intake(period)** | count of issues with `created_at` in period, grouped by area/sub-area/region | FR-014 |
| **Throughput(period)** | count of **close events** with `closed_at` in period | FR-015 |
| **Backlog(T)** | issues `created_at ≤ T` AND not in closed status as of T (via `status_intervals`) | FR-016 |
| **Time invested(period)** | Σ `time_spent_seconds` for worklogs with `started_at` in period, grouped by issue/area | FR-017 |
| **Became-Highest(period)** | count of `priority_intervals` entries into `Highest` (incl. creation-at-Highest) with `valid_from` in period — each entry counted | FR-007 |
| **Highest-exit(period)** | count of exits from `Highest` (drop below, or close while Highest) in period | FR-009 |
| **Highest backlog(T)** | issues whose interval covering T has `priority=Highest` and are open at T = cumulative entries − exits | FR-009 |
| **Time-to-close** | per close event: `closed_at − created_at`; reported as mean + sample stddev + CV + low-n flag | FR-022–024 |
| **Region (time-of-day)** | `region_from_timestamp(created_at, windows)` | FR-026a |
| **Region (per-user)** | `region_from_user_map(assignee_account_id)` | FR-026b |

## Drill-down contract (FR-019–021)

Every aggregate above has a paired query returning the underlying issues with **`key`, `title`, `assignee_name`**. The two period predicates are physically distinct and never conflated:
- **created-in-period N** → `WHERE week(created_at) = N` (or pulse = N).
- **closed-in-period N** → close events `WHERE week(closed_at) = N`, joined back to issues — independent of `created_at`.

## Entity relationships

```text
issues 1──* issue_labels
issues 1──* changelog ──derives──> priority_intervals (1──*)
                       ──derives──> status_intervals  (1──*) ──> close events
issues 1──* worklogs           (bucketed by started_at; no author)
issues *──1 users (via assignee_account_id; per-user region only)
sync_state (standalone watermark)
```
