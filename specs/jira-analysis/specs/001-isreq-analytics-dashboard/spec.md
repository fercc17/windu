# Feature Specification: ISReq Analytics Dashboard

**Feature Branch**: `001-isreq-analytics-dashboard`

**Created**: 2026-06-12

**Status**: Draft

**Input**: User description: "Runtime and data flow — sync-then-read from Jira/Tempo into a local Postgres datastore, queried by a Python dashboard; data sources (Jira Cloud REST v3, issues+changelog+worklog), a suggested data model, derived logic (week numbering, region-from-timestamp, raised-to-Highest detection, priority reconstruction, coefficient of variation), and a configuration glossary." Combined with the project constitution, which supplies the product's mission and non-negotiable principles.

> **Scope note**: This specification covers the **whole dashboard** as feature 001, expressed as prioritized, independently testable user journeys. The runtime architecture, datastore schema, source-system endpoints, and UI framework choice supplied in the input are **implementation concerns** and are deferred to the planning phase — they appear here only as Key Entities (conceptual), Assumptions, and Dependencies, not as functional requirements.

## Clarifications

### Session 2026-06-12

- Q: When a ticket enters Highest priority more than once (raised → dropped → raised again), how should it count toward "became Highest"? → A: Each entry event — every transition into Highest is counted in its period, paired with each exit, so the running balance equals the live count of open Highest tickets.
- Q: How should PR/MP-review tickets (title contains the `[PR/MP Review]` substring) be treated in the core intake / throughput / backlog metrics? → A: Included in all core metrics, and additionally tagged so they can be filtered in/out and viewed as a distinct category.
- Q: If a ticket is closed, reopened, then closed again, how should throughput and the "closed in week N" drill-down treat it? → A: Count each close — every entry into a closed status is a close event counted in its period; backlog shows the ticket open again between reopen and re-close.
- Q: When a ticket's sprint field lists more than one pulse, which pulse should per-pulse views attribute it to? → A: The latest (most recent) pulse.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Is Highest intake outpacing Highest closure? (Priority: P1)

A team lead opens the dashboard and, without configuring anything, sees a single view that answers the north-star question: across time, are tickets becoming Highest priority faster than Highest tickets are being closed? The view counts a ticket as "became Highest" whether it was created at Highest **or** raised to Highest after creation, and it makes the create-rate versus close-rate gap (and the resulting Highest backlog) immediately legible.

**Why this priority**: This is the centerpiece of the product (Constitution Article I). Every other view is supporting context. If this story is absent or wrong, the tool has failed regardless of the rest.

**Independent Test**: Load the dashboard against a synced dataset and read the weekly Highest create-vs-close chart. Verify a ticket created at Medium and later raised to Highest appears in the "became Highest" count for the week of the transition, and that the net Highest backlog line reflects creates minus closes over time. Delivers the core decision-support value on its own.

**Acceptance Scenarios**:

1. **Given** a synced dataset, **When** the user opens the dashboard, **Then** the Highest create-rate vs close-rate comparison is the primary view and is legible without further configuration.
2. **Given** a ticket created as Medium and raised to Highest in week 5, **When** the user views the Highest analysis, **Then** that ticket is counted in week 5's "became Highest" figure and not in any earlier week's.
3. **Given** weeks where more tickets became Highest than were closed, **When** the user views the Highest analysis, **Then** the growing Highest backlog is directly visible.

---

### User Story 2 - Every number is traceable to its tickets (Priority: P1)

From any figure on the dashboard — a count, an average, or a point on a chart — the user clicks to reveal the exact list of tickets behind it. Each ticket shows its Jira key, title, and assignee. Drill-down labels mean exactly what they say: "created in week N" lists only tickets created that week; "closed in week N" lists tickets closed that week even if they were created earlier.

**Why this priority**: Constitution Article II. An aggregate the user cannot open is a rumor. Traceability is what makes the Highest story (US1) and every other view trustworthy, so it ships alongside the north star.

**Independent Test**: Click any aggregate on any view and confirm it expands to the underlying tickets with key, title, and assignee. Confirm that "created in week N" and "closed in week N" drill-downs return different sets for a ticket created in week 2 and closed in week 6.

**Acceptance Scenarios**:

1. **Given** any aggregate value on screen, **When** the user selects it, **Then** the underlying tickets are listed, each with Jira key (`ISREQ-NNN`), title, and assignee display name.
2. **Given** a ticket created in week 2 and closed in week 6, **When** the user drills "created in week 2" and separately "closed in week 6", **Then** the ticket appears in both, and appears in neither "created in week 6" nor "closed in week 2".

