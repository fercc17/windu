<!--
SYNC IMPACT REPORT
==================
Version change: (uninitialized template) → 1.0.0
Bump rationale: Initial ratification. The placeholder template is replaced with a
  complete, project-specific constitution. Per semantic versioning for governance
  documents, the first concrete adoption is 1.0.0 (MAJOR baseline).

Principles/sections defined (all newly added in this ratification):
  - Mission (north-star question framing the product)
  - Article I: The Highest hypothesis is the north star
  - Article II: Every number is traceable
  - Article III: Statistical honesty
  - Article IV: Two cadences and two scopes are first-class
  - Article V: Region has two distinct derivations
  - Article VI: Time invested is best-effort, issue-level, and labeled
  - Article VII: Point-in-time correctness
  - Article VIII: Database isolation and non-destruction
  - Article IX: Read-only on source systems
  - Article X: Sync-then-read
  - Article XI: Secrets and data residency
  - Governance (amendment + conflict-surfacing procedure)

Removed sections: placeholder template principles ([PRINCIPLE_1..5], [SECTION_2], [SECTION_3]).

Templates / dependent artifacts status:
  - .specify/templates/plan-template.md          ✅ no change needed
        ("Constitution Check" gate resolves against this file dynamically)
  - .specify/templates/spec-template.md          ✅ no change needed (no hard-coded principles)
  - .specify/templates/tasks-template.md         ✅ no change needed (no hard-coded principles)
  - .specify/templates/checklist-template.md     ✅ no change needed (no hard-coded principles)
  - .specify/templates/constitution-template.md  ✅ unchanged (source template, not an artifact)
  - CLAUDE.md                                     ✅ no change needed (points to current plan, generic)

Deferred / follow-up TODOs: none.
  RATIFICATION_DATE set to 2026-06-11 (initial adoption date — constitution first
  provided and ratified today).
-->

# ISReq Analytics Dashboard Constitution

## Mission

The ISReq Analytics Dashboard exists to answer one question with evidence: are Highest-priority tickets being created faster than the team can close them? Around that question it measures intake volume, time invested, throughput, and backlog across request areas and sub-areas and regions, on both a weekly and a per-pulse basis, for all ISReq tickets and for the `ps5-blocker` subset. The detailed requirements live in the specification. This constitution governs how every phase makes tradeoffs while building toward that mission.

These articles are non-negotiable and bind every phase: specify, plan, tasks, and implement. Any spec, plan, task, or code that violates an article must be rejected and corrected, not worked around. When an article conflicts with convenience, the article wins.

## Article I: The Highest hypothesis is the north star

- The Highest-priority analysis is the centerpiece of the product. When effort, scope, or polish must be prioritized, the Highest analysis wins over secondary views.
- The tool MUST make the create-rate versus close-rate comparison for Highest tickets directly legible, including tickets raised to Highest after creation.
- Rationale: every other view is supporting context. If the Highest story is wrong or buried, the tool has failed regardless of how good the rest looks.

## Article II: Every number is traceable

- No aggregate may be a dead end. Any count, average, or chart value that represents a set of tickets MUST be drillable down to the underlying tickets, each shown with its Jira key (`ISReq-NNN`), title, and assignee display name.
- Drill-down filters MUST mean exactly what they say. "Closed in week N" lists tickets closed that week even if created earlier. "Created in week N" lists only those created that week. The two MUST NOT be conflated.
- Rationale: an unverifiable number is a rumor. The user has to be able to click any figure and see the tickets behind it.

## Article III: Statistical honesty

- Averages MUST be reported alongside dispersion. Where the spec calls for time-to-close, report mean, standard deviation, and coefficient of variation together, never the mean alone.
- The tool MUST state whether a statistic is sample or population based, and MUST surface low sample sizes rather than presenting a fragile average as solid.
- The tool MUST NOT smooth, round, or omit data in a way that flatters the hypothesis or hides it. The numbers go where the data goes.
- Rationale: this dashboard exists to settle an argument with data. Dishonest or naive stats poison that.

## Article IV: Two cadences and two scopes are first-class

- Weekly and per-pulse cadences are both first-class. A "week" is numbered relative to dashboard inception, not ISO week. A pulse is read from the ticket's sprint field.
- Both analysis scopes, all ISReq tickets and the `ps5-blocker` labeled subset, MUST be supported wherever the spec requires, with consistent definitions across both.
- Rationale: a view that only works for one cadence or one scope is half-built.

## Article V: Region has two distinct derivations

