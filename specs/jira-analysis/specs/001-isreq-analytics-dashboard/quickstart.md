# Quickstart & Validation Guide: ISReq Analytics Dashboard

A runnable guide to stand the system up and prove the feature works end-to-end. It references [data-model.md](./data-model.md) and [contracts/](./contracts/) rather than duplicating them. Implementation code lives in `tasks.md` / the implementation phase, not here.

## Prerequisites

- Linux host / LXD container, Python 3.12, a reachable PostgreSQL instance.
- A **non-superuser** role `isreq_app` owning a dedicated schema `isreq` (created once by a human/DBA, not the app):
  ```sql
  -- run by an admin, once; the app never does this
  CREATE ROLE isreq_app LOGIN PASSWORD '***';
  CREATE SCHEMA IF NOT EXISTS isreq AUTHORIZATION isreq_app;
  ALTER ROLE isreq_app SET search_path = isreq;
  ```
- A Jira Cloud account with read access to the ISReq project + an API token.

## Setup

1. Create a venv and install: `pip install -e .` (deps: streamlit, sqlalchemy, psycopg, alembic, httpx, tenacity, pandas, pydantic-settings, pytest).
2. Configure: copy `config/config.example.toml` → `config/config.toml` and fill the **FILL** keys (anchor date, field ids, region windows, closed statuses, label/priority/title casing — see [contracts/config.md](./contracts/config.md)). Set secrets in the environment (`ISREQ_JIRA_*`, `ISREQ_DB_*`). Provide `config/users-region.csv`.
3. Create tables (additive, human-invoked): `python -m isreq_dashboard.cli.init_schema` (runs `alembic upgrade head`; `isreq`-scoped; never drops). Re-running is a no-op.

## Run

- One sync: `python -m isreq_dashboard.cli.sync_main` (incremental; first run backfills).
- Schedule it: install `deploy/isreq-sync.timer` + `.service` (systemd). The dashboard never calls Jira.
- Dashboard: `streamlit run src/isreq_dashboard/app/Home.py`.

## Validation scenarios (map to Success Criteria)

| # | Scenario | Steps | Expected | Verifies |
|---|---|---|---|---|
| V1 | **North star legible** | Open the dashboard. | The Highest create-vs-close chart + cumulative Highest backlog is the landing view, no config needed. | SC-001, Art. I |
| V2 | **Raised-to-Highest counted in transition week** | Use the fixture issue created Medium in wk2, raised to Highest in wk5, closed wk6. | Appears in wk5 `became_highest`, not wk2; appears in wk6 `closed`. | SC-003, FR-007 |
| V3 | **Drill-down semantics distinct** | Drill "created in wk2" and "closed in wk6" for V2's issue. | Issue appears in both; absent from "created in wk6" and "closed in wk2". Each row shows key/title/assignee. | SC-002, FR-021 |
| V4 | **Stats never bare** | Open any time-to-close figure. | mean + sample stddev + CV + "sample" label shown together; a low-n bucket is flagged. | SC-004, FR-022–024 |
| V5 | **Two cadences × two scopes** | Toggle weekly↔per-pulse and all↔ps5-blocker on each view. | Numbers recompute consistently; ps5-blocker ≤ all everywhere. | SC-005, FR-010/013 |
| V6 | **Time bucketed by worklog date** | Fixture: worklog started wk8 on an issue created wk2. | Contributes to wk8, not wk2; no per-person view exists. | SC-006, FR-017/018 |
| V7 | **Sync-then-read** | Stop network to Jira, reload dashboard. | All views still render from Postgres. | SC-007, Art. X |
| V8 | **Idempotent sync** | Run sync twice on the same fixture. | Identical row counts; no duplicate issues/changelog/worklogs. | SC-009, Art. VIII/X |
| V9 | **Freshness shown** | Look at the header. | `last_sync_at` is displayed. | SC-008 |
| V10 | **Isolation & non-destruction** | Inspect SQL issued during sync/boot. | All writes are `isreq.*`; no `DROP`/`TRUNCATE`; connects as `isreq_app`. | Art. VIII |

## Done = all V1–V10 pass against a seeded fixture dataset, then against the live ISReq project after the first full sync.