---

### User Story 3 - Intake, throughput, and backlog by area, sub-area, and region (Priority: P2)

A manager reviews how the queue is moving: how many tickets are coming in (intake), how many are being closed (throughput), and how many remain open (backlog) over time, broken down by request area, sub-area, and region. This frames the Highest story with overall queue health.

**Why this priority**: These are the core supporting metrics named in the mission. They give the Highest analysis its context but are secondary to it.

**Independent Test**: For a chosen cadence, verify intake equals the count of tickets created per period, throughput equals the count closed per period, and backlog equals open tickets at each point, each splittable by area, sub-area, and region, and each drillable per US2.

**Acceptance Scenarios**:

1. **Given** a chosen period, **When** the user views intake, **Then** it shows the count of tickets created in that period, broken down by area, sub-area, and region.
2. **Given** a chosen period, **When** the user views throughput, **Then** it shows the count of tickets that entered a closed status in that period.
3. **Given** any point in time, **When** the user views backlog, **Then** it shows tickets created on or before that point and not yet resolved.

---

### User Story 4 - Two cadences and two scopes, everywhere (Priority: P2)

The user toggles every analytical view between two cadences — weekly (numbered from dashboard inception) and per-pulse (read from the ticket's sprint) — and between two scopes — all ISReq tickets and the `ps5-blocker` subset — with the metric definitions identical across both.

**Why this priority**: Constitution Article IV. A view that works for only one cadence or one scope is half-built. This is a cross-cutting capability layered onto the views above.

**Independent Test**: On each view, switch weekly↔per-pulse and all↔`ps5-blocker` and confirm the numbers recompute consistently and that the `ps5-blocker` view is a strict subset of the all-ISReq view for the same period and metric.

**Acceptance Scenarios**:

1. **Given** any analytical view, **When** the user switches between weekly and per-pulse, **Then** the same metric is recomputed on the selected cadence with consistent definitions.
2. **Given** any analytical view, **When** the user switches between all ISReq and `ps5-blocker`, **Then** the `ps5-blocker` figures count only tickets carrying the `ps5-blocker` label and are otherwise defined identically.

---

### User Story 5 - Honest time-to-close statistics (Priority: P3)

When the dashboard reports how long tickets take to close, it never shows a lone average. It reports mean, standard deviation, and coefficient of variation together, states whether the statistic is sample- or population-based, and visibly flags buckets with few data points.

**Why this priority**: Constitution Article III. This dashboard exists to settle an argument with data; naive averages poison it. It refines existing metrics rather than adding a new view.

**Independent Test**: For any time-to-close figure, confirm mean, standard deviation, and coefficient of variation are shown together with a sample/population label, and that a period with a small number of closed tickets is flagged as low-sample.

**Acceptance Scenarios**:

1. **Given** a time-to-close figure, **When** it is displayed, **Then** mean, standard deviation, and coefficient of variation appear together with a sample-vs-population label.
2. **Given** a period with few closed tickets, **When** its time-to-close is shown, **Then** the low sample size is surfaced rather than presented as a solid average.

---

### User Story 6 - Best-effort time invested by area (Priority: P3)

The user views how much time has been logged against the queue per period, bucketed by the worklog's own date and attributed at ticket and area level. The view is plainly labeled best-effort and dependent on logging discipline, and it never attributes time to an individual person.

**Why this priority**: Constitution Article VI. Time invested is valuable context but is the least reliable signal and is explicitly secondary to throughput and the Highest story.

**Independent Test**: Confirm time-invested totals bucket a worklog logged in week 8 against a ticket created in week 2 into week 8; confirm the view carries a best-effort caveat and offers no per-person breakdown.

**Acceptance Scenarios**:

1. **Given** a worklog entry started in week 8 on a ticket created in week 2, **When** time invested is shown weekly, **Then** that entry contributes to week 8, not week 2.
2. **Given** the time-invested view, **When** it is displayed, **Then** it is labeled best-effort and provides no per-person attribution.

---

### User Story 7 - Region two ways, never conflated (Priority: P3)

The user sees region (AMER / EMEA / APAC) breakdowns derived correctly for the question being asked: creation-time-of-day analysis derives region from each ticket's creation timestamp using configurable EMEA-anchored windows, while per-user counts derive region from a static user-to-region map. The two derivations are never silently substituted for one another, and EMEA is the default reference timezone.

**Why this priority**: Constitution Article V. Mixing the two derivations produces numbers that look right and are wrong, but region is a secondary lens relative to the Highest story.

**Independent Test**: Confirm a creation-time-of-day region breakdown uses the configured time windows on the creation timestamp, while a per-user region breakdown uses the user-to-region map, and that the two are presented as distinct derivations.

**Acceptance Scenarios**:

1. **Given** the creation-time-of-day analysis, **When** region is shown, **Then** it is derived from the ticket creation hour via the configured EMEA-anchored windows.
2. **Given** a per-user count, **When** region is shown, **Then** it is derived from the static user-to-region map, not from any timestamp.

---

### Edge Cases

- **Multiple priority transitions**: A ticket raised to Highest, dropped below Highest, then raised again — each transition into Highest is counted as a separate entry event in the period it occurred (and each exit likewise); point-in-time "was Highest during period W" is answered from reconstructed priority intervals; the Highest backlog reflects the live Highest-and-open state at period end.
- **Reopened tickets**: A ticket closed, reopened, then closed again — throughput counts **each** close in its own period; backlog reflects the ticket as open again between reopen and re-close.
- **Worklog volume beyond inline limits**: An issue with more worklog entries than the source's inline page returns — all entries MUST still be captured so time invested is not silently undercounted.
- **Tickets with missing dimensions**: A ticket missing area, sub-area, pulse, or assignee — it is bucketed under an explicit "Unknown/Unassigned" group rather than dropped from totals.
- **Pre-inception timestamps**: A creation or worklog date before the week-1 anchor — surfaced as a pre-inception bucket (week ≤ 0) rather than silently merged into week 1.
- **Multi-sprint tickets**: A ticket whose sprint field lists more than one pulse — attributed to its latest (most recent) pulse (FR-012).
- **No worklogs / never closed**: A ticket with no worklogs contributes zero time but still counts in intake and backlog; a ticket worked but never closed contributes time and backlog but not throughput.
- **Interrupted sync**: A sync that fails partway — a re-run completes and produces no duplicate tickets, changelog rows, or worklog entries (idempotent).
- **Source unavailable at view time**: Jira/Tempo unreachable during user interaction — the dashboard still renders from the local datastore (sync-then-read).

## Requirements *(mandatory)*

### Functional Requirements

**Data foundation (sync-then-read)**

- **FR-001**: System MUST source all ticket data from the company Jira ISReq project and act as a read-only observer — it MUST NOT create, edit, transition, comment on, or delete anything in the source systems.
- **FR-002**: System MUST serve every view from a local datastore populated by a scheduled background sync, and MUST NOT contact the Jira or Tempo API during a page render or user interaction.
- **FR-003**: The sync MUST be incremental (fetch only tickets changed since the last successful sync) while still capturing complete priority and status changelog history for each changed ticket, and MUST be idempotent (safe to re-run on a timer without duplicating or corrupting data).
- **FR-004**: System MUST capture the complete set of worklog entries for each ticket, not a truncated inline subset, so time-invested totals are not silently undercounted.
- **FR-005**: System MUST record and surface data freshness (the timestamp of the last successful sync) so every view states how current it is.

**Highest analysis (north star)**

- **FR-006**: System MUST present, as the primary view, a direct comparison of the rate at which tickets become Highest priority versus the rate at which Highest tickets are closed, over time.
- **FR-007**: A ticket MUST count toward "became Highest" if it was created at Highest priority OR was raised to Highest after creation; a raise-to-Highest MUST be detected from priority-change history and counted in the period of the transition, not the period of creation. Each transition into Highest is a distinct **entry event**: a ticket raised to Highest more than once (raised → dropped → raised again) is counted at each transition, in each transition's period.
- **FR-008**: Whether a ticket held a given priority during a given period MUST be determined from reconstructed point-in-time priority history, never from the ticket's current priority.
- **FR-009**: The Highest view MUST make it immediately legible whether Highest tickets are accumulating (creates outpacing closes), e.g. via a net/cumulative Highest backlog over time. A ticket **exits** the Highest set when it drops below Highest OR is closed; the net Highest backlog MUST equal cumulative entry events minus cumulative exits, which MUST equal the live count of tickets that are Highest and open at each period end.

**Cadence and scope (cross-cutting)**

- **FR-010**: Every time-series view MUST support two cadences — weekly and per-pulse — and the user MUST be able to switch between them.
- **FR-011**: A "week" MUST be numbered sequentially relative to a configured inception anchor date, not by ISO week: `week(t) = floor((t − anchor) / 7 days) + 1`, with week 1 being the anchor week.
- **FR-012**: A "pulse" MUST be read from the ticket's sprint field. When the sprint field lists more than one pulse, the ticket MUST be attributed to its latest (most recent) pulse.
- **FR-013**: Every analytical view MUST support two scopes — all ISReq tickets and the `ps5-blocker`-labeled subset — with identical metric definitions across both, and the user MUST be able to switch between them.

**Core queue metrics**

- **FR-014**: System MUST report intake volume as the count of tickets created per period, broken down by request area, sub-area, and region.
- **FR-015**: System MUST report throughput as the count of close events per period, where a "close" is any entry of a ticket into a configured closed-status set. Each entry into a closed status is counted in the period it occurred, so a ticket closed, reopened, and closed again is counted at each close (consistent with the "closed in period N" drill-down in FR-021).
- **FR-016**: System MUST report backlog as the count of tickets open at a point in time, computed at query time from creation, resolution, and reopen history (open = created on/before the point and not in a closed status as of that point; a reopened ticket counts as open again until it is re-closed).
- **FR-017**: System MUST report time invested as the sum of worklog time per period, bucketed by each worklog entry's `started` date (NOT the ticket's creation date), attributed at ticket and area level only.
- **FR-018**: Time invested MUST be labeled best-effort and dependent on logging discipline, MUST NOT be presented as authoritative effort, and MUST NOT be attributed to any individual person (synced worklogs are authored by the sync app, not the human logger).

