# IS Operations Alert Analytics

## Measuring the reactive alert load as its own analysis, hosted in the same app as ISReq

Status: Proposal, open for review
Type: Product Requirement
Author: F. Carrillo, Site Reliability Engineer, Canonical IS
Scope: Canonical IS SRE
Date: June 2026
Parent of: a child Implementation spec for the PagerDuty connector and schema
Depends on: the ISReq analytics dashboard, as the shared platform and shell
Closes:

## Abstract

IS operations run on two streams of work: reactive alerts handled through PagerDuty, and planned request work tracked in ISReq. The analytics platform measures only the request stream today, so IS has no durable situational awareness of the reactive load, how many pages it takes, how fast they are answered, who carries them, and where they concentrate. This spec specifies ingesting PagerDuty read-only and presenting it as its own analysis in the same app, a single operations console for a team running at scale, with no requirement that the two data sets be joined.

## Rationale

The ISReq dashboard turned request work into trend: 3,016 tickets and 926 hours between 9 February and 17 June 2026, broken into intake, throughput, backlog, and cycle times. It answers, for the request stream, how much work, how fast, and where. Nobody answers the same questions for the reactive stream in a durable, trended way. PagerDuty shows the live state and a few of its own reports, but the on-call load IS carries over a quarter, whether it is getting noisier, and which services drive it, are not measured anywhere the team already looks.

The value here is one console, not one number. IS does not need the two streams correlated to benefit from measuring both. It needs the same situational awareness for alerts that it already has for requests: volume and trend, response times, on-call burden, and the noisy surfaces. Putting that in the same app means one place to look, one design language, and one platform to maintain, rather than standing up and learning a second tool. The two analyses sit side by side and stay independent.

The platform makes this cheap because it is source-agnostic below the connector. The dashboard is sync-then-read: a read-only sync writes a source into PostgreSQL, an audited metrics layer reads it, and an API serves it without calling the source at render. PagerDuty is a second independent source on that pattern, landing in its own schema, touching neither the ISReq schema nor its metrics. The two analyses are co-tenants of one app, deliberately decoupled, which also means the alert analysis can ship and change without any risk to the audited ISReq numbers.

Running operations at scale, a team that cannot see its alert load over time cannot tell whether it is getting louder, whether a rotation is overloaded, or whether last quarter's fixes actually reduced pages. Measuring it is the precondition to elevate the maturity of how IS manages on-call, the same move the ISReq dashboard made for request work, from anecdote to evidence.

## Specification

### Scope and constraints

The platform ingests PagerDuty through its REST API v2 with a read-only token, under the rules the ISReq constitution already sets: read-only on the source, sync-then-read so no source call happens at render, and an additive-only schema. PagerDuty data lands in its own `pd` schema, separate from `isreq`.

PagerDuty is presented as its own analysis, its own navigation group and its own pages, in the same app. No join to ISReq is in scope. The two analyses share the application shell, the chart and table components, and the sync-then-read platform, not their data.

In scope for the first iteration: incidents and their timeline, services, escalation policies, teams, and on-call assignments. Out of scope for the first iteration: raw per-alert event payloads, change events, and automation actions, all of which can be added later on the same pattern.

### App structure

The app hosts independent analyses. ISReq is the first; PagerDuty is the second, with its own navigation group, its own pages, and its own schema. They share the FastAPI and SPA shell, the rendering components, and the platform, and nothing else. There are no shared keys and no cross-queries, which keeps the audited ISReq layer untouched and lets the alert analysis evolve on its own cadence. This is the answer to the naming question the ISReq-only framing left open: the app is an IS operations console that hosts multiple operational analyses, of which ISReq and PagerDuty are the first two.

### Data model

The PagerDuty entities to sync, and what each one is for.

