# Phase 0 Research: ISReq Analytics Dashboard

This document records the decisions that resolve the open questions in the Technical Context. The brief supplied with `/speckit-plan` already fixed most of the stack; the items below are the choices that needed an explicit decision and rationale. No `NEEDS CLARIFICATION` items remain that block planning; remaining items are **configuration values** to fill before `/speckit-implement` (see [contracts/config.md](./contracts/config.md)).

---

## R-001 — UI / runtime framework

- **Decision**: **Streamlit** (Python), single-process app reading PostgreSQL.
- **Rationale**: Internal homelab tool with few concurrent viewers; Streamlit gives the fastest path to the required drill-down (dataframe selection events + query params) and charts, at the lowest build cost. The spec keeps requirements UI-agnostic, so this binds no functional requirement.
- **Alternatives considered**: **React + FastAPI** — rejected for v1 (materially higher build cost; adds an API tier and auth concerns not justified for a few internal users). **Fallback trigger**: revisit before `/speckit-tasks` if a polished multi-user UI, fine-grained auth, or public exposure becomes a requirement.
- **Revisit gate**: This is the one decision the brief flagged as "decide before /tasks." Confirm or override at the start of `/speckit-tasks`.

## R-002 — Jira fetch strategy (issues + changelog)

- **Decision**: Query the Jira Cloud REST v3 search endpoint with JQL scoped to `project = ISReq`, expanding `changelog` to capture priority and status transitions; paginate fully. Incremental syncs add `updated >= "<last_sync>"` to the JQL.
- **Rationale**: Article X requires incremental fetch that still captures full changelog history; changelog expansion on search gives both in one paginated pass.
- **Alternatives considered**: Per-issue changelog endpoint for every issue (more calls, slower); webhooks (push) — rejected: not sync-then-read, adds a listener and ordering/replay concerns.
- **Note**: The search endpoint caps inline changelog history on very long-lived issues; sync MUST detect truncation and complete such issues via the per-issue changelog endpoint.

## R-003 — Worklog completeness (Article VI / FR-004)

- **Decision**: Treat the ≤20 worklog entries inlined by issue search as **incomplete**. For any issue with more, complete via `/rest/api/3/issue/{key}/worklog` (paginated). For incremental runs, use the bulk pair `/rest/api/3/worklog/updated` → `/rest/api/3/worklog/list` to pull only changed worklogs. Bucket every entry by its `started` timestamp. Do **not** store the worklog author for attribution (it is the Tempo sync app).
- **Rationale**: Prevents silent undercounting of time invested; the bulk pair keeps incremental syncs cheap.
- **Alternatives considered**: Trusting inline worklogs — rejected (undercounts, violates FR-004).

## R-004 — Point-in-time priority (Article VII)

- **Decision**: Reconstruct `priority_intervals(issue_key, priority, valid_from, valid_to)` from the issue's **creation priority** plus its **ordered changelog priority changes**. All "was Highest during period W" questions are interval range lookups. "Raised to Highest after creation" = a priority changelog row with `to = Highest` where the priority immediately before was not Highest.
- **Rationale**: Article VII forbids reading current priority for historical questions; intervals turn a per-query replay into an indexable range lookup.
- **Clarified semantics (Session 2026-06-12)**: Each transition into Highest is a distinct **entry event** counted in its period; a ticket exits the Highest set when it drops below Highest or is closed; net Highest backlog = cumulative entries − exits = live count of Highest-and-open tickets.

## R-005 — Status intervals, close events & reopens

- **Decision**: Reconstruct `status_intervals` from status changelog. A **close event** is any transition into the configured closed-status set; **count each close** (a closed→reopened→closed ticket counts twice in throughput, once per close). Backlog "open at T" = created on/before T and not in a closed status as of T (reopened ⇒ open again until re-closed).
- **Rationale**: Matches the clarified throughput/backlog semantics (FR-015/016) and keeps "closed in period N" literally true (FR-021).
- **Alternatives considered**: Using only `resolved_at` (current) — rejected: loses reopen history and intermediate closes.

## R-006 — Week numbering & pre-inception data

- **Decision**: `week(t) = floor((t − anchor_date) / 7 days) + 1`, week 1 = the anchor week. Timestamps before the anchor land in a labeled pre-inception bucket (week ≤ 0) rather than being merged into week 1.
- **Rationale**: Article IV requires inception-relative numbering, not ISO weeks. Anchor is configurable.
- **Resolved (2026-06-12)**: `anchor_date = 2026-02-09` (first real ticket ISREQ-2; a Monday). See contracts/config.md.

## R-007 — Region: two derivations (Article V)

