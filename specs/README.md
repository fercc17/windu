# Specs, constitutions & design docs

Reference material brought over from the three apps merged into windu. Source of
truth for *why* each subsystem behaves the way it does; useful when re-pointing
the merged backend and rebuilding the UI.

## is-cmdb/  → CMDB, IS Services, GitOps, Change management
- `ARCHITECTURE.md`, `SCHEMA.md`, `DJANGO_UI.md` — system, data model, UI design
- `PARSER.md`, `POLLER.md`, `NETBOX_INTEGRATION.md` — ingestion subsystems
- `reverse_spec.md`, `AGENTS.md` — reverse-engineered spec / runbook
- `docs/` — `cab-design.md`, `is-cmdb-cab-spec.md` (Change Advisory Board),
  `charm-architecture.md`, `is-infrastructure-integration.md`, `TOKENS.md`,
  `development.md`, `parser-github-actions-setup.md`, `findings/`

## jira-analysis/  → ISReq, ISDB, PagerDuty
- `constitution.md` — the project "constitution" (Art. I–XI: north-star, interval
  reconstruction, DB isolation, read-only clients, sync-then-read, secrets)
- `specs/001-isreq-analytics-dashboard/` — spec, plan, data-model, research, tasks,
  contracts, jira-discovery
- `specs/is-operations-pagerduty/` — PagerDuty analytics spec
- `specs/is-operations-automation/` — automation spec (+ PDF/HTML/PNG artifacts)

## standup-dashboard/  → Stand up
- `constitution.md`
- `specs/001-sre-standup-dashboard/` — spec, plan, data-model, research, tasks,
  contracts, checklists

## Test fixtures
Juju status fixtures (`ps5`/`ps6`/`ps7`) live at
`backend/tests/fixtures/juju/{ps5,ps6,ps7}.txt`.