**Traceability (drill-down)**

- **FR-019**: Every aggregate value shown (count, average, chart point, or summary) MUST be drillable to the list of underlying tickets.
- **FR-020**: Each drilled-down ticket MUST display its Jira key (`ISREQ-NNN`), title, and assignee display name.
- **FR-021**: Drill-down filters MUST mean exactly what their label says and MUST NOT be conflated — "created in period N" lists only tickets created in N; "closed in period N" lists tickets closed in N regardless of when they were created.

**Statistical honesty**

- **FR-022**: Wherever a time-to-close average is reported, the system MUST report mean, standard deviation, and coefficient of variation together, never the mean alone.
- **FR-023**: The system MUST state whether each statistic is sample- or population-based; the coefficient of variation MUST use the sample standard deviation (n−1) and be labeled sample-based.
- **FR-024**: The system MUST surface low sample sizes (flag or annotate buckets with few data points) rather than presenting a fragile average as solid.
- **FR-025**: The system MUST NOT smooth, round, or omit data in a way that distorts the create-vs-close comparison or flatters the hypothesis; presented numbers MUST follow the underlying data.

**Region**

- **FR-026**: System MUST derive region (AMER / EMEA / APAC) two distinct ways that MUST NOT be conflated or silently substituted: (a) creation-time-of-day analysis derives region from the ticket creation timestamp using configurable, EMEA-anchored time windows; (b) per-user counts derive region from a static user-to-region map.
- **FR-027**: Where a single reference timezone is needed to present data, the system MUST default to EMEA.