- Region (AMER, EMEA, APAC) is derived two different ways and these MUST NOT be conflated or silently substituted for each other:
  - Creation-time-of-day analysis derives region from the ticket creation timestamp using configurable, EMEA-anchored time windows.
  - Per-user counts derive region from a static user-to-region map.
- Where the data presents in a single reference timezone, it MUST default to EMEA, since most users are EMEA.
- Rationale: mixing the two derivations produces numbers that look right and are wrong.

## Article VI: Time invested is best-effort, issue-level, and labeled

- Time invested is sourced from Jira native worklog, into which Tempo syncs. It MUST be presented as best-effort, dependent on logging discipline, and MUST NOT be presented as authoritative actual effort.
- Time is attributed at issue and area level only. Synced worklogs are authored by the Tempo app, not the human who logged them, so per-person time attribution is NOT available and MUST NOT be invented or guessed.
- Worklog entries MUST be bucketed into weeks and pulses by the worklog `started` date, not by issue creation date.
- Rationale: an intake queue rarely has clean worklogs, and the synced author hides the real logger. Overselling either misleads.

## Article VII: Point-in-time correctness

- Ticket priority is not static. Any metric or drill-down depending on whether a ticket held a given priority during a given week MUST be computed from priority history reconstructed from the Jira changelog, never from current priority.
- "Raised to Highest after creation" MUST be detected from changelog priority transitions, counted in the week the transition occurred.
- Rationale: reading current priority silently produces wrong Highest numbers, which breaks Article I.

## Article VIII: Database isolation and non-destruction

The application shares a PostgreSQL instance with other unrelated projects. It MUST NOT damage anything outside its own boundary.

- The app MUST connect only as the non-superuser role `isreq_app`, whose default `search_path` is the dedicated schema `isreq`.
- All application objects MUST live in the `isreq` schema, created under that search_path or schema-qualified as `isreq.*`. The app MUST NOT create, alter, or drop objects in `public` or any other schema, and MUST NOT connect to the `postgres` maintenance database for application data.
- Schema setup MUST be additive only (`CREATE TABLE IF NOT EXISTS`). The app MUST NOT issue `DROP TABLE`, `DROP SCHEMA`, `TRUNCATE`, or ORM drop-all during normal startup, sync, or migration.
- Any destructive operation MUST be a separate, explicitly named command a human invokes deliberately, and MUST NEVER run on boot or on the sync schedule.
- With SQLAlchemy: bind `MetaData(schema="isreq")` and set `connect_args={"options": "-csearch_path=isreq"}`. With Alembic: set `version_table_schema="isreq"` and `include_schemas=True`, scoping migrations to `isreq`.
- Rationale: ownership and search_path are the hard guarantees. A bug must be containable to `isreq` and nothing else.

## Article IX: Read-only on source systems

- Jira and Tempo are read-only. The app MUST NOT create, edit, transition, comment on, or delete anything in them. All write side effects are confined to the `isreq` schema.
- Rationale: this is an observer over a live company queue, not a participant.

## Article X: Sync-then-read

- Source data MUST be pulled by a scheduled sync job into the `isreq` schema. The app MUST read only from the database and MUST NOT call the Jira or Tempo API during a page render or interaction.
- The sync job MUST be idempotent and safe to run repeatedly on a timer, using upserts on stable keys, and SHOULD fetch incrementally while still capturing full changelog history.
- Rationale: statistics and drill-downs need a stable local dataset, and per-render API calls do not scale.

## Article XI: Secrets and data residency

- Jira credentials, the Tempo token, and the database password MUST come from environment or a secret store, never hardcoded, never committed. The agent folder and credential-bearing files MUST be gitignored. Code MUST NOT print secrets.
- This is internal company data on a homelab. Network egress MUST be limited to the Jira and Tempo APIs. Ticket data MUST NOT be shipped to any third-party service.
- Rationale: these tokens reach internal data. Leakage is a security incident.

## Governance

- Amendments require a deliberate, explicit change to this document. No phase may relax an article on its own initiative.
- If any spec, plan, task, or implementation cannot satisfy an article, it MUST surface the conflict for a human decision rather than silently violating it.
- Versioning policy: this constitution is versioned with semantic versioning. MAJOR for backward-incompatible governance or principle removals/redefinitions; MINOR for a new article/section or materially expanded guidance; PATCH for clarifications, wording, and non-semantic refinements.
- Compliance review: every spec, plan, tasks list, and implementation MUST be checked against these articles before it is accepted. The plan template's Constitution Check gate is the enforcement point for that review.

**Version**: 1.0.0 | **Ratified**: 2026-06-11 | **Last Amended**: 2026-06-11
