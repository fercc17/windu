# CAB / Change Management — Design Proposal

**Status:** Proposal / exploratory. Not yet scheduled for implementation.
**Revision:** rev 4 (2026-06-23) — defines the explicit **CR status enum** and
state machine (§8): numbered approval levels with **"Awaiting Lx"** naming, plus
`submitted`, `verifying`, `applied`, `closed`, `expired`, and a separate `outcome`
field. rev 3 added the resilience model (§9.4). rev 2 added regional windows,
multi-target CRs, the approval chain, re-approval-on-edit, Canonical IDP identity,
and Google Calendar integration.
**Audience:** IS SRE team + the consuming teams (stakeholders).
**Scope of this doc:** data model, roles & identity, the approval system,
lifecycle, impact engine, and notifications for adding **Change management (a
CAB)** to IS-CMDB. It deliberately does not prescribe UI screens in detail.

---

## 1. Purpose & scope

Turn IS-CMDB from a read-only *"what exists / what's live"* tool into the system
that answers **"what is about to change, who does it break, and who do we tell?"**

IS-CMDB's differentiator is that it already knows the topology — placement,
dependencies, ownership, resilience — so it can **compute impact and stakeholders
automatically** instead of asking a human to list them. The design leans into that
and keeps approval thin.

### In scope
- A **system of record and coordination** for changes (a CR = Change Request).
- An **impact analysis engine** over juju models / nodes / switches / clouds.
- An **approval system** with identity (Canonical IDP) and a regional approval chain.
- A **notification + calendar engine** (Mattermost/email/PagerDuty + Google Calendar).
- **Both** infrastructure changes (node/switch/cloud takedowns) **and application
  changes** (juju-model deploys). *(Decision Q3 = both.)*

### Non-goals
- **It does not execute commands.** Execute/verify/rollback commands are a stored
  **runbook**; the SRE runs them via existing tooling (Juju / Terraform / CI /
  kubectl). The CMDB records *that they ran and the outcome*. (See §13.)
- It is **not a full ITSM** workflow engine.

---

## 2. Design principles

1. **Impact-first.** Affected-services and stakeholder lists are *computed*, never hand-entered.
2. **Don't execute — record.** Commands are runbook fields + recorded outcomes.
3. **Fail cautious on resilience.** Unknown resilience is treated as **non-resilient**.
4. **Thin approval, rich impact.** Complexity goes into impact + comms.
5. **Reuse existing primitives** (`MaintenanceWindow`, blast-radius CTE, `consumed_by`, resilient signal, notification channels).
6. **Auditable & immutable.** A submitted CR is append-only history; edits create a new version (§8.3).
7. **Completeness gate.** No CR is *submitted* without a maintenance window, an assigned executer, and execute + verify + rollback commands.

---

## 3. Reuse map — exists vs. new