- **Decision**: Two independent, non-substitutable functions. (a) `region_from_timestamp(created_at, windows)` maps the creation hour to AMER/EMEA/APAC using configurable, **EMEA-anchored** UTC windows — used for creation-time-of-day intake analysis. (b) `region_from_user_map(account_id)` reads the static user→region map — used for per-user counts. EMEA is the default reference timezone where a single tz is needed.
- **Rationale**: Article V explicitly forbids conflating the two; separate functions make substitution impossible by construction. Unmapped users → `Unknown`.
- **Open config**: the UTC window boundaries and the user-region CSV — fill before implement.

## R-008 — Statistics (Article III)

- **Decision**: `domain/stats.py` returns, for any time-to-close set: mean, **sample** standard deviation (n−1), and coefficient of variation `CV = s / mean`, always labeled sample-based, plus a low-sample flag when `n < LOW_N_THRESHOLD` (default 5, configurable). No smoothing/rounding that distorts the create-vs-close comparison.
- **Rationale**: Article III mandates dispersion alongside the mean and surfacing low samples; sample stddev is the honest default for an observed subset.
- **Alternatives considered**: Population stddev — rejected as default (we observe a sample of an ongoing process), but the label makes the choice explicit.

## R-009 — Database isolation (Article VIII)

- **Decision**: Connect as `isreq_app`; `MetaData(schema="isreq")`; `connect_args={"options": "-csearch_path=isreq"}`. Alembic configured with `version_table_schema="isreq"` and `include_schemas=True`, migrations scoped to `isreq`. App startup runs `alembic upgrade head` (additive) — never `create_all` drop-first, never drop-all. All `DROP`/`TRUNCATE`/reset operations live only in `cli/admin_reset.py`, invoked deliberately by a human, never on boot or on the timer.
- **Rationale**: Direct implementation of Article VIII's hard guarantees (ownership + search_path + additive-only + isolated destructive command).
- **Alternatives considered**: Plain idempotent `CREATE TABLE IF NOT EXISTS` without Alembic — viable but loses migration history; Alembic chosen for versioned, reviewable schema changes while still additive.

## R-010 — Scheduling & idempotency (Article X)

- **Decision**: A systemd **timer** triggers `cli/sync_main.py`. A `sync_state` row holds the last successful sync watermark. The sync upserts on stable keys (issue key, changelog id, worklog id) so re-runs neither duplicate nor corrupt. On success it advances the watermark; on failure it leaves the watermark untouched so the next run retries the same delta.
- **Rationale**: systemd timer is the host-native scheduler; watermark + upsert give idempotent, incremental, restart-safe sync (SC-009).
- **Alternatives considered**: APScheduler in-process — rejected (couples scheduling to the app lifecycle; systemd is more robust on the host).

## R-011 — Configuration & secrets (Article XI)

- **Decision**: `pydantic-settings` loads **secrets from environment only** (Jira account email + API token; DB password) and **non-secret config from a TOML file** (anchor date, area/sub-area/pulse field ids, region windows, closed-status set, Highest priority name, `ps5-blocker` label, PR/MP title substring). The user→region map loads from a CSV path. No secret is logged or printed. `.env` and any secret-bearing config are gitignored.
- **Rationale**: Article XI requires env/secret-store sourcing and no committed secrets; splitting secret vs non-secret keeps the TOML safely shareable.
- **Note on Tempo**: Tempo syncs into Jira native worklog, so a single Jira token covers issues, changelog, and worklog — **no Tempo token required**; egress is effectively only to the Atlassian Jira API.

## R-012 — Custom field discovery (area / sub-area / pulse)

- **Decision**: The fields carrying area, sub-area, and pulse are **configurable custom-field ids**. Provide a one-shot discovery helper that lists `/rest/api/3/field` so the operator can identify the correct `customfield_*` ids during setup.
- **Rationale**: Field ids are instance-specific; discovery + config avoids hardcoding and keeps the model correct (the brief flags "confirm where area lives").
- **Open config**: the resolved field ids — fill before implement.

---

## Open configuration (carried to `/speckit-implement`, not blocking the plan)

These are **values**, not design decisions; collected in [contracts/config.md](./contracts/config.md):

- Jira base URL; exact `ISReq` project key casing.
- Week-1 `anchor_date`.
- Custom-field ids for area / sub-area / pulse.
- Closed-status set (e.g. `{Done, Closed, Resolved}`).
- Region UTC windows (AMER / EMEA / APAC), EMEA-anchored.
- User→region CSV (`account_id, region`).
- Highest priority name; `ps5-blocker` label casing; PR/MP-review title substring.
- Low-sample threshold (default 5).