**Categorization and configuration**

- **FR-028**: System MUST identify PR/MP-review tickets by a configured title substring (e.g. `[PR/MP Review]`). These tickets MUST be **included** in all core intake / throughput / backlog / time metrics, and additionally tagged so the user can filter them in or out and view them as a distinct category. They MUST NOT be silently excluded from totals.
- **FR-029**: The following MUST be configurable without code changes: source base URL and project key, credentials, the week-1 anchor date, the fields carrying area / sub-area / pulse, the closed-status set, the region time-windows, the user-to-region map, the Highest priority name, the `ps5-blocker` label, and the PR/MP-review title substring.

**Security and data residency**

- **FR-030**: Credentials and tokens MUST come from environment or a secret store, never hardcoded or committed, and MUST never be printed; network egress MUST be limited to the Jira and Tempo APIs, and ticket data MUST NOT be sent to any third-party service.

### Key Entities *(conceptual — schema is an implementation concern)*

- **Ticket (Issue)**: a unit of work in the ISReq queue. Attributes: Jira key (`ISREQ-NNN`), title, creation time, resolution time, current status, current priority, assignee (account id + display name), request area, sub-area, pulse, labels.
- **Changelog Event**: a recorded transition of a field (notably priority and status) on a ticket at a time; the basis for point-in-time correctness and raised-to-Highest detection.
- **Priority Interval (derived)**: a span during which a ticket held a single priority, reconstructed from creation priority plus ordered priority changes; lets "was this Highest during period W" be a range lookup rather than a replay.
- **Worklog Entry**: time spent on a ticket, with a `started` date and a duration; bucketed by `started`. Its author is the sync app and MUST NOT be used for per-person attribution.
- **Label**: a tag on a ticket; the `ps5-blocker` label defines the secondary analysis scope.
- **User**: a person identified by account id, with a display name and a mapped region.
- **Week**: a period numbered sequentially from the inception anchor date.
- **Pulse**: a sprint period read from the ticket's sprint field.
- **Region**: AMER / EMEA / APAC, derived two distinct ways (creation-time windows; user-to-region map).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can determine, for any selected week or pulse, whether Highest tickets are being created faster than they are closed within seconds of opening the dashboard, with no manual configuration of the primary view.
- **SC-002**: 100% of aggregate values across all views are drillable to their underlying tickets, and every listed ticket shows key, title, and assignee.
- **SC-003**: For a ticket created below Highest and raised to Highest in a later period, the dashboard counts it in the "became Highest" figure of the transition period and in no earlier period (verifiable against a known fixture).
- **SC-004**: 100% of reported time-to-close averages are accompanied by standard deviation, coefficient of variation, and a sample/population label; no average is shown alone.
- **SC-005**: Every analytical view supports both cadences (weekly, per-pulse) and both scopes (all ISReq, `ps5-blocker`), and the `ps5-blocker` figures are a strict subset of the all-ISReq figures for the same period and metric.
- **SC-006**: Time-invested figures bucket each worklog by its `started` date, verifiable by a worklog logged in a later period against an earlier-created ticket contributing to the later period.
- **SC-007**: The dashboard renders all views with the source systems unreachable after a completed sync, confirming no per-render API dependency.
- **SC-008**: The last successful sync time is visible on the dashboard.
- **SC-009**: Re-running the sync produces no duplicate tickets, changelog rows, or worklog entries.

