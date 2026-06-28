---
name: canonical-spec-backfill
description: >
  Read an existing code repository and backfill a Canonical IS SRE specification
  for it. Use when a system exists in production but has no spec, and the goal is
  to produce a spec that documents what is already there, classified and formatted
  to the IS SRE conventions, ready for braindump publication and the Javier to
  Pierre approval chain. Produces an MD file only. The docx is generated later,
  after the human runs the spec review exercise.
trigger: >
  "backfill a spec", "write a spec for this repo", "document this system as a spec",
  "generate the Canonical spec from the code", or any request to turn an existing
  repo into an IS SRE spec.
---

# Canonical IS SRE Spec Backfill

This skill turns an existing repo into a Canonical IS SRE spec. The system already
runs. You are documenting reality, not proposing a feature. That single fact changes
how every section gets filled and is the source of the only failure mode that matters:
inventing motivation and history that the code cannot tell you.

Read this whole file before touching the repo. The conventions are not optional and
the section order is exact.

---

## 0. Hard rules that apply to everything you write

- No em dashes anywhere. None. Not in prose, not in tables, not in diagrams.
- No prose double dashes (`--`). Markdown horizontal rules and table separators are fine.
- Engineer to engineer tone. Not marketing, not academic, not HR. State the problem,
  state the design. Do not sugar coat.
- Assume the reader understands the Canonical stack and the team context. The
  Specification is not a whitepaper that explains the world from scratch.
- MD first, always. Do not generate docx in this skill. The docx comes after the human
  runs the spec review exercise.
- Pierre vocabulary is load bearing, not decoration: "situational awareness", "elevate
  the maturity", "running at scale", "at scale", "leverage" (as a noun), "resiliency" or
  "resilient". Place them where they carry meaning: Abstract for first impression,
  Rationale for the argument, Specification for the operating framing. Do not sprinkle them.
- Never reproduce sensitive values. The spec is company wide visible and goes to the VP.
  Do not lift secrets, keys, tokens, passwords, Keystone tenant IDs, raw internal IPs,
  internal hostnames, or bucket names into any section. Refer to them by role, for example
  "the master cloud collector bucket", "the Keystone tenant binding", "the CMDB host", not
  by their literal value. If a value is structurally important to explain the design,
  describe its shape ("a 32 char hex tenant ID"), not the value itself. When in doubt,
  redact and note it in Open Issues.
- Document what runs, not everything that exists. A repo carries dead modules, commented
  blocks, disabled feature flags, and deprecated directories. The spec describes the
  deployed and reconciled system. For this stack the membership signals are authoritative:
  Flux kustomization membership and Juju bundle membership tell you what is actually
  reconciled. Code present in the tree but not reconciled or deployed is not part of the
  running system. Do not document it as if it were. Anything ambiguous goes in Open Issues,
  not into the Specification as fact.

### The anti-hallucination boundary (the whole point of this skill)

Code is a high confidence source for some sections and a zero confidence source for
others. Respect the line.

| Section | What the repo can tell you | Confidence |
|---|---|---|
| Abstract | What the system does, derived from code | High |
| Specification | Architecture, components, data model, dependencies, phases already shipped | High |
| Open Issues | TODOs, FIXMEs, open tickets, missing tests, known gaps | High |
| Further Information | Dependencies, versions, repo links, related systems | High |
| Rationale | Why the system exists, why the prior state was inadequate | Low to none |
| Spec History | Past meetings, attendees, decisions | None. Not produced by this skill |

For anything the code cannot tell you, do not invent it. Write a clearly marked
placeholder the human fills, or move the gap into Open Issues. A backfilled Rationale
that fabricates a historical problem statement is worse than an empty one, because it
will be wrong in front of Javier and Pierre.

---

## 1. Workflow

Run these steps in order. Do not skip the reconnaissance and jump to writing.

