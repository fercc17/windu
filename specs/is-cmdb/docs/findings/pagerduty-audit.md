# PagerDuty audit (issue #30)

Read-only audit of the live Canonical PagerDuty (`canonical.pagerduty.com`),
**2026-06-08**, using `PAGERDUTY_API_TOKEN` (read scope). Reproduce with
`python scripts/explore_pagerduty.py` (caches `/tmp/pd_explore.json`). All
paginated calls sleep 0.2 s between pages.

## Teams

The token sees **3 teams**. Of the two the handoff names:

| Team | ID | Present? |
|------|----|----------|
| IS | `PQ4ZG3S` | ✅ yes |
| IS 24x7 | — | ❌ **not found** with this token |

> **Gap:** "IS 24x7" does not exist (or is not visible to this token) in this
> account. Maintenance-window automation should treat the IS-24x7 team as
> optional / configurable rather than assume it exists.

## Services owned by IS (7)

| ID | Name | Status |
|----|------|--------|
| P0KBH6J | Batphone Alert | active |
| PCJEQ65 | is-pd-bot default iso policy | active |
| PUMA5CQ | is-pd-bot default webops policy | active |
| PGWVYGZ | k8s-is-cos-ps6-cos@is-bastion-ps6 | disabled |
| PP52CJW | Prometheus Alerts | active |
| PT6U7AJ | Site24x7 | active |
| PJ5D40R | Support to IS - Alert | active |

Most IS services are **alerting policies** (Batphone, Prometheus, Site24x7),
not per-environment services. Exactly one is named after an environment:
`k8s-is-cos-ps6-cos@is-bastion-ps6` (matches env `k8s-is-cos-ps6`).

## On-calls

11 on-call entries returned for IS (all escalation level 1). Sample escalation
policies: `axino-EscalationPolicy2`, `mm-pd-bot-EscalationPolicy`,
`mm-pd-bot-ci-EscalationPolicy`, `Site24x7 (Devops)`, `stg-is-prometheus`.
Several show `OFF` as the current on-call user.

## Maintenance-window schema

`GET /maintenance_windows` objects carry:

```
id, type, summary, self, html_url, sequence_number,
start_time, end_time, description, services[], teams[], created_by
```

Live sample: *"Expired Certificates"*, `2026-06-05 → 2026-06-08`, on service
`P98PWY2` (*Bootstack Alerts - Prodstack 8*). So MWs are scoped to **services**.

## Environment / cloud → service mapping strategy

> **Correction (2026-06-10):** an earlier draft of this section recommended the
> `Bootstack Alerts - Prodstack N` services. Those belong to a **different team**
> and are **not** the right target. Maintenance-window automation targets the
> **IS team** (`PQ4ZG3S`,
> <https://canonical.pagerduty.com/teams/PQ4ZG3S/users>) and its services.

The IS team owns the 7 services listed above. They are **account/team-wide
alerting pipelines** (Batphone, Prometheus, Site24x7, Support to IS, the
is-pd-bot policies), **not** per-cloud or per-environment services — the only
env-specific one (`k8s-is-cos-ps6-cos@is-bastion-ps6`) is disabled. So there is
**no per-cloud granularity**: a maintenance window silences an IS service
*team-wide*, not "ps7 only".

**Resolution rule** (used by #32/#33): a maintenance window on any IS-managed
environment/node silences the following IS-team services (configurable, default
set chosen 2026-06-10):

| PagerDuty service | Service ID |
|-------------------|-----------|
| Batphone Alert | `P0KBH6J` |
| Support to IS - Alert | `PJ5D40R` |
| Site24x7 | `PT6U7AJ` |

(`Prometheus Alerts` `PP52CJW` was deliberately excluded.) Store these as a
configurable default (e.g. `PAGERDUTY_MW_SERVICE_IDS`) resolved at
window-creation time, rather than per-environment, since the granularity does
not exist in PagerDuty.

Match attempt summary: 1163 distinct juju model tokens, **0** matched an IS
service name directly; 1 environment name (`k8s-is-cos-ps6`) matched a service —
confirming per-environment mapping is not viable and the team-wide service set
above is the correct unit.

## Writing maintenance windows requires a write token

The current token is **read-only**. Create/cancel needs a write-capable token
(`PAGERDUTY_WRITE_TOKEN`, added later — see #33). Target call shapes:

```http
POST https://api.pagerduty.com/maintenance_windows
Authorization: Token token=<PAGERDUTY_WRITE_TOKEN>
Content-Type: application/json
Accept: application/vnd.pagerduty+json;version=2
From: <requester-email>

{ "maintenance_window": {
    "type": "maintenance_window",
    "start_time": "2026-06-10T02:00:00Z",
    "end_time":   "2026-06-10T06:00:00Z",
    "description": "IS-CMDB: node ps8-... maintenance",
    "services": [ {"id": "P98PWY2", "type": "service_reference"} ]
} }

DELETE https://api.pagerduty.com/maintenance_windows/{id}      # cancel
```
