---
description: "Task list for ISReq Analytics Dashboard implementation"
---

# Tasks: ISReq Analytics Dashboard

**Input**: Design documents from `/specs/001-isreq-analytics-dashboard/`

**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅ (config, metrics, sync), quickstart.md ✅, constitution.md ✅

**Tests**: INCLUDED. The plan's Testing strategy (unit / contract / integration) and the explicit "(tested)" guarantees in `contracts/sync.md` and `contracts/metrics.md`, together with the constitution's honesty mandate (Art. II/III), make tests part of the deliverable for this feature.

**Organization**: Tasks are grouped by user story (US1–US7, in spec priority order) so each story can be implemented and tested independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: Which user story this task serves (US1…US7); Setup / Foundational / Polish carry no story label
- Every task names an exact file path.

## Path Conventions (from plan.md — single Python project)

- Package root: `src/isreq_dashboard/`
- Tests: `tests/{unit,integration,contract,fixtures}/`
- Migrations: `migrations/` · Deploy units: `deploy/` · Config: `config/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project skeleton, dependencies, and shareable (non-secret) config examples.

- [X] T001 Create the full source tree per plan.md "Project Structure": `src/isreq_dashboard/{config.py,db/,jira/,domain/,metrics/,app/pages/,app/components/,cli/}`, plus `migrations/`, `deploy/`, `config/`, and `tests/{unit,integration,contract,fixtures}/`, each package with an `__init__.py`.
- [X] T002 Initialize the Python 3.12 project in `pyproject.toml` with runtime deps (streamlit, sqlalchemy>=2, psycopg[binary], alembic, httpx, tenacity, pandas, pydantic-settings) and dev deps (pytest, testcontainers); declare console entrypoints for the CLIs in `pyproject.toml`.
- [X] T003 [P] Configure ruff + formatting and the pytest config (markers `unit`/`contract`/`integration`, `tests/` paths) in `pyproject.toml`.
- [X] T004 [P] Create `config/config.example.toml` and `config/users-region.example.csv` populated with every key/shape from `contracts/config.md` (project_key, field ids, highest/label/PR-MP casing, closed_statuses, anchor_date, region_windows_utc, low_n_threshold, reference_timezone, pr_mp_default_visibility).
- [X] T005 [P] Create `.gitignore` ignoring `.env`, secret-bearing config (`config/config.toml`, `config/users-region.csv`), and the agent folder (Art. XI).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Config, DB isolation, the read-only Jira client, the idempotent sync pipeline, the shared domain builders (weeks, priority/status intervals, regions), and the Streamlit shell. **Every user story depends on synced data, so the sync pipeline lives here, not in a story.**

**⚠️ CRITICAL**: No user-story work can begin until this phase is complete.

### Configuration

- [X] T006 [P] Implement the pydantic-settings loader in `src/isreq_dashboard/config.py`: secrets from env (`ISREQ_JIRA_*`, `ISREQ_DB_*`), non-secret TOML, and users-CSV path; hard startup failure on any missing/malformed required key; reject a superuser DB role (FR-029/030, Art. VIII/XI).
- [X] T007 [P] Contract test the config schema in `tests/contract/test_config.py`: missing/malformed key fails loudly, region windows cover 24h with no gap, CSV regions in `{AMER,EMEA,APAC}`, and no token/password substring appears in log output.

### Database (isolation & non-destruction — Art. VIII)

- [X] T008 Implement the SQLAlchemy 2 engine in `src/isreq_dashboard/db/engine.py` with `MetaData(schema="isreq")` and `connect_args={"options": "-csearch_path=isreq"}`, connecting as `isreq_app` (depends on T006).
- [X] T009 Define all ORM models in `src/isreq_dashboard/db/models.py` — `issues`, `issue_labels`, `changelog`, `worklogs` (no author column), `users`, `sync_state`, `priority_intervals`, `status_intervals` — with the columns and indexes in `data-model.md` (depends on T008).
- [X] T010 Implement the session factory in `src/isreq_dashboard/db/session.py` (depends on T008).
- [X] T011 Initialize Alembic in `migrations/` scoped to `isreq` (`version_table_schema="isreq"`, `include_schemas=True`) and author the initial **additive** migration creating all tables/indexes from T009 (depends on T009).
- [X] T012 Implement the additive schema CLI in `src/isreq_dashboard/cli/init_schema.py` running `alembic upgrade head` (idempotent, never drops) (depends on T011).
- [X] T013 [P] Implement the destructive CLI in `src/isreq_dashboard/cli/admin_reset.py` (DROP/TRUNCATE/reset only here, human-invoked, never on boot/timer — Art. VIII) (depends on T009).

### Read-only Jira client & ingestion (Art. IX/X)

- [X] T014 [P] Implement the read-only Jira REST v3 client in `src/isreq_dashboard/jira/client.py`: GET-only methods, basic auth (email + token), full pagination, tenacity retry/backoff; no create/edit/transition/comment/delete paths exist (FR-001, Art. IX).
- [X] T015 [P] Add the one-shot field-discovery helper (lists `/rest/api/3/field`) in `src/isreq_dashboard/jira/client.py` so the operator can resolve area/sub-area/pulse `customfield_*` ids (R-012).
- [X] T016 Implement raw→row mapping in `src/isreq_dashboard/jira/mapping.py`: issue/changelog/worklog dicts, configurable field-id resolution, `is_pr_mp` from title substring, and latest-pulse selection from a multi-sprint field (FR-012/028) (depends on T006).
- [X] T017 Implement complete worklog fetch in `src/isreq_dashboard/jira/worklog.py`: never trust the inline ≤20; complete per issue via `/issue/{key}/worklog` and incrementally via `worklog/updated`→`worklog/list`; bucket by `started`, store no author (FR-004, R-003) (depends on T014).

### Shared domain builders (used by sync derive-step and by metrics)

- [X] T018 [P] Implement week numbering in `src/isreq_dashboard/domain/weeks.py`: `week(t)=floor((t−anchor)/7d)+1`, with a labeled pre-inception bucket for `week ≤ 0` (FR-011, R-006).
- [X] T019 [P] Implement the priority-interval builder in `src/isreq_dashboard/domain/priority.py`: seed `(created_at, creation_priority)`, then close/open intervals per ordered `priority` changelog rows; contiguous, exactly one open interval (Art. VII, R-004).
- [X] T020 [P] Implement the status-interval builder + close-event derivation in `src/isreq_dashboard/domain/status.py`: intervals from status changelog; a close event = each entry into the configured closed-status set (reopen ⇒ ≥2 events) (FR-015/016, R-005).
- [X] T021 [P] Implement the two non-substitutable region functions in `src/isreq_dashboard/domain/regions.py`: `region_from_timestamp(created_at, windows)` (EMEA-anchored) and `region_from_user_map(account_id)` (Unmapped→`Unknown`); EMEA default reference tz (Art. V, R-007). *(Shared: consumed by US3 intake grouping and US7.)*

### Sync pipeline (idempotent, incremental, sync-then-read)

- [X] T022 Implement the incremental, idempotent sync in `src/isreq_dashboard/jira/sync.py`: watermark JQL `project=<key> [AND updated >= last_sync]` with `changelog` expansion + truncation completion, complete worklogs (T017), rebuild `priority_intervals`/`status_intervals` (T019/T020) per touched issue, upsert on stable keys, advance `sync_state.last_sync_at` only on full success (FR-002/003, R-002/010) (depends on T009, T016, T017, T019, T020).
- [X] T023 Implement the timer entrypoint in `src/isreq_dashboard/cli/sync_main.py` (incremental default, explicit `full` flag; structured logs with counts/duration, no secrets) (depends on T022).

### Foundational tests & app shell

- [X] T024 [P] Create recorded Jira fixtures in `tests/fixtures/` (issues+changelog+worklog), including the SC-003 issue (created Medium wk2 → raised to Highest wk5 → closed wk6), a >20-worklog issue, a closed→reopened→reclosed issue, and a pre-inception timestamp.
- [X] T025 [P] Unit-test the domain builders in `tests/unit/test_domain.py` (weeks incl. pre-inception, priority intervals contiguity, status intervals + close events on reopen, both region derivations) (depends on T018–T021).
- [X] T026 Contract-test the sync in `tests/contract/test_sync.py`: read-only (no non-GET method exists), idempotent (twice ⇒ identical counts, SC-009), incremental watermark, >20-worklog completeness, and schema isolation (all writes `isreq.*`, connects as `isreq_app`) (depends on T022, T024).
- [X] T027 Integration-test the pipeline in `tests/integration/test_sync_pipeline.py`: fixtures → sync → Postgres rows + rebuilt intervals, watermark advanced once (depends on T022, T024).
- [X] T028 Build the Streamlit shell in `src/isreq_dashboard/app/Home.py` (entrypoint scaffold) and a freshness banner in `src/isreq_dashboard/app/components/controls.py` reading `sync_state.last_sync_at` on every view (FR-005, SC-008, I-5) (depends on T010).
- [X] T060 Load/refresh the user→region map from `ISREQ_USERS_CSV` into `isreq.users` (sync.md behavior #6) in `src/isreq_dashboard/jira/sync.py` (invoked by `cli/sync_main.py`); validate regions ∈ `{AMER,EMEA,APAC}`, unmapped/absent → `Unknown`, never guessed. **Populates the table `region_from_user_map` (T021) and US7 per-user counts read** (FR-026b) *(added via /speckit-analyze remediation — execute within Foundational, before US7)* (depends on T009, T006).

**Checkpoint**: Schema is additive and isolated, a real/fixture sync populates `isreq` (issues, changelog, worklogs, intervals, **and the `users` map**), and the app shell renders with a freshness banner. User stories can now begin.

---

## Phase 3: User Story 1 - Is Highest intake outpacing Highest closure? (Priority: P1) 🎯 MVP

**Goal**: The default landing view compares the rate tickets *become Highest* (created-at-Highest **or** raised-to-Highest) vs the rate Highest tickets close, and makes the cumulative Highest backlog legible.

**Independent Test**: Load the dashboard on the synced fixture; the Highest create-vs-close chart + cumulative backlog is the landing view with no config; the SC-003 issue counts in wk5 `became_highest` (not wk2); the backlog line equals cumulative entries − exits (V1, V2).

- [X] T029 [P] [US1] Contract-test M1 in `tests/contract/test_metrics_highest.py`: `became_highest` counts each entry event (incl. creation-at-Highest), the SC-003 issue lands in wk5 only, `highest_backlog(t)=Σbecame−Σexits`= live Highest-and-open count, and no query reads `current_priority` (I-4) (depends on T024).
- [X] T030 [US1] Implement M1 in `src/isreq_dashboard/metrics/highest.py`: `became_highest`, `highest_closed`, `highest_exits`, `highest_backlog`, all from `priority_intervals`/`status_intervals`, accepting `(cadence, scope, period)` selectors (weekly/all wired now) (depends on T019, T020, T009).
- [X] T031 [US1] Render the north-star view as the default landing in `src/isreq_dashboard/app/Home.py`: plot `became_highest` vs `highest_closed` per period and the cumulative `highest_backlog`, legible with no configuration (FR-006/009, Art. I) (depends on T030, T028).

**Checkpoint**: US1 is fully functional and independently testable — the MVP delivers the north-star decision support.

---

## Phase 4: User Story 2 - Every number is traceable to its tickets (Priority: P1)

**Goal**: Any aggregate drills to its underlying tickets (key, title, assignee), with "created in N" and "closed in N" computed from physically distinct predicates.

**Independent Test**: Click an aggregate on the north-star view → underlying tickets list with key/title/assignee; for the SC-003 issue, "created in wk2" and "closed in wk6" each contain it while "created in wk6"/"closed in wk2" do not (V3).

- [X] T032 [P] [US2] Contract-test drill-down in `tests/contract/test_drilldown.py`: `created-in-N` vs `closed-in-N` use different columns and can diverge for one issue (I-3), drill row count equals its aggregate (I-2), and every row carries `key`,`title`,`assignee_name` (FR-020/021) (depends on T024).
- [X] T033 [US2] Implement the paired drill-down queries in `src/isreq_dashboard/metrics/drilldown.py`: `created_in_period`, `closed_in_period` (from close events), `became_highest_in_period`, `open_at(t)` — each returning `key,title,assignee_name`, never conflating created vs closed (depends on T019, T020, T009).
- [X] T034 [US2] Implement the ticket-table component in `src/isreq_dashboard/app/components/drilldown.py` (key/title/assignee) (depends on T033).
- [X] T035 [US2] Wire dataframe selection events on the north-star view to the drill-down table in `src/isreq_dashboard/app/Home.py` (depends on T034, T031).

**Checkpoint**: US1 + US2 work together — every north-star figure is now openable to its tickets.

---

## Phase 5: User Story 3 - Intake, throughput, backlog by area, sub-area, region (Priority: P2)

**Goal**: Intake (created/period), throughput (close events/period), and backlog (open at T) — each split by area, sub-area, and creation-time-of-day region, each drillable.

**Independent Test**: For a cadence, intake = count created/period, throughput = count of close events/period (reopened ticket counts twice), backlog = open at each point (reopened ⇒ open again); each splittable by area/sub-area/region and drillable per US2.

- [X] T036 [P] [US3] Contract-test M2/M3/M4 in `tests/contract/test_metrics_queue.py`: intake by area/sub_area/region, throughput counts each close (reopen→reclose=2), backlog open-at-T with reopen, and PR/MP tickets included in totals (FR-014/015/016/028) (depends on T024).
- [X] T037 [P] [US3] Implement intake (M2) in `src/isreq_dashboard/metrics/intake.py`: count `created_at` in period grouped by area/sub_area/`region_from_timestamp` (depends on T021, T009).
- [X] T038 [P] [US3] Implement throughput (M3) in `src/isreq_dashboard/metrics/throughput.py`: count close events with `closed_at` in period (each close) (depends on T020, T009).
- [X] T039 [P] [US3] Implement backlog (M4) in `src/isreq_dashboard/metrics/backlog.py`: issues `created_at ≤ t` and not closed as of `t` via `status_intervals` (reopen-aware) (depends on T020, T009).
- [X] T040 [US3] Implement the Intake page in `src/isreq_dashboard/app/pages/1_Intake.py` (area/sub_area/region grouping + drill-down) (depends on T037, T034).
- [X] T041 [US3] Implement the Throughput page in `src/isreq_dashboard/app/pages/2_Throughput.py` (+ drill-down) (depends on T038, T034).
- [X] T042 [US3] Implement the Backlog page in `src/isreq_dashboard/app/pages/3_Backlog.py` (+ drill-down) (depends on T039, T034).

**Checkpoint**: US1–US3 independently functional — the Highest story now has full queue-health context.

---

## Phase 6: User Story 4 - Two cadences and two scopes, everywhere (Priority: P2)

**Goal**: Every view toggles cadence (weekly ↔ per-pulse) and scope (all ↔ `ps5-blocker`) with identical metric definitions and `ps5-blocker ≤ all` everywhere.

**Independent Test**: On each view switch weekly↔per-pulse and all↔`ps5-blocker`; numbers recompute consistently and `ps5-blocker` is a strict subset of `all` for the same period/metric (V5).

- [X] T043 [P] [US4] Contract-test cadence/scope in `tests/contract/test_cadence_scope.py`: per-pulse vs weekly consistency, identical definitions across scopes, and the subset invariant `ps5_blocker ≤ all` (I-1, SC-005) (depends on T024).
- [X] T044 [US4] Implement the cadence + scope + period-range controls in `src/isreq_dashboard/app/components/controls.py` (weekly/per-pulse toggle, all/`ps5-blocker` toggle, period selector) **and the PR/MP-review filter** (include/exclude via `is_pr_mp`, defaulting to `pr_mp_default_visibility`, plus a distinct PR/MP category view — FR-028) (depends on T028).
- [X] T045 [US4] Thread `(cadence, scope, pr_mp_filter)` through every metric — `highest.py`, `intake.py`, `throughput.py`, `backlog.py` (per-pulse keying via `issues.pulse`, scope filter via `issue_labels`, PR/MP filter via `issues.is_pr_mp`) — and wire the controls into `app/Home.py` and all `app/pages/*` (depends on T044, T030, T037, T038, T039).

**Checkpoint**: All existing views are now cadence- and scope-complete.

---

## Phase 7: User Story 5 - Honest time-to-close statistics (Priority: P3)

**Goal**: Time-to-close never shows a lone mean — always mean + sample stddev + CV + sample/population basis, with low-n buckets flagged.

**Independent Test**: Any time-to-close figure shows mean, stddev, CV, and a "sample" label together; a few-close bucket is flagged low-sample (V4).

- [X] T046 [P] [US5] Unit-test stats in `tests/unit/test_stats.py`: mean, sample stddev (n−1), `CV=s/mean`, basis label, and `low_sample=(n<low_n_threshold)` (FR-022–024) (no dependency beyond T002).
- [X] T047 [US5] Implement `domain/stats.py`: return mean + sample stddev + CV + `basis="sample"` + `n` + `low_sample`; no distorting smoothing/rounding (FR-025) (depends on T046).
- [X] T048 [US5] Compute M6 time-to-close per close-event selection and render mean+stddev+CV+basis+low-n flag wherever a time-to-close average appears (Throughput page + any close-time summary), never the mean alone; **make the statistic drillable to its underlying close events** (FR-019, I-2) via the US2 drill-down component (`app/pages/2_Throughput.py`) (depends on T047, T038, T034).

**Checkpoint**: Every average on the dashboard is now statistically honest.

---

## Phase 8: User Story 6 - Best-effort time invested by area (Priority: P3)

**Goal**: Worklog seconds summed per period, bucketed by worklog `started`, attributed at issue/area only, plainly labeled best-effort, with no per-person breakdown.

**Independent Test**: A worklog started wk8 on an issue created wk2 contributes to wk8; the view is labeled best-effort and offers no per-person view (V6).

- [X] T049 [P] [US6] Contract-test M5 in `tests/contract/test_time_invested.py`: bucket by `started_at` (wk8 worklog on a wk2 issue → wk8), grouped by issue/area, and no per-person attribution is computable (FR-017/018) (depends on T024).
- [X] T050 [US6] Implement M5 in `src/isreq_dashboard/metrics/time_invested.py`: Σ `time_spent_seconds` for worklogs with `started_at` in period, grouped by issue/area only (depends on T009, T018).
- [X] T051 [US6] Implement the Time-Invested page in `src/isreq_dashboard/app/pages/4_Time_Invested.py` with the best-effort caveat, drill-down to issues + summed time, and no per-person breakdown (depends on T050, T034).

**Checkpoint**: Time-invested context is available and correctly caveated.

---

## Phase 9: User Story 7 - Region two ways, never conflated (Priority: P3)

**Goal**: A region view presenting both derivations distinctly — creation-time-of-day (`region_from_timestamp`) and per-user (`region_from_user_map`) — never substituted, EMEA default.

**Independent Test**: Creation-time-of-day region uses the configured windows on `created_at`; per-user region uses the user→region map; both appear as clearly distinct derivations (US7 acceptance).

- [X] T052 [P] [US7] Contract-test region distinctness in `tests/contract/test_region.py`: time-of-day grouping uses `region_from_timestamp(created_at)` while per-user uses `region_from_user_map(assignee_account_id)`, and the two are never interchanged (FR-026/027) (depends on T021, T024).
- [X] T053 [US7] Implement the Region page in `src/isreq_dashboard/app/pages/5_Region.py`: a creation-time-of-day breakdown and a per-user breakdown, each labeled with its derivation, EMEA as the default reference tz, drillable per US2 (depends on T021, T034).

**Checkpoint**: All seven user stories are independently functional.

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Deployment, end-to-end validation, and the remaining cross-cutting guarantees.

- [X] T054 [P] Create the systemd units in `deploy/`: `isreq-sync.service` + `isreq-sync.timer` (runs `cli.sync_main`) and `isreq-dashboard.service` (runs Streamlit) (R-010).
- [X] T055 [P] Implement the cross-cutting invariant suite in `tests/contract/test_invariants.py` (I-1 subset, I-2 traceability=count, I-3 created/closed disjointness, I-4 no current_priority, I-5 freshness exposed).
- [X] T056 [P] Ensure consistent `Unknown/Unassigned` bucketing for missing area/sub-area/pulse/assignee across `metrics/*` (edge case: never drop from totals).
- [X] T057 [P] Assert secret-safety across sync and app: no token/password substring in any log output (Art. XI) in `tests/contract/test_secret_safety.py`.
- [X] T058 [P] Write operator docs / README covering setup, init_schema, sync, and dashboard launch (mirrors quickstart.md).
- [X] T059 Run the quickstart V1–V10 validation against the seeded fixture, then against the live ISReq project after the first full sync; record pass/fail.
- [X] T061 [P] Add a structural guard test in `tests/contract/test_render_isolation.py` asserting the `app/` package never imports or calls `jira/` (no per-render source dependency), giving SC-007 an automated check beyond the manual V7 reload *(added via /speckit-analyze remediation)*.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies — start immediately.
- **Foundational (Phase 2)**: depends on Setup — **BLOCKS all user stories** (sync + DB + domain builders + app shell live here).
- **User Stories (Phase 3–9)**: all depend on Foundational. In priority order P1→P3; can run in parallel once Foundational is done, with the noted cross-story integrations.
- **Polish (Phase 10)**: depends on the desired user stories being complete.

### User Story Dependencies

- **US1 (P1)** — depends only on Foundational. The MVP.
- **US2 (P1)** — depends on Foundational; integrates with US1 (wires drill-down into the north-star view). The `drilldown.py` queries are independently testable without US1.
- **US3 (P2)** — depends on Foundational; reuses the US2 drill-down component for its pages.
- **US4 (P2)** — depends on Foundational; threads cadence/scope through whatever metrics exist (US1 + US3). Independently testable on the metric layer.
- **US5 (P3)** — depends on Foundational; renders alongside throughput (US3) but `stats.py` is independently unit-testable.
- **US6 (P3)** — depends on Foundational only; fully independent (own metric + page).
- **US7 (P3)** — depends on Foundational (`regions.py`); reuses the US2 drill-down component.

### Within Each User Story

- Tests are written first and must fail before implementation.
- Models/builders (Foundational) → metrics → pages.
- Drill-down component (US2) is reused by US3/US6/US7 pages.

### Parallel Opportunities

- Setup: T003, T004, T005 in parallel.
- Foundational: after config (T006/T007), the DB chain (T008→T009→T010/T011/T012) is largely sequential; **T013, T014, T015, T018, T019, T020, T021, T024 are [P]** (distinct files). Tests T025/T026/T027 follow their targets.
- Once Foundational completes, **US1, US6, and US7's metric/test layers can start in parallel**; US2 unblocks US3/US6/US7 page wiring.
- Within US3: **T037, T038, T039 (intake/throughput/backlog metrics) run in parallel**; their pages T040–T042 follow.
- All `[P]` contract/unit tests across stories can be authored in parallel.

---

## Parallel Example: User Story 3

```bash
# After Foundational + US2 drill-down component:
# Launch the three queue-metric implementations together (different files):
Task: "Implement intake (M2) in src/isreq_dashboard/metrics/intake.py"
Task: "Implement throughput (M3) in src/isreq_dashboard/metrics/throughput.py"
Task: "Implement backlog (M4) in src/isreq_dashboard/metrics/backlog.py"

# Then the three pages (each reuses the US2 drill-down component):
Task: "Implement app/pages/1_Intake.py"
Task: "Implement app/pages/2_Throughput.py"
Task: "Implement app/pages/3_Backlog.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 + the traceability it relies on)

1. Complete Phase 1 (Setup) and Phase 2 (Foundational — sync populates `isreq`, intervals built, app shell + freshness).
2. Complete Phase 3 (US1) → the north-star create-vs-close + cumulative Highest backlog as the landing view.
3. Complete Phase 4 (US2) → every north-star figure is drillable (the constitution treats an un-openable aggregate as a rumor, so US2 ships with the MVP).
4. **STOP and VALIDATE**: run V1–V3 against the fixture. Deploy/demo.

### Incremental Delivery

1. Setup + Foundational → foundation ready.
2. US1 + US2 → MVP (north star + traceability).
3. US3 → queue health (intake/throughput/backlog).
4. US4 → cadence/scope everywhere.
5. US5, US6, US7 → honest stats, time invested, region — each independent.
6. Polish → systemd, invariants, quickstart validation.

### Parallel Team Strategy

After Foundational: Dev A on US1→US2, Dev B on US3 (metrics in parallel), Dev C on US6/US7. US4 lands once US1/US3 metrics exist; US5 once throughput exists.

---

## Notes

- `[P]` = different files, no dependency on an incomplete task.
- `[Story]` maps a task to its user story for traceability.
- Every user story is independently completable and testable.
- Tests fail before implementation; commit after each task or logical group.
- Stop at any checkpoint to validate a story independently.
- Hard rules carried in every task: read-only on Jira (Art. IX), no API at render (Art. X), writes confined to `isreq` and additive-only (Art. VIII), secrets from env only (Art. XI), point-in-time priority from intervals (Art. VII).
- **T060–T061** were appended via `/speckit-analyze` remediation; their IDs are out of positional order but they execute within their labeled phase (T060 in Foundational — before US7; T061 in Polish). All pre-existing task IDs and their dependency references are unchanged.
