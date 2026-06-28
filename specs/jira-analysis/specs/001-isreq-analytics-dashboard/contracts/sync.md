# Contract: Sync Job

The sync job is the **only** component that talks to Jira, and it is **read-only** (Art. IX) and **write-confined to `isreq`** (Art. VIII). It runs on a systemd timer (Art. X), is idempotent, and never blocks a dashboard render.

## Inputs

- Config + secrets (see [config.md](./config.md)).
- `sync_state` watermark per resource.
- Mode: `incremental` (default) or `full` (explicit flag for a backfill; still additive, still upsert-only).

## Behavior

1. **Issues + changelog**: JQL `project = <key>` (+ `AND updated >= "<last_sync>"` when incremental), expanding `changelog`, paginated to completion. Detect changelog truncation on long-lived issues and complete via the per-issue changelog endpoint (R-002).
2. **Worklogs**: never trust the inline ≤20; complete per issue via the per-issue worklog endpoint, or incrementally via `worklog/updated` → `worklog/list` (R-003). Bucket by `started`. Store no author.
3. **Derive**: rebuild `priority_intervals` and `status_intervals` for each touched issue from its (now complete) changelog.
4. **Upsert**: write issues, labels, changelog, worklogs, intervals using stable keys (issue key; changelog id; worklog id). Re-running produces no duplicates (SC-009).
5. **Watermark**: advance `sync_state.last_sync_at` **only on full success**; on failure leave it so the next run retries the same delta.
6. **Users**: load/refresh the user→region map from CSV (not from Jira).

## Guarantees (tested)

| Guarantee | Test |
|---|---|
| **Read-only source** | No non-GET HTTP method is ever issued (assert client has no write methods). |
| **Idempotent** | Run sync twice on the same fixture ⇒ identical row counts, no dupes (SC-009). |
| **Incremental** | Second run with advanced watermark issues `updated >=` JQL and touches only changed issues. |
| **Worklog completeness** | An issue fixture with > 20 worklogs ⇒ all entries persisted (FR-004). |
| **Schema isolation** | All writes are to `isreq.*`; no statement targets `public` or another schema; connects as `isreq_app` (Art. VIII). |
| **Non-destructive** | Normal/`full` runs issue no `DROP`/`TRUNCATE`/drop-all; those exist only in `cli/admin_reset.py` (Art. VIII). |
| **Restart-safe** | Killing mid-run then re-running converges to the same state and advances the watermark only once complete (R-010). |
| **Secret-safe** | No token/password appears in logs (Art. XI). |

## Outputs

- Populated/updated `isreq` tables (data-model.md).
- An advanced `sync_state` watermark and a `last_sync_at` timestamp surfaced to the dashboard (SC-008).
- Structured logs (counts synced, duration) with **no** secrets and **no** ticket bodies shipped anywhere off-host (Art. XI).