1. Reconnaissance. Walk the repo and build an inventory (Section 2).
2. Classify the spec type from the evidence (Section 3). Lock the type before writing.
3. Map repo artifacts to spec sections (Section 4).
4. Draft the MD in the exact section order (Section 5).
5. Fill diagrams from the actual topology (Section 6).
6. Mark every gap. Rationale and History get explicit `[HUMAN INPUT REQUIRED]`
   placeholders, never invented content (Section 7).
7. Run the spec review exercise in the correct order: conventions check, then defect
   scan, then Pierre summary resonance check (Section 8).
8. Output the MD and hand back the gap list (Section 9).

---

## 2. Repo reconnaissance

Read these before writing a single line of spec. The goal is to reconstruct the system
from its source, not from a README that may be stale.

General signals, any repo:

- `README*`, `CONTRIBUTING*`, `docs/`, `ADR*` or `adr/` (architecture decision records
  are the closest thing to a real Rationale you will find, read them first).
- `CHANGELOG*`, git log, and tags. Useful only to fix the Abstract and Specification to
  the real current state of the system. Do not use any of this to generate a spec
  changelog. The spec changelog is a human artifact about meetings and review decisions,
  not a mirror of commits, PRs, or merges. The skill does not write it.
- Open issues and TODO/FIXME/XXX/HACK grep across the tree. These become Open Issues.
- Test directories. Coverage gaps are real Open Issues. State them.
- CI config (`.github/workflows/`, `.gitlab-ci.yml`). Tells you the delivery pipeline
  and what "done" means in this repo.
- `LICENSE`. Note it in Further Information.

Canonical stack signals, read whichever are present:

- `*.tf`, `*.tofu`, `terraform/`, Juju terraform provider blocks. These define declared
  infrastructure. Extract resources, providers, and module structure. This is your
  primary source for the Specification.
- `charmcraft.yaml`, `metadata.yaml`, `actions.yaml`, `config.yaml`, `manifest.yaml`,
  `*.charm`, `reactive/` or `src/charm.py`. Charm metadata gives you relations
  (dependency edges), config surface, and the integration contract.
- Juju bundle YAML, `bundle.yaml`, overlays. These give you the deployed topology and
  application to application relations.
- FluxCD: `kustomization.yaml`, `*-source.yaml`, `flux-system/`. Tells you the GitOps
  reconciliation model and what is pulled from where.
- Kubernetes manifests, Helm charts, `Chart.yaml`. Workloads, services, RBAC.
- COS / observability: Prometheus rules, Grafana dashboards as JSON, Loki/Tempo config,
  alert rules. These tell you what is measured and therefore what the SLO surface is.
- For infrastructure-services style repos: `services/definitions/<primitive>/<env>.yaml`
  with native keys. Parse the native keys, do not rename them. Environment files are
  the source of truth for what exists per environment.
- Vault config, Wazuh config, PagerDuty integration config. Security and incident
  surface.
- Temporal workflow/worker code, OCI image build files. Orchestration surface.

Produce an inventory before writing: components, relations between them, declared vs
live distinction if both exist, external dependencies with versions, the delivery
pipeline, and the list of known gaps (TODOs, missing tests, open issues).

---

## 3. Classify the spec type

Every spec declares a type in the header. Classify before writing. For a backfill the
type is almost always Implementation or Standards. Use this decision order.

1. Does the repo define an interface that other code or users consume? An API, an ABI,
   a DSL, a charm relation contract, a published schema, a CLI others script against?
   If yes and the spec is about that contract, type is **Standards**. Standards specs
   additionally require a Decision Summary and a High Level diagram inside the
   Specification.

2. Is the repo an implementation of something, where the spec describes how the system
   is built and operated? Most backfilled infrastructure and tooling repos land here.
   Type is **Implementation**.

3. Is the repo a process artifact (release tooling, decommission workflow, review
   automation, a procedure encoded as code) where the spec is about the process the
   code enacts rather than the code itself? Type is **Process**. Process specs require
   a Process Description, a process workflow diagram, the relevant rules or standards,
   and a RACI matrix inside the Specification.

4. Is the spec general guidelines or design information with no new feature and no
   binding contract? Type is **Informational**.