| Entity | Holds | Used for |
|---|---|---|
| incidents | created, acknowledged, resolved times, urgency, service, current assignees | volume, MTTA, MTTR, urgency mix |
| log entries (incident timeline) | trigger, acknowledge, escalate, resolve, notify events with timestamps | MTTA and MTTR derivation, escalation depth, notification load |
| services | name, owning team, escalation policy | noisy-service Pareto |
| escalation policies, teams | escalation structure and ownership | on-call burden by team |
| users, on-calls | who is on call and when | on-call burden by person, interrupt distribution |

Derived measures: MTTA as trigger to first acknowledge, MTTR as trigger to resolve, interrupt count per person, and an after-hours share computed against the same region time-of-day windows the ISReq region metrics already use.

### Alert metrics

The pages mirror the existing ISReq pages so the two analyses read the same way, on the same pulse and weekly cadence.

- Incident volume per period, split by urgency.
- MTTA and MTTR per period, with the same average, deviation, and coefficient-of-variation treatment the cycle-times page uses.
- Escalation rate, the share of incidents that escalate past the first responder.
- Noisy services, a Pareto of incident count and acknowledged time by service.
- On-call load, incidents and after-hours pages per person and per team.
- After-hours share by region window, reusing the ISReq region logic.

### Delivery shape

Thin first, anchored to milestones rather than dates, each milestone independently useful.

- Milestone 1: the read-only PagerDuty connector and the `pd` schema, syncing incidents, timelines, services, and on-calls.
- Milestone 2: the alert metrics, pages, and the navigation group that makes them a distinct analysis in the app.
- Milestone 3, optional and later: deeper cuts such as per-service detail, escalation paths, and on-call fairness.

### Open Issues

| Issue | Owner | Status |
|---|---|---|
| Classification. This is written as a Product Requirement because it specifies what the analytics must provide. The connector, schema, and metric definitions are Implementation and belong in a child spec for James Simpson's technical co-sign. Confirm the split. | F. Carrillo, J. Arregui | Open |
| PagerDuty read access. A read-only API token, the account, and the in-scope services and teams must be provisioned before Milestone 1. Rate limits on the REST API need to be confirmed against the incident volume. | F. Carrillo, IS SRE | Open |
| Product identity. Adding alerts makes the app a multi-analysis operations console. Confirm renaming it from the ISReq dashboard to IS Operations Analytics, with ISReq and PagerDuty as co-tenant sections. | F. Carrillo | Open |
| On-call hours. A page-to-hours figure needs an interrupt-cost constant, the context-switch minutes per page. Count-based metrics need no constant, so this is only required if an on-call hours measure is wanted. | IS SRE | Open |
| Out of scope by decision. Any ISReq to PagerDuty correlation, a reactive versus planned view, is deliberately excluded here. If it is ever wanted it is a separate spec, and it would carry its own load-bearing service-to-area mapping risk. | F. Carrillo | Open |
| Routing. J. Arregui must review before this reaches Pierre. Do not route directly to Pierre. | F. Carrillo | Open |

## Further Information

Method. PagerDuty data is read through the REST API v2 with a read-only token, under the same sync-then-read and additive-schema rules the ISReq constitution sets. MTTA and MTTR are derived from incident log entries.

Alternatives considered. An integrated analysis, correlating alert load against ISReq intake and backlog into a reactive versus planned view, was considered and deferred. It depends on a clean mapping from PagerDuty services to ISReq areas that does not exist yet, and the standalone alert analysis delivers value without it. Co-hosting two independent analyses captures most of the benefit, one operations console, at a fraction of the risk.

Related work. The ISReq analytics dashboard is the first analysis in this console and the shared platform this spec builds on. The IS Operations Automation spec addresses the planned-work side of operations. This spec adds the reactive side as its own view in the same app.

## Spec History and Changelog

| Date | Author | Change or Meeting Notes |
|---|---|---|
| 2026-06-21 | F. Carrillo | Initial draft. PagerDuty scoped as a standalone analysis co-hosted with ISReq in one operations console, with no data integration between the two. An integrated reactive versus planned cross-analysis was considered and deferred to a possible future spec, removing the service-to-area mapping risk from this one. |
