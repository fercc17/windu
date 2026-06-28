# Contract: Configuration

The application is configured from three sources. **Secrets come from the environment only**; non-secret tuning comes from a TOML file; the user→region map comes from a CSV. Validation happens at startup (pydantic-settings); a missing or malformed required key is a hard startup error (never a silent default that flatters the data). No secret is ever logged or printed (Art. XI).

**Status legend**: ✅ resolved from Jira (2026-06-12, see [../jira-discovery.md](../jira-discovery.md)) · ⏳ awaiting a human decision/data.

## Environment variables (secrets + connection)

| Env var | Required | Meaning |
|---|---|---|
| `ISREQ_JIRA_BASE_URL` | ✅ `https://warthogs.atlassian.net` | Jira site |
| `ISREQ_JIRA_EMAIL` | ✅ provided in `.env` | auth identity |
| `ISREQ_JIRA_API_TOKEN` | ✅ secret in `.env` | single token; covers issues, changelog, worklog. **No Tempo token.** |
| `ISREQ_DB_HOST` / `ISREQ_DB_PORT` / `ISREQ_DB_NAME` | ⏳ | Postgres connection (NOT the `postgres` maintenance DB) |
| `ISREQ_DB_USER` | ⏳ | must be `isreq_app` (non-superuser) |
| `ISREQ_DB_PASSWORD` | ⏳ secret | |
| `ISREQ_CONFIG_FILE` | no | path to TOML (default `config/config.toml`) |
| `ISREQ_USERS_CSV` | no | path to user→region CSV (default `config/users-region.csv`) |

Connection MUST set `search_path=isreq` via `connect_args` (Art. VIII).

## TOML config (non-secret)

| Key | Value | Status | Spec ref |
|---|---|---|---|
| `project_key` | `ISREQ` (issue keys `ISREQ-NNN`) | ✅ | FR-001 |
| `field_area` | `customfield_13027` (parent of the "Request area" cascading select) | ✅ | FR-014 |
| `field_sub_area` | `customfield_13027` (child of the same cascade) | ✅ | FR-014 |
| `field_pulse` | `customfield_10020` ("Sprint"); pulse = sprint name (`IS Pulse 2026#NN`), latest when multiple | ✅ | FR-012 |
| `highest_priority_name` | `Highest` | ✅ | FR-006 |
| `ps5_blocker_label` | `ps5-blocker` | ✅ | FR-013 |
| `pr_mp_title_substring` | `[PR/MP Review]` | ✅ | FR-028 |
| `closed_statuses` | `["Closed","Done","Rejected"]` — all three count as a close (`Rejected` included) | ✅ (decided 2026-06-12) | FR-015 |
| `low_n_threshold` | `5` | default | FR-024 |
| `anchor_date` | `2026-02-09` (first real ticket ISREQ-2; a Monday) | ✅ (decided 2026-06-12) | FR-011 |
| `region_windows_utc` | ⏳ **provide** EMEA-anchored hour ranges | ⏳ | FR-026a |
| `reference_timezone` | `EMEA` | default | FR-027 |
| `pr_mp_default_visibility` | `included` (counted in core views; toggle to hide) | ✅ (decided 2026-06-12) | FR-028 |

> Native field `customfield_10368` "Region" exists but is empty in samples → **not used**; region is derived (Art. V).

`region_windows_utc` shape (EMEA-anchored, example — **provide** real boundaries):
```toml
[region_windows_utc]
EMEA = { start = "06:00", end = "14:00" }
AMER = { start = "14:00", end = "22:00" }
APAC = { start = "22:00", end = "06:00" }   # wraps midnight
```

## User→region CSV ⏳

```csv
account_id,region
<accountId>,EMEA
```
Regions MUST be one of `AMER`,`EMEA`,`APAC`. Unmapped assignees resolve to `Unknown` (never guessed). I can generate the full skeleton of all ISREQ assignees (account_id + display_name) on request; the AMER/EMEA/APAC values are yours to fill.

## Validation contract (tested)

- Startup fails loudly if any required/⏳ key is missing or malformed.
- `anchor_date` parses as a date; region windows cover 24h with no gap; CSV regions are in the allowed set.
- Secrets are absent from all log output (assert no token/password substring in logs).
- `ISREQ_DB_USER` is rejected if it resolves to a superuser role (defensive check toward Art. VIII).