## Assumptions

- **Whole-dashboard scope**: This feature is the entire ISReq Analytics Dashboard, delivered as the prioritized stories above; US1 (Highest create-vs-close) is the north-star MVP and US2 (traceable drill-down) ships with it.
- **Source of truth**: Jira Cloud for the ISReq project is the source of truth. Time/effort flows into Jira native worklog (Tempo syncs into it). A single Jira API token (account email + token) covers issues, changelog, and worklog; no separate Tempo token is required.
- **Serving model**: Views are served from a local datastore populated by a scheduled sync; the dashboard is single-tenant internal tooling running on a private homelab, with egress limited to the Jira/Tempo APIs and no ticket data sent to third parties.
- **Closed definition**: "Closed" is defined by a configurable closed-status set (resolved 2026-06-12 to `{Closed, Done, Rejected}` — `Rejected` counts as a close; see contracts/config.md); the close timestamp is the first entry into that set.
- **Backlog at query time**: Resolution timestamps are trustworthy enough that backlog is computed at query time from created/resolved without a weekly snapshot table.
- **Region defaults**: Region time-windows are EMEA-anchored and configurable; EMEA is the default reference timezone because most users are EMEA.
- **PR/MP-review treatment (decided 2026-06-12)**: PR/MP-review tickets (title contains the configured `[PR/MP Review]` substring) are included in all core metrics and additionally tagged so they can be filtered in/out and viewed as a distinct category. See FR-028.
- **Multi-sprint tickets (decided 2026-06-12)**: A ticket whose sprint field lists multiple pulses is attributed to its latest (most recent) pulse. See FR-012.
- **Configuration to finalize before implementation (CONFIG GLOSSARY)**: source base URL; exact project key casing; week-1 anchor date; the fields carrying area / sub-area / pulse; the closed-status set; the region UTC windows; the user-to-region map (CSV of account_id → region, provided later); the Highest priority name; the `ps5-blocker` label casing; and the PR/MP-review title substring.
- **Runtime/UI technology deferred**: The user-facing technology (e.g. a single-page analytics app for speed vs a more polished multi-user UI) is an implementation decision for the planning phase and does not change any requirement here; it MUST be decided before `/speckit-tasks`.

## Dependencies

- Read access to the Jira Cloud REST API for the ISReq project (issues, changelog, worklog) via account email + API token.
- A persistent local datastore for synced data and a scheduler capable of running the sync job on a recurring timer.
- A maintained user-to-region mapping (provided as data, not code).