5. PRS is product requirements authored by a product manager. A backfill from code is
   almost never a PRS. Do not pick it for an existing implementation.

If the repo genuinely spans two types (for example an implementation that also defines
a new public schema), do not pick one arbitrarily. Pick the dominant type and flag the
secondary aspect in Open Issues, per the conventions.

---

## 4. Repo artifact to spec section map

This is the core of the backfill. Each spec section is filled from specific repo
evidence, or marked as a human gap.

| Spec section | Filled from | Method |
|---|---|---|
| Header metadata | Repo name, primary author from git, scope from deployment targets | Derive. Status starts as "Proposal, open for review" |
| Abstract | What the system does | Two to three sentences, derived from the code's actual behaviour |
| Rationale | ADRs if they exist, otherwise HUMAN INPUT | Do not fabricate. See Section 7 |
| Specification | Terraform, charms, bundles, manifests, env definitions, CI | The bulk of the work. Reconstruct architecture from declared state |
| Open Issues | TODO/FIXME grep, open tickets, missing tests, incomplete phases | Direct extraction. Be honest about gaps |
| RACI (Process only) | Who runs the process, from CODEOWNERS plus HUMAN INPUT | Owners come from code, accountability comes from the human |
| Decision Summary (Standards only) | ADRs, commit messages on the contract | Summarize the interface decisions actually made |
| Further Information | Dependencies, versions, repo and Jira links, related specs | Direct extraction from manifests and lockfiles |
| Spec History and Changelog | Not generated by the skill | Leave the section header with a placeholder. The human writes it in the final doc |

---

## 5. Required sections and exact order

Write the MD in this order. The order is fixed by the conventions.

### 5.1 Header metadata block

Opens the spec, immediately under title and subtitle.

```
Status: Proposal, open for review
Type: Standards | Informational | Process | Implementation
Author: Name, role
Scope: Who or what the spec applies to
Date: Month year
Parent of: Child spec list, if any
Depends on: Parent spec or other dependencies, if any
Closes: CultureAmp action, ticket, or commitment, if applicable
```

The Type field is required. Author defaults to the dominant git contributor unless the
human overrides. Scope is derived from where the system is deployed (which environments,
which clouds, which teams consume it).

### 5.2 Abstract

Two to three sentences. Strict. Not paragraphs. Describe what the system is and what it
does, in the system's actual current behaviour. If you cannot summarize the repo in a
couple of sentences, the spec scope is too broad, split it.

### 5.3 Rationale

Roughly one page maximum. Reasons the spec should be accepted, and why the prior state
was inadequate.

For a backfill this is the dangerous section. The code does not contain the historical
motivation. Two honest options:

- If ADRs or design docs exist in the repo, summarize the motivation they record. Cite
  them in Further Information.
- If they do not exist, the real rationale for a backfill is usually that an undocumented
  system is running at scale with no situational awareness around it, and the spec exists
  to elevate the maturity of how it is operated and reviewed. State that if it is true.
  Then insert `[HUMAN INPUT REQUIRED: original design motivation]` for anything you cannot
  source.

Omit the section entirely rather than write filler. Lack of motivation is a valid reason
to reject a spec, so a fabricated Rationale actively harms the spec.

### 5.4 Specification

The body. Reconstruct the system from declared state. Assume the reader knows the context.

Structure with subtopics. Use diagrams, code blocks, pseudo-code, and tables. Cover:

- Architecture and components, reconstructed from the repo, not imagined.
- The data or declaration model. For env definition repos, document the native keys as
  they appear, do not rename them.
- Dependency edges between components, taken from charm relations, bundle relations, or
  module wiring.
- Declared state vs live state if the system distinguishes them.
- The delivery and reconciliation model, taken from CI and Flux config.
- What is measured, taken from the observability config, framed as the SLO surface.

Implementation and Rollout content lives here as subtopics, not as separate top level
sections. Anchor phased work to milestones, not dates, unless a date is a real commitment.

Required and type specific content inside Specification:

- **Open Issues** subsection, required for every spec. This is a subsection of
  Specification, not a peer. Fill it from real gaps: TODOs, missing test coverage,
  incomplete phases, unresolved type ambiguity. Do not soften it.
- **RACI matrix**, required for Process specs.
- **Decision Summary and High Level diagram**, required for Standards specs.

There is no minimum length. The maximum is implicit: as brief as possible while getting
the point across.

**Traceability while drafting.** This is the mechanism that makes the Section 8 defect
scan real rather than a promise. As you write each architectural claim in the
Specification, carry a source reference for it: the file path plus the specific anchor,
for example a charm relation in `metadata.yaml`, a terraform resource block, a Flux
kustomization, or a native key in an env definition file. Every component, relation,
dependency, and measured signal you state must have one. A claim you cannot attach a
source to is a hallucination and does not go in the spec, it goes in Open Issues as an
open question.

Decide upfront where these references land in the output. Two acceptable modes:

- Keep them. Collect the source references into Further Information as a provenance list,
  one line per claim. Useful for the first review pass so Javier or James can verify the
  spec against the repo quickly.
- Strip them. Remove the references before output and rely on the defect scan having
  passed. Cleaner spec, less verifiable later.

Default to keeping them for a first backfill. Strip only when the human asks for the
clean version.

### 5.5 Further Information

Optional but for a backfill it is almost always present and almost always rich, because
dependencies and references are exactly what code is good at telling you. Include
dependency versions, lockfile contents worth noting, repo and Jira links, related specs,
ADR links, and industry or theoretical references where the design maps to known patterns
(GitOps four principles, IaC per Kief Morris, Platform Engineering per Fournier, SRE per
Treynor Sloss). Alternate designs the code shows evidence of (abandoned modules, feature
flags) belong here.

### 5.6 Spec History and Changelog

The convention requires this section, and it will exist in the final doc. The skill does
not write it. Do not generate entries from commits, PRs, merges, or tags. The changelog
records meetings, attendees, and review decisions, which are human events the agent has
no access to.

Emit the section header with an empty table and a single placeholder line, nothing else:

```
| Date | Attendees / Author | Change or Meeting Notes |
|---|---|---|
| [HUMAN INPUT REQUIRED] | | |
```

The human fills the first real entry in the final doc. The agent leaves it blank.

---

## 6. Diagrams

Add a diagram wherever it simplifies understanding, and always for Standards (High Level
diagram required) and Process (workflow diagram required).

- Draft diagrams as Mermaid inside the MD so they are reviewable in the braindump. The
  final docx pipeline renders diagrams as png at generation time.
- Build the diagram from the real topology you reconstructed in reconnaissance. An
  architecture diagram that does not match the bundle relations is a defect.
- Keep diagrams legible at one screen. If the topology does not fit, the spec is probably
  too broad.

---

## 7. Gap handling

For every section the code cannot source, do one of two things. Never a third.

1. Insert an explicit, greppable placeholder: `[HUMAN INPUT REQUIRED: <what is missing>]`.
2. Move the gap into the Open Issues subsection if it is a real open question about the
   system rather than missing documentation.

Banned: writing plausible sounding Rationale, inventing meeting history, asserting design
intent the code does not support, or stating an SLO the observability config does not
actually enforce. If you are tempted to write "the team decided", stop. You do not know
that. The human does.

At the end, return the full list of placeholders so the human knows exactly what to fill
before the spec moves forward.

---

## 8. Spec review exercise

Run this before declaring the MD ready. The order is fixed and matters. Reversing it
causes structural violations to be missed.

1. **Conventions check first.** Verify section order is exact, the Type field is present
   and correct, Abstract is two to three sentences, Open Issues is a subsection of
   Specification, Standards has its Decision Summary and High Level diagram, Process has
   its RACI and workflow diagram, the Spec History header is present with the empty
   placeholder and no fabricated entries, and there are no em dashes or prose double
   dashes anywhere.

