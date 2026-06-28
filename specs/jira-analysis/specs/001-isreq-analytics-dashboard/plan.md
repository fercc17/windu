# Implementation Plan: ISReq Analytics Dashboard

**Branch**: `001-isreq-analytics-dashboard` | **Date**: 2026-06-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-isreq-analytics-dashboard/spec.md`

## Summary

Answer one question with evidence: are Highest-priority ISReq tickets being created faster than the team can close them? A scheduled **sync job** (systemd timer) pulls the ISReq project from the Jira Cloud REST API — issues, full changelog, and complete worklog — incrementally and idempotently into a dedicated `isreq` PostgreSQL schema. A **Streamlit dashboard** reads only from PostgreSQL (never the Jira API at render time) and presents the north-star Highest create-vs-close comparison plus intake, throughput, backlog, and best-effort time-invested views, each on two cadences (weekly, per-pulse), two scopes (all ISReq, `ps5-blocker`), with every aggregate drillable to its underlying tickets. Point-in-time priority correctness is achieved by reconstructing **priority intervals** from the changelog, so "was this Highest during week W" and "raised to Highest after creation" are computed from history, not current priority.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**:
- UI: **Streamlit** (chosen default — see research.md R-001); drill-down via dataframe selection events + query params.
- Data/ORM: **SQLAlchemy 2.x** + **psycopg** (v3) driver; **Alembic** for additive, `isreq`-scoped migrations.
- Aggregation: **pandas** (frames feed Streamlit selection events; heavy aggregation pushed to SQL).
- Jira client: **httpx** (sync client) with **tenacity** for retry/backoff; read-only (GET only).
- Config: **pydantic-settings** (env + TOML file); secrets from env only.
- Tests: **pytest** + **testcontainers**/local Postgres for integration.

**Storage**: PostgreSQL (shared instance), dedicated schema `isreq`, connected as non-superuser role `isreq_app` with `search_path=isreq`. No weekly snapshot tables — backlog is computed at query time (per FR-016).

**Testing**: pytest — unit (domain logic: weeks, priority intervals, regions, stats), contract (metric definitions, config schema, sync idempotency), integration (sync against a recorded Jira fixture → Postgres → metric queries).

**Target Platform**: Linux server in an LXD container on a private homelab. Two long-lived/triggered processes: the Streamlit app (service) and the sync job (systemd timer).

**Project Type**: Single Python project with two entrypoints — a Streamlit web UI and a CLI sync job (plus explicit, human-only schema/admin commands).

**Performance Goals**: Dashboard interactions resolve from local Postgres in < ~1 s for typical period ranges; the dashboard renders with the Jira API unreachable (sync-then-read, SC-007). Incremental sync completes in a few minutes on a normal delta.

**Constraints**: Read-only on Jira/Tempo (Art. IX); no source API calls during render (Art. X); all writes confined to `isreq` and additive-only at startup (Art. VIII); secrets from env, egress limited to the Atlassian/Jira API, no third-party data sharing (Art. XI).

**Scale/Scope**: One intake project (`ISREQ`) — **~2.9k issues today** (361 Highest, 99 ps5-blocker, ~932 `[PR/MP Review]`), growing; envelope ≤ ~20k issues over time, with proportionate changelog/worklog rows; ≤ ~5 concurrent internal viewers. 7 prioritized user stories; ~5 analytical views × 2 cadences × 2 scopes. (Dataset confirmed via read-only Jira discovery — see `jira-discovery.md`.)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Article | How this plan satisfies it | Status |
|---|---|---|
| **I. Highest is north star** | Highest create-vs-close is the default landing view (`app/Home.py` + `metrics/highest.py`); cumulative Highest backlog made legible; raised-to-Highest included. | ✅ PASS |
| **II. Every number traceable** | Each aggregate query has a paired drill-down query returning key/title/assignee; `created-in-period` and `closed-in-period` are distinct SQL predicates (FR-021). | ✅ PASS |
| **III. Statistical honesty** | `domain/stats.py` always returns mean + sample stddev (n−1) + CV with a sample/population label and a low-n flag; no smoothing/rounding that distorts (FR-022–025). | ✅ PASS |
| **IV. Two cadences, two scopes** | `domain/weeks.py` (anchor-relative weeks) + pulse-from-sprint; a scope filter (`all` vs `ps5-blocker`) with identical metric definitions across both (FR-010–013). | ✅ PASS |
| **V. Region two derivations** | `domain/regions.py` exposes two non-substitutable functions — `region_from_timestamp(windows)` and `region_from_user_map(account_id)`; EMEA is the default reference tz (FR-026/027). | ✅ PASS |
| **VI. Time invested best-effort** | `metrics/time_invested.py` attributes at issue/area level only, buckets by worklog `started`, labels best-effort; `worklogs` table stores no author for attribution (FR-017/018). | ✅ PASS |
| **VII. Point-in-time correctness** | `priority_intervals` reconstructed from creation priority + ordered changelog; all priority-state queries are interval lookups, never current priority (FR-007/008). | ✅ PASS |
| **VIII. DB isolation & non-destruction** | Role `isreq_app`; `MetaData(schema="isreq")`; `connect_args={"options":"-csearch_path=isreq"}`; Alembic `version_table_schema="isreq"`, `include_schemas=True`; startup is additive (`CREATE … IF NOT EXISTS` / `alembic upgrade`), never drop-all; `DROP`/`TRUNCATE`/reset live only in a separate human-invoked CLI that never runs on boot or on the timer. | ✅ PASS |
| **IX. Read-only sources** | Jira client exposes GET-only methods; no create/edit/transition/comment/delete code paths exist. | ✅ PASS |
| **X. Sync-then-read** | systemd timer runs the sync into `isreq`; the app reads only Postgres; a `sync_state` watermark drives incremental `updated >= last_sync` JQL with full changelog expansion; upserts on stable keys make it idempotent. | ✅ PASS |
| **XI. Secrets & residency** | Jira email+token and DB password come from env via pydantic-settings; never logged/printed; `.env`, config with secrets, and the agent folder are gitignored; egress limited to the Jira API; no ticket data leaves the host. | ✅ PASS |

**Result: PASS — no violations.** Complexity Tracking is therefore empty.

## Project Structure

### Documentation (this feature)

```text
specs/001-isreq-analytics-dashboard/
├── plan.md              # This file (/speckit-plan output)
├── research.md          # Phase 0 output — decisions & rationale
├── data-model.md        # Phase 1 output — entities, intervals, derivations
├── quickstart.md        # Phase 1 output — runnable validation guide
├── contracts/           # Phase 1 output — config, metrics, sync contracts
│   ├── config.md
│   ├── metrics.md
│   └── sync.md
├── checklists/
│   └── requirements.md  # spec quality checklist (from /speckit-specify + /speckit-clarify)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/isreq_dashboard/
├── config.py               # pydantic-settings: env (secrets) + TOML (non-secret) + user-region CSV loader
├── db/
│   ├── engine.py           # SQLAlchemy engine; MetaData(schema="isreq"); search_path connect_args
│   ├── models.py           # issues, issue_labels, changelog, priority_intervals, status_intervals, worklogs, users, sync_state
│   └── session.py
├── jira/
│   ├── client.py           # read-only (GET) Jira REST v3 client; pagination; auth = email + API token
│   ├── sync.py             # incremental, idempotent sync; upserts; watermark
│   ├── worklog.py          # complete worklog fetch (per-issue + bulk updated/list) — never trust inline ≤20
│   └── mapping.py          # raw Jira issue/changelog/worklog → row dicts; field-id resolution
├── domain/
│   ├── weeks.py            # week(t) = floor((t-anchor)/7d)+1; pre-inception bucket
│   ├── priority.py         # build priority_intervals; entry/exit events; raised-to-Highest
│   ├── status.py           # build status_intervals; close events; reopen handling
│   ├── regions.py          # region_from_timestamp(windows) | region_from_user_map(account_id)
│   └── stats.py            # mean, sample stddev (n-1), CV, low-n flag, sample/pop label
├── metrics/
│   ├── highest.py          # north-star create-rate vs close-rate (+ cumulative Highest backlog)
│   ├── intake.py           # created-per-period by area/sub-area/region
│   ├── throughput.py       # close-events-per-period (count each close)
│   ├── backlog.py          # open-at-point from created/resolved/reopen history
│   ├── time_invested.py    # worklog seconds per period by issue/area, bucket by started
│   └── drilldown.py        # underlying-ticket queries; exact created-in vs closed-in semantics
├── app/                    # Streamlit
│   ├── Home.py             # north-star view (default landing)
│   ├── pages/
│   │   ├── 1_Intake.py  2_Throughput.py  3_Backlog.py  4_Time_Invested.py  5_Region.py
│   └── components/
│       ├── controls.py     # cadence + scope toggles, period range, freshness banner
│       └── drilldown.py    # ticket table (key, title, assignee)
└── cli/
    ├── sync_main.py        # systemd-timer entrypoint (idempotent)
    ├── init_schema.py      # additive schema setup (human-invoked)
    └── admin_reset.py      # DESTRUCTIVE; explicit, human-only; NEVER on boot/timer

migrations/                 # Alembic, scoped to isreq (additive only)
deploy/
├── isreq-sync.service  isreq-sync.timer
└── isreq-dashboard.service
config/
├── config.example.toml     # anchor date, field ids, region windows, label/priority/title casing, closed-status set
└── users-region.example.csv
tests/
├── unit/  integration/  contract/
└── fixtures/               # recorded Jira issue+changelog+worklog samples
```

**Structure Decision**: Single Python project (no separate frontend/backend split) because Streamlit serves the UI in-process and the only other entrypoint is the batch sync job. Domain logic (weeks, priority/status intervals, regions, stats) is isolated from both Streamlit and Jira so it is unit-testable without either. The destructive admin command is physically separated into its own CLI module to make Article VIII's "never on boot/sync" guarantee structural, not just behavioral.

## Complexity Tracking

No constitution violations — this section is intentionally empty.
