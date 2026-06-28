# Jira Discovery — ISREQ (2026-06-12)

Read-only discovery against `https://warthogs.atlassian.net` to resolve the CONFIG GLOSSARY. All calls were GET/approximate-count (Article IX). Raw responses were inspected locally and not committed.

## Connection
- Base URL: `https://warthogs.atlassian.net`
- Project: key **`ISREQ`**, name "IS Requests-ENG", id 14645, type software.
- Issue keys are therefore **`ISREQ-NNN`** (the spec/constitution stylize this as `ISReq-NNN`).

## Resolved configuration

| Key | Resolved value | Source |
|---|---|---|
| `project_key` | `ISREQ` | `/project/search` |
| `field_area` | `customfield_13027` **parent** ("Request area", cascading select) | `/field` + issue sample |
| `field_sub_area` | `customfield_13027` **child** | same cascading field |
| `field_pulse` | `customfield_10020` ("Sprint"); pulse = sprint **name** e.g. `IS Pulse 2026#04` | `/field` + sample |
| `highest_priority_name` | `Highest` (id 1; full set Highest/High/Medium/Low/Lowest) | `/priority` |
| `ps5_blocker_label` | `ps5-blocker` (lowercase; JQL match is case-insensitive but stored lowercase) | label sample |
| `pr_mp_title_substring` | `[PR/MP Review]` (literal, with brackets) | summary sample |
| `closed_statuses` | `Closed`, `Done`, `Rejected` (statusCategory = *done*) | `/project/ISREQ/statuses` |

Cascading area example: `'ProdStack (private cloud)' > 'Networking -> Cloud-to-cloud connectivity'`, `'IS Operated Services' > 'Database (DBaaS)'`. Some issues have parent only (child = null → treat sub-area as `(none)`).

Full status list: *done* → Closed, Done, Rejected · *new* → Triaged, Untriaged · *undefined* → Open · *indeterminate* → BLOCKED, Escalated, In Progress, In Review, Materialized, Sleeping, To Be Deployed.

## Dataset snapshot
- Total ISREQ issues: **2,887** (revises the plan's ≤50k envelope down to ~3k, growing).
- Currently `Highest`: 361 (point-in-time count via changelog will differ).
- `ps5-blocker`: 99.
- `[PR/MP Review]` (summary match): **932 (~32%)** — high-volume automated review tickets.
- Pulse cadence: 2 weeks (e.g. IS Pulse 2026#04 = 2026-02-16 → 2026-03-02).
- Assignees are few/concentrated in recent sample (top: gianluca.perna 41 of 100). Full user→region list TBD.

## Decisions (resolved 2026-06-12)

1. **Week-1 anchor date** → `2026-02-09` (first real ticket ISREQ-2; a Monday). Defines all week numbers; earlier tickets land in the pre-inception bucket.
2. **`Rejected` as a close** → yes. All of {Closed, Done, Rejected} count as closes (throughput + backlog exit).
3. **PR/MP Review default** → included in core views by default, with a toggle to hide (matches the `/clarify` decision).

## Still needed (data — not derivable from Jira)

4. **Region derivation inputs** (Article V): the EMEA-anchored UTC `region_windows`, and the static user→region CSV. Native `customfield_10368` "Region" is empty, so it is **not** used. I can generate the full assignee skeleton (account_id + display_name) on request; the AMER/EMEA/APAC values are yours to fill.

## Notes / flags
- **Key casing**: real key is `ISREQ`; consider whether to display `ISREQ-NNN` (accurate) or keep the `ISReq` styling in prose. Functionally, config uses `ISREQ`.
- **Pulse from sprint**: multi-sprint issues exist; per FR-012 attribute to the latest sprint (by startDate). Some issues have no sprint (pulse = none).
- **Worklog/Tempo**: confirmed. Author is `Timesheets by Tempo - Jira Time Tracking` (`accountType=app`) — **validates Article VI** (no per-person attribution; the real logger is not exposed). Entries carry `started` + `timeSpentSeconds`; inline cap is **20** (complete via per-issue `/issue/{key}/worklog` or bulk `/worklog/updated`→`/worklog/list`); `started` may arrive with a non-UTC offset (e.g. `+0100`) → normalize, EMEA reference default. A single Jira token suffices.