| Concept | Already in IS-CMDB | New work |
|---|---|---|
| Target = node / cloud / environment | `maintenance.MaintenanceWindow` 3-scope model | extend to **multi-target** + **switch** (§6) |
| Maintenance window | `MaintenanceWindow` (+ PagerDuty silence) | **regional standard MWs** (§7) |
| "What runs on the node" | `Node.primary/secondary_environments` + Redis placement | — |
| Blast radius | recursive CTE over `EnvironmentDependency` | reuse for impact set |
| Switch → nodes | netbox cable graph (issues #39/#40, switch-impact query) | use for switch targets |
| Stakeholders | `Environment.consumed_by` / `consumer_team` | group + notify + approve |
| Resilient vs not | `resilient_env_names()` (coarse: gitops + >3 VMs + >1 node) | **tiered, per-fault-domain model** (§9.4) |
| Notification channels | `maintenance.MaintenanceNotificationChannel` | message variants + calendar |
| Change outcome → DORA | `dora` change-failure-rate | rolled-back CR = change failure (§12) |
| Identity | **none today** | **Canonical IDP + groups** (§10) |

The genuinely new surface: the `Change` object + runbook/assignee/type, the
**approval chain**, the **risk score**, **identity/authZ**, and the **Google
Calendar** hook.

---

## 4. Change taxonomy

| Type | When | Approval | Examples |
|---|---|---|---|
| **Standard** | Pre-approved, repeatable, low-risk; matches a registered **template** whose guardrails hold | **No CAB gate** (peer-ack); stakeholders informed | rolling reboot of a resilient node; cert rotation |
| **Normal** | Anything else; risk-assessed | The full **approval chain** (§9), consumer **last** | take down a ps6 node hosting prod; juju model migration; switch maintenance |
| **Emergency (eCR)** | Urgent (outage mitigation, security fix) | **single tech-lead approver pre-exec**; consumers notified; **mandatory PIR** + retroactive review | emergency failover; hotfix during an incident |

Auto-upgrade: a "standard" CR whose computed impact violates its template
guardrails (e.g. a non-resilient service appears) is **reclassified to normal** and
routed through the chain.

---

## 5. Roles — "the full team"

Roles map to **Canonical IDP groups** (§10). Some parties are computed (consumers),
some assigned.

| Role | Who may fill it | IDP group | Responsibility |
|---|---|---|---|
| **Proposer / requester** | **anybody** (any authenticated IDP user) | *(all users)* | raises the CR, writes description + runbook |
| **Executer (implementer)** | an **SRE of the CR's region**; assigned by the **proposer** or **IS leadership** | `sre-<region>` (e.g. `sre-apac`) | runs the runbook during the MW; marks start / verify / outcome |
| **Peer reviewer** | **any SRE** | `sre` | technical sanity-check of the runbook |
| **Tech-lead approver** | a **small reserved set — 3 tech leads** (or a **product owner** for special cases) | `cab-tech-leads` (+ `product-owners`) | tech-side go/no-go |
| **Change manager** | a **regional SRE manager**, or the **global director** | `sre-manager-<region>` (+ `global-director`) | CAB sign-off for normal changes |
| **Consumer approver** | members of each **impacted consuming team** (`consumed_by`) | the consuming team's group | **last** approval; absorbs/accepts the impact |

Assignment rules:
- **Anybody proposes.** The **executer** is set by the proposer *or* by **IS
  leadership** (`is-leadership` group) — leadership can (re)assign.
- **Executer must match the CR's region** (`sre-apac` for an APAC change, etc.).
- **Change manager must be the region's SRE manager** (global director may always step in).

---

## 6. The `Change` (CR) object — data model

Proposed new app `cmdb/apps/changes/`.

### 6.1 `Change`
- `id` (UUID), `reference` (e.g. `CHG-2026-0042`)
- `title`, `description`
- `change_type` — `standard | normal | emergency`
- `temperature` (UI: **"Service impact"**) — `cold | hot`, shown as **"Outage (downtime)"** /
  **"Live (no downtime)"**. A dimension **independent** of `change_type`. Only **outage (cold)**
  changes can be staged ahead (see `staging_notes`).
- `status` — CR status enum + state machine (§8); execution *result* in `outcome` (below)
- `region` — `amer | emea | apac` (derived from targets; drives MW + executer + change-manager)
- **`version`** (int) — increments on any post-submit edit; invalidates approvals (§8.3)
- `maintenance_window` — FK `maintenance.MaintenanceWindow` (**required to submit**)
- **People:** `proposer`, `executer` (**required**), `peer_reviewer`
- **Runbook (stored, never executed) — all four required to submit:**
  - `precheck_commands`, `execute_commands`, `verify_commands`, `rollback_commands`
- `staging_notes` (optional) — configuration that can be staged ahead of time without
  applying the change; **cold changes only** (rejected on a `hot` change).
- **Risk:** `risk_score` (int), `risk_tier` (`low | medium | high`) — computed (§9)
- **Timing:** `created_at`, `submitted_at`, `approved_at`, `started_at`, `completed_at`, `estimated_duration`
- **Outcome:** `outcome` (`success | rolled_back | failed | partial | n/a`), `pir_notes`
- **Calendar:** `gcal_event_id` (set when the calendar event is created, §11.2)
- `freeze_override` (bool + justification) — only to proceed during a freeze window (§14)

### 6.2 `ChangeTarget` — **multi-target** (a CR can affect several things)
One row per target; a CR has **1..N** targets, and **may list multiple juju
models**.
- `change` (FK)
- `target_type` — `juju_model | node | switch | cloud`
- reference (exactly one, by type):
  - `environment` (FK `environments.Environment`) for `juju_model`
  - `node` (FK `netbox.Node`) for `node`
  - `switch` (FK to the switch entity from the netbox cable graph) for `switch`
  - `cloud` (CharField slug) for `cloud`
- All targets of a CR should resolve to **one region**; a cross-region selection is
  rejected (split into per-region CRs) so the regional MW / SRE / manager are
  unambiguous.

### 6.3 `ChangeAffectedEnvironment` (computed impact set — snapshot)
- `change` (FK), `environment` (FK)
- `impact_type` — `direct` (runs on a target) | `dependency` (downstream)
- `dependency_depth` (int)
- `resilience_tier` (snapshot: `none|instance|node|rack|cloud`) +
  `survives_change_domain` (bool — vs *this* change's fault domain) +
  `recoverability` (`auto|gitops|manual`) + `resilience_basis` (text) — see §9.4
- `consumer_team` (snapshot)
- `notified_at`, `acknowledged_at`, `ack_by`

### 6.4 `ChangeApproval` (one row per required approval, **ordered**)
- `change` (FK), `version` (the CR version this approval is for)
- `level` (int — approval **level**: L1=peer, L2=tech_lead, L3=change_manager (high-risk only), L4=consumer (always **last**)) — position in the chain
- `role` — `peer | tech_lead | change_manager | consumer`
- `party` — IDP group / consuming-team slug
- `decision` — `pending | approved | rejected | blocked_date | acknowledged`
- `decided_by`, `decided_at`, `comment`, `proposed_alternative_date` (consumer veto, §11.1)
- Rows are generated from `change_type` + `risk_tier` + impact set on submit.

### 6.5 `ChangeNotification` (reuse `MaintenanceNotificationChannel` shape)
- `change`, `channel` (`pagerduty | mattermost | email | cos | gcal`),
  `recipient`, `variant` (`resilient_blip | non_resilient_engineer | info | calendar_invite`),
  `sent_at`, `success`, `error_message`

### 6.6 `ChangeTemplate` (standard changes)
- `name`, `description`, `auto_approve` (bool), guardrails (`requires_all_resilient`,
  `max_nodes`, `allowed_target_types`, `allowed_env_types`, `allowed_clouds`),
  `default_*_commands`. **Owned/edited by IS management** (`is-management` group). *(Decision Q4.)*

---

## 7. Maintenance windows — **per region**

Three standing **regional standard MWs**: **APAC**, **EMEA**, **AMER**. *(New
requirement.)*
- Each region has a recurring standard window; a CR's `region` selects the
  applicable standard MW automatically.
- A **standard** change attaches to its region's next standard MW with no extra
  approval; a **normal** change picks the standard MW or proposes a bespoke one.
- MW required to submit (completeness gate). On entering *in_progress*, the linked
  MW drives the existing PagerDuty/COS silence.
- **Past windows are emergency-only.** A standard/normal CR must schedule a
  **future** window; only an **emergency** CR may use a window in the past
  (retroactive recording of an already-executed mitigation).
- **Minimum window length.** The window must end **at least 1 hour after** it starts.
- **Conflict detection**: the CMDB knows placement, so it flags two CRs targeting
  overlapping nodes/switches/clouds in overlapping windows, and MW collisions with
  freeze windows (§14).

---

## 8. Lifecycle / state machine

### 8.1 Status enum (canonical)

`Change.status` takes one of the values below. **Naming rule:** an in-review status
is named **`awaiting_l<n>`** — it means the CR is *waiting for* level *n* to sign,
**not** that level *n* has signed. (We avoid "pending Lx", which reads ambiguously:
already-L1-approved, or waiting-for-L1?) There is a single terminal **`approved`**
once *all* required levels clear; the per-level detail lives in the `ChangeApproval`
rows. `status` is *where the CR is in the flow*; the *result* of execution lives in
the separate `outcome` field (§6.1).

| Status | Display | Meaning |
|---|---|---|
| `draft` | Draft | being authored; not yet submitted |
| `submitted` | Submitted | completeness gate passed; impact + risk computed, approval chain generated |
| `awaiting_l1` | Awaiting L1 — Peer | waiting for peer (any SRE) sign-off |
| `awaiting_l2` | Awaiting L2 — Tech Lead | waiting for a tech lead (or product owner) |
| `awaiting_l3` | Awaiting L3 — Change Manager | **high-risk only** — regional SRE manager / global director |
| `awaiting_l4` | Awaiting L4 — Consumer | impacted consuming team(s); **always last** |
| `approved` | Approved | every required level cleared; not yet scheduled |
| `scheduled` | Scheduled | approved + MW in the future; calendar event created |
| `in_progress` | In Progress | executer is running the runbook inside the MW |
| `verifying` | Verifying | executed; running the verify step |
| `applied` | Applied | verify passed; the change is in place |
| `closed` | Closed | filed after PIR (PIR mandatory for emergency / rolled-back / failed) |
| `rejected` | Rejected | an approver declined (terminal) |
| `cancelled` | Cancelled | the proposer withdrew it (terminal) |
| `blocked` | Blocked | a consumer blocked the date (must propose an alternative, §11.1) → reschedule |
| `expired` | Expired | the MW elapsed without execution → reschedule or cancel |
| `rolled_back` | Rolled Back | executed, verify failed, reverted; feeds DORA change-failure (§12) |

`status` answers *where in the flow*; the **separate `outcome` field**
(`success | rolled_back | failed | partial | n/a`, §6.1) answers *with what result* —
keeping the two apart stops the success/rollback/partial combinations from
exploding the status enum.

**Approval level → role** (a level is skipped when the CR's type/risk doesn't need it):

| Level | Role | Required for |
|---|---|---|
| **L1** | Peer (any SRE) | normal (all risk); optional for emergency |
| **L2** | Tech lead (3 leads / PO) | normal (all risk); **required pre-exec** for emergency |
| **L3** | Change manager (regional SRE mgr / global director) | **normal high-risk only** |
| **L4** | Consumer (impacted teams) | when the impact set is non-empty; **always last** |

A **standard** change skips L1–L4 (peer-ack only). A **normal low/medium** change
runs L1 → L2 → L4 (no L3). A **normal high** change runs L1 → L2 → L3 → L4. The
number is the role's fixed level (not a contiguous counter), so a skipped L3 is
itself a signal that the change was below the high-risk bar; **L4 (consumer) is
always the final gate**.

### 8.2 Transitions

```
draft ──submit (completeness gate)──▶ submitted ──impact+risk+chain──▶ awaiting_l1
  awaiting_l1 ─▶ awaiting_l2 ─▶ [awaiting_l3: high-risk] ─▶ awaiting_l4 ─▶ approved
  approved ──(MW future)──▶ scheduled ──(MW start)──▶ in_progress ──execute──▶ verifying
  verifying ──pass──▶ applied ──PIR / file──▶ closed
  verifying ──fail──▶ rolled_back ──(mandatory PIR)──▶ closed

branches:
  any awaiting_* ──approver declines──────────────▶ rejected     (terminal)
  draft | submitted | awaiting_* ──proposer pulls──▶ cancelled   (terminal)
  awaiting_l4 ──consumer blocks date (+ alternative)──▶ blocked ──reschedule──▶ (re-submit, version++)
  scheduled ──MW elapsed, not run─────────────────▶ expired ──reschedule──▶ scheduled | cancelled
  any post-submit edit ───────────────────────────▶ version++, chain resets to awaiting_l1 (§8.3)

emergency: draft ──▶ approved (single tech-lead L2, pre-exec) ──▶ scheduled ──▶ in_progress
           … ──▶ applied | rolled_back ──▶ closed   (PIR mandatory)
```

**Gates:**
- *draft → submitted*: **completeness gate** (MW + executer + execute/verify/rollback); impact + risk computed and the approval chain generated.
- approval advances **in level order**, **consumer (L4) last**; all required levels approved → *approved*.
- *approved → scheduled*: triggers the **Google Calendar event** (§11.2).
- *scheduled → in_progress*: now within the MW; executer marks start. If the MW elapses first → *expired*.
- *in_progress → verifying → applied* on a passing verify; *verifying → rolled_back* on failure (+ mandatory PIR).
- *applied / rolled_back → closed*: PIR filed (mandatory for emergency, rolled-back, or failed).

### 8.3 Re-approval on edit (**rule**)
**Any change to a submitted CR restarts the entire flow.** Editing bumps
`change.version`; all `ChangeApproval` rows for prior versions are invalidated and a
fresh chain (L1 peer → L2 tech-lead → L3 change-manager → L4 consumer) is generated,
resetting `status` to `awaiting_l1`. This prevents "approve, then quietly alter the
runbook/target/window." The prior versions remain in the immutable audit trail.

## 9. Impact engine + risk + the approval chain

### 9.1 Impact computation (on submit, snapshotted)
Union across **all** targets:
- **juju_model** → that environment (and you may list several).
- **node** → `node.primary/secondary_environments` (+ Redis live placement).
- **switch** → nodes attached to the switch (netbox cable graph) → their environments.
- **cloud** → environments where `cloud == target`.
Then **downstream**: recursive blast-radius CTE over `EnvironmentDependency` to
depth N. Snapshot `resilient`, `consumer_team`, `criticality_tier`, `env_type`,
`slo_rto` per affected env.

### 9.2 Risk score (computed; tunable like the DORA thresholds)
| Signal | Contribution |
|---|---|
| Any affected env `env_type = prod` | +3 |
| Max `criticality_tier` (1 / 2 / 3) | +4 / +2 / +1 |
| Each **non-resilient** affected service | +2 (capped) |
| Blast-radius depth | +1 / level |
| Affected-env count (1 / 2–5 / 6+) | +0 / +1 / +2 |
| Target = whole cloud or a switch | +3 |
**Tiers:** `low` (<4) · `medium` (4–8) · `high` (>8).

### 9.3 The approval chain (ordered; **consumer last**) — the steps are the approval **levels** L1–L4 (§8.1)

| Step | Role | Standard | Normal (low/med) | Normal (high) | Emergency |
|---|---|---|---|---|---|
| 1 | **Peer** (any SRE) | ack | required | required | optional |
| 2 | **Tech lead** (3 leads / PO) | — (auto) | required | required | **required (pre-exec)** |
| 3 | **Change manager** (regional SRE mgr / global director) | — | — | **required** | retroactive |
| 4 | **Consumer** (impacted consuming teams) | informed | **ack** (non-resilient) | **block-capable** (non-resilient) | informed |
| — | **PIR** | — | on failure | on failure | **mandatory** |

- Approval is **sequential**: a CR doesn't reach the consumer until peer + tech-lead
  (+ change-manager for high risk) have approved — consumers approve **last**, on a
  near-final change.
- A **product owner** may stand in for the tech lead on special cases.
- The **global director** may approve in the change-manager slot for any region.

### 9.4 Resilience model — *what "resilient" means*

Resilience drives the entire notification fork (§11.1: "brief blip" vs "ready an
engineer"), so it must be principled, fail-cautious, and show its reasoning.
Today `redis_client.resilient_env_names()` is a coarse boolean (**GitOps-managed +
>3 live VMs + spread across >1 node**). The model below replaces it.

**Two framings:**

1. **Per fault-domain, evaluated against the change's target.** Resilience is not a
   global flag — it is *"survives loss of \<fault domain\>"*. The CAB evaluates each
   affected env against the **specific fault domain this change removes** (the node /
   switch / rack / cloud being touched), using placement + the switch graph +
   `host_aggregate`. The *same* env can be node-resilient but not switch-resilient,
   and the notice reflects *this* change. (3 VMs on 3 nodes all behind one switch are
   dead when that switch goes.)
2. **Resilience ≠ recoverability.** Resilience = survives the fault **automatically**
   (the blip). Recoverability = how fast/automatically you **rebuild** after it dies.
   **GitOps belongs to recoverability, not resilience** — it doesn't prevent the
   outage, it speeds the rebuild. They're surfaced **separately**, so a
   non-resilient-but-GitOps env ("recovers via reconcile in ~X") is distinguished
   from a non-resilient-manual one ("engineer required").

**Stateless vs stateful** (the data tier is the usual real SPOF):
- *Stateless tier* — ≥N backends across distinct fault domains behind a (redundant)
  load balancer → resilient.
- *Stateful tier* — needs **replication + automatic leader election/failover**
  (Patroni / MySQL group replication / etc.). Replicas behind a single-primary DB
  with no auto-failover are **not** resilient, however HA the web tier looks.

**Resilience scorecard** (builds on the proposed criteria — ≥3 backends +
auto-failover + HAProxy + GitOps — plus the gaps):

| Criterion | Why it matters | Computable from CMDB today |
|---|---|---|
| ≥3 backends across **≥3 distinct nodes** (anti-affinity), **odd for quorum** | survive losing the targeted node *and* keep majority | ✅ placement (`juju_units` / hosts) — partly today |
| Spread across **rack / switch** (ideally **cloud**) | survive the actual fault domain of the change | ✅ switch graph (#39/#40) + `host_aggregate` |
| **Stateful tier**: replication + **auto-failover** | the real SPOF | ⚠️ infer from charm (patroni, mysql-router…) — needs a charm→failover map |
| **Redundant load balancer** (HAProxy not a SPOF; ≥2 / VIP) | the LB itself | ⚠️ count haproxy units / VIP — partial from `charm_versions` / `services` |
| **Critical dependencies also resilient** (weakest-link) | HA on a SPOF dependency isn't HA | ✅ recurse `EnvironmentDependency` |
| **Capacity headroom (N+1)** | survivors must absorb the load or cascade | ⚠️ quotas vs real utilisation (partial) |
| **Health checks + auto-ejection** | failover must trigger fast | ❌ config inspection / review |
| **Failover actually tested** | "configured" ≠ "works" | ❌ review-only |

**Tiered classification** (not a boolean):

| Tier | Meaning | Survives |
|---|---|---|
| `none` | single instance / no redundancy | nothing |
| `instance` | ≥N backends but co-located | a process crash, **not** a node |
| `node` | backends across **≥3 nodes** | one node *(≈ today's signal)* |
| `rack` / `switch` | across racks/switches | a switch/rack |
| `cloud` | present in **≥2 clouds** | a cloud |

The CAB compares the **change's fault domain** to the tier: a *node* takedown against
a `node`+ env → **blip**; against an `instance`/`none` env → **engineer**. A *switch*
takedown needs `switch`+; a *cloud* takedown needs `cloud`.

**Confidence & the reviewed override** (fail cautious):
- Compute the tier from the ✅/⚠️ signals; **default unknown / low-confidence to
  non-resilient** — a false "you're fine" is the worst outcome.
- An **explicit reviewed override** — a per-env, audited flag with a recorded
  **basis** — covers what the CMDB can't see (tested failover, health-check quality).
  An SRE can confirm or *downgrade*; the override + author live in the audit trail.
- Every verdict carries its **basis** (e.g. "3 units / 3 nodes / 2 switches; Patroni
  primary+2 replicas"), shown in the §11.1 notice so consumers can dispute it.

**Per-tier rollup to the worst tier (decided).** Resilience is assessed **per tier
(web / data / LB)** and the env's **effective tier is the worst**
(`effective = min(tier)` across its tiers) — the data tier is the usual hidden SPOF,
so a single rollup would mask it. The §11.1 notification fork uses this effective
tier (compared against the change's fault domain). Each tier keeps its own
`resilience_basis` so the notice can name *which* tier is the weak link.

---

## 10. Identity & authorization — **Canonical IDP**

*(Decision Q1.)* Identity comes from **Canonical IDP** (the Identity Platform —
there is a charm; relates to issue #127). We will **create IDP user groups** for
the CAB roles and map them to the team slugs already in the data
(`Environment.iam_groups`, `team`/`owner`, `consumer_team`).

Groups to create:
- `sre-amer`, `sre-emea`, `sre-apac` — regional SREs (executer pool, peer pool).
- `sre` — union of the above (any-SRE peer review).
- `cab-tech-leads` — the 3 tech leads (step-2 approval).
- `product-owners` — may substitute for a tech lead on special cases.
- `sre-manager-amer | -emea | -apac` — regional change managers (step 3).
- `global-director` — may approve step 3 anywhere.
- `is-leadership` — may assign/reassign executers.
- `is-management` — owns standard-change templates.
- Consuming-team groups (already align with `consumed_by` / IAM groups) — consumer approval.

Every approval, assignment, and edit is **stamped with the IDP identity** and
appears in the audit trail.

---

## 11. Stakeholder notification & calendar

### 11.1 Notifications (fork on resilience)
On **approved → scheduled** (and a reminder before MW start), notify each affected
`consumer_team`, batched, via the existing channels. The fork uses the env's
resilience **tier vs this change's fault domain** (§9.4):
- **Survives this change** (tier ≥ the change's fault domain) → *"Brief blip at `<MW>` during `<ref>`; auto-recovers. Basis: `<resilience_basis>`."*
- **Does not survive** → *"`<env>` **will go down** at `<MW>` and **won't auto-recover** — have an engineer ready. RTO `<slo_rto>`."* — plus, when `recoverability=gitops`, *"rebuild is automatic via reconcile"*; when `manual`, the runbook `<runbook_url>`.

Consumer power *(Decision Q2)*:
- A consumer can **block a specific date** — but the block **must propose an
  alternative date** (`proposed_alternative_date`). A block sends the CR to
  *blocked*; the proposer reschedules the MW (→ re-approval, §8.3).
- A consumer can **request an additional technical reviewer** to review the change
  on their behalf (adds an extra peer-review step before they approve).

### 11.2 Google Calendar *(new requirement)*
On **final approval (→ approved/scheduled)**, create a **Google Calendar event**
for the MW window:
- **Invitee (required):** the **executer**.
- **Invitees (optional / for awareness):** the **consumer** teams (and the change
  manager).
- Event body: CR `reference`, targets, runbook summary, rollback note.
- Store `gcal_event_id` on the CR. **Reschedule/cancel updates or removes** the
  event. (Integration via a Google service account / OAuth in production.)

---

## 12. DORA synergy
A CR is a change event: **completed = success**, **rolled_back / failed = a change
failure** → feeds `dora` change-failure-rate with attributable data (team, cloud,
criticality). If a `dora.Incident` opens during a CR's MW, auto-link it (suggests
the change caused it).

---

## 13. Why the CMDB must not execute
A CMDB with prod-execution rights concentrates exactly the blast radius we're
trying to manage, needs credentials to every cloud, and breaks the read-only / no
-superuser invariants. The full value (coordination, impact, approval, comms,
calendar, audit) is realized **without** execution. The SRE runs the runbook in
existing tooling; the CMDB records start/verify/outcome.

---

## 14. Additional features
- **Conflict & collision detection** (overlapping targets/MWs; freeze collisions).
- **Freeze / blackout windows** — only emergency changes proceed; others need `freeze_override` + justification.
- **Change calendar** view (per region/cloud/team) — mirrors the Google Calendar events.
- **Standard-change catalog** — the `ChangeTemplate` library, owned by IS management.
- **Post-implementation review (PIR)** — mandatory for emergency / failed / rolled-back.
- **Immutable audit trail** — every version, approval, notification, calendar event is append-only.
- **SLO-aware messaging** — non-resilient notices include `slo_rto`.

---

## 15. Worked example — "take down a ps6 node hosting 3 juju models"
1. **Anybody** drafts a CR. Targets: the **node** `ps6-ra1-n3` **plus** the 3
   affected **juju models** explicitly (multi-target). Region resolves to **EMEA**
   (ps6's region). Proposer fills execute/verify/rollback and picks the **EMEA
   standard MW**. IS leadership assigns an **`sre-emea`** executer.
2. On submit, the **impact engine** unions targets → 5 direct envs + 2 downstream
   dependents; snapshots resilience: 6 resilient, **1 non-resilient (tier-2 prod)**.
3. Guardrail (`requires_all_resilient`) fails → auto-upgraded to **normal**, risk
   **medium**. Approval chain generated: **peer → tech-lead → consumer** (no
   change-manager at medium).
4. Peer (any SRE) approves → one of the **3 tech leads** approves → finally the
   **consumer** team of the non-resilient env reviews **last**. They could **block
   the date** (proposing another) or **request an extra reviewer**; here they approve.
5. CR → **approved**: a **Google Calendar** event is created for the EMEA MW,
   inviting the executer (required) and the consumer team (awareness). Resilient
   consumers get a "brief blip" notice; the non-resilient team got the
   "ready-an-engineer, RTO 30m" notice.
6. At MW start → *in_progress*; PagerDuty silenced. Executer runs the runbook,
   *verifying* → **completed (success)**. (Had verify failed → rollback →
   *rolled_back* + PIR + a DORA change-failure event.)
7. *If the proposer later edits the runbook or window* → `version` bumps, **all
   approvals reset**, the chain (and calendar invite) is regenerated.

---

## 16. Suggested phasing (when it's time to build — not now)
- **A — CR record + completeness gate + multi-target.**
- **B — Impact engine** (placement + switch graph + blast radius + risk score).
- **C — Identity (Canonical IDP groups) + the ordered approval chain + re-approval-on-edit.**
- **D — Notifications** (resilient/non-resilient) **+ Google Calendar**.
- **E — Polish:** templates/catalog, freeze windows, calendar view, DORA linkage, conflict detection, PIR.

---

## 17. Decisions (all resolved)

**Resolved:**
1. **Identity** → **Canonical IDP** (charm); create CAB user groups (§10).
2. **Consumer power** → can **block a specific date but must propose an
   alternative**; can **request an additional technical reviewer** (§11.1).
3. **Scope** → **both** infra and app (juju-model) changes.
4. **Standard templates** → owned by **IS management**.

**Resolved (rev 3):**
5. **Resilience model** → specified in **§9.4**: *per-fault-domain*, **tiered**
   (`none→instance→node→rack/switch→cloud`), **resilience ≠ recoverability**, a
   stateless/stateful split, a computable scorecard, and a **reviewed override**
   that defaults fail-cautious to non-resilient. Today's coarse
   `resilient_env_names()` (gitops + >3 VMs + >1 node) is subsumed by this.

6. **Per-tier resilience** → assess **per tier (web / data / LB)** and roll up to
   the **worst** (`effective = min(tier)`), §9.4. No open decisions remain.

**Resolved (rev 4):**
7. **CR status enum** → §8.1: explicit values with **`awaiting_l<n>`** naming
   (waiting *for* a level, not the ambiguous "pending Lx"); a single terminal
   `approved` once the chain clears; `submitted`, `verifying`, `applied`, `closed`,
   and `expired` added; `cancelled` (proposer withdraws) kept distinct from
   `rejected` (an approver declines); the execution result lives in a separate
   `outcome` field (`success | rolled_back | failed | partial`).

**Newly captured requirements (rev 2):** regional standard MWs (APAC/EMEA/AMER);
multi-target CRs incl. **switch** targets and **multiple juju models**; anybody
proposes / IS-leadership-or-proposer assigns the executer; **region-matched** SRE
executer & change manager; reserved **3 tech leads** (+ product owner); **consumer
approves last**; **edit ⇒ full re-approval**; **Google Calendar event on
approval**.