2. **Defect scan second.** Verify the reconstructed architecture matches the repo. Use
   the source references carried during drafting: every component, relation, dependency,
   and measured signal must have one, and each reference must still point at real code.
   Any claim without a valid source is a hallucination and comes out now, into Open Issues
   if it is a real open question, deleted otherwise. Two extra sweeps in this step:
   confirm nothing documented is dead or unreconciled code (check Flux and bundle
   membership), and confirm no sensitive value (secret, token, tenant ID, raw IP, internal
   hostname, bucket name) survived into any section.

3. **Pierre summary resonance check third.** This simulates the real path: Pierre will
   not read the spec, he will ask Gemini for a summary. The spec only survives if Pierre's
   own vocabulary surfaces in that AI summary on its own, without you forcing it.

   Produce, for your own use only, three summaries of the spec at the sizes Pierre asks
   for: one paragraph, roughly two pages, roughly five pages (use the full spec if it is
   shorter than five pages). Write them the way a generic summarizer would, neutral, not
   trying to please anyone.

   Then check each summary against the full Pierre phrase list:

   - situational awareness
   - elevate the maturity (and the longer form, elevate the maturity of our processes)
   - running at scale
   - at scale
   - leverage (used as a noun)
   - AI
   - resiliency, or resilient

   Scoring. A phrase passes only if it appears because the spec's content pulled it in,
   not because you seeded it into the summary. For each phrase, mark present or absent per
   summary size. The check fails if a phrase is absent from the shorter summaries, because
   the shorter the summary the more it reflects what the spec is actually about. A phrase
   that only shows up in the five page version but vanishes from the one paragraph version
   is not load bearing yet.

   On failure, do not edit the summaries. Edit the spec body so the concept becomes
   structural: situational awareness and AI belong in the Abstract and Rationale where the
   value argument lives, running at scale and at scale belong in the Rationale and the
   Specification operating framing, resiliency belongs wherever the spec describes failure
   behaviour, blast radius, redundancy, or recovery, leverage belongs wherever the spec
   explains what the system makes possible with the same headcount. Then regenerate the
   summaries and rerun the check. Repeat until the short summaries carry the phrases on
   their own.

   Do not show these summaries or the scoring unless asked. They are an internal gate. What
   ships is the spec, tuned until it passes.

---

## 9. Output

- Write the spec as a single MD file. Name it after the system, for example
  `is-cmdb-spec.md`.
- If the spec exceeds ten pages, add an Executive Summary subsection at the top of the
  Specification section, aimed at Pierre, illustrated with charts, diagrams, and tables,
  referencing the full body below it.
- Return the MD plus the placeholder gap list. Do not generate docx. Do not route for
  approval. Braindump publication and the Javier to Pierre approval chain are separate
  steps the human owns.

### Approval chain context (for the human, not actioned by the agent)

- Javier Arregui is the primary approver for IS SRE, in the IS Director role formerly
  held by Kristofer Tingdahl, who has left. Javier is always informed before anything
  reaches Pierre. Do not bypass.
- Pierre Guillemin is the ultimate approver for organization wide visibility.
- James Simpson is the technical co-sign and carries weight on technical specs.
- Maksim is the process co-sign and carries weight on Process specs.

---

## 10. Quick reference: section order and requirements

| Section | Required | Length | Backfill notes |
|---|---|---|---|
| Header metadata | Yes | Fixed format | Type field required. Status starts as Proposal |
| Abstract | Yes | 2 to 3 sentences | Derived from actual system behaviour |
| Rationale | Optional | ~1 page max | Do not fabricate. ADRs or HUMAN INPUT only |
| Specification | Yes | Brief but complete | Reconstructed from declared state. Contains Open Issues |
| Further Information | Optional | No limit | Usually rich for a backfill: deps, versions, refs |
| Spec History | Header only | Stub | Skill emits empty placeholder. Human writes it in the final doc. Never from git |

Type specific, inside Specification: Open Issues (all), RACI (Process), Decision Summary
plus High Level diagram (Standards), Process Description plus workflow diagram (Process).