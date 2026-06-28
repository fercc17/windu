# TOKENS.md — credentials IS-CMDB uses, and how to get them

Every secret this project reads, what it's for, whether it's required, how to obtain it,
and what stops working without it. Tokens are read from `.env` (Django, via
`django-environ`) or from the process environment (parser/poller). A template lives in
[`.env.example`](../.env.example).

> **You need zero tokens to bootstrap a fully-populated local instance.** The GitHub
> source repos are vendored in the tree and a complete DB snapshot ships as
> `sample_data.json` (see [`../AGENTS.md`](../AGENTS.md)). Tokens only unlock *live*
> refresh from external systems (Netbox, PagerDuty, Mattermost, S3, Juju). Each
> integration degrades gracefully: if its token is unset, the code logs a warning and
> continues.

**Never commit `.env` or real tokens.** `.env` is git-ignored; only `.env.example`
(placeholders) is tracked. Scope every token to the minimum repo/permission and set an
expiry.

---

## At a glance

| Env var | For | Required? | How to get it |
|---|---|---|---|
| `SECRET_KEY` | Django crypto signing | Yes (auto-set locally) | Generate (see below) |
| `DATABASE_URL` | Postgres connection | Yes (auto-set locally) | Set by `setup_local.sh` |
| `REDIS_URL` | Redis connection | Yes (auto-set locally) | Set by `setup_local.sh` |
| `INFRA_READ_TOKEN` | Parser checkout of infra repo **in CI** | CI only | GitHub fine-grained PAT |
| `CMDB_DISPATCH_TOKEN` | infra→cmdb `repository_dispatch` | CI only (lives in *infra* repo) | GitHub fine-grained PAT |
| `CMDB_DATABASE_URL` | DB URL **for GitHub Actions** | CI only | Your hosted Postgres URL |
| `NETBOX_URL` / `NETBOX_TOKEN` | Pull physical nodes from Netbox | Optional (node data) | Netbox UI → API tokens |
| `NETBOX_WEBHOOK_SECRET` | Verify Netbox webhook HMAC | Optional | You choose it; set both sides |
| `PAGERDUTY_API_TOKEN` | Read PD services/oncalls (audits) + DORA incident ingest (MTTR) | Optional | PagerDuty → API Access Keys (read-only) |
| `PAGERDUTY_WRITE_TOKEN` | Create/cancel PD maintenance windows | Optional (not yet wired — mock) | PagerDuty → API Access Keys (read/write) |
| `FLUX_WEBHOOK_HMAC_SECRET` | Verify Flux notification webhook HMAC (DORA deploys) | Optional (DORA deploy metrics) | You choose it; set in the Flux `generic-hmac` Provider secret + here. Per-cloud: `FLUX_WEBHOOK_HMAC_SECRET_<CLOUD>` |
| `MATTERMOST_URL` / `MATTERMOST_TOKEN` / `MATTERMOST_DM_USER` | Maintenance DMs | Optional | Mattermost personal access token |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `S3_BUCKET_NAME` | Placement-history export to S3 | Optional | AWS IAM access key |
| `JUJU_CONTROLLER_URL` / `JUJU_USERNAME` / `JUJU_PASSWORD` | Poller live placement (Juju) | Optional (poller) | Juju controller creds |
| `K8S_KUBECONFIG_PATH` | Poller live placement (K8s) | Optional (poller) | A kubeconfig file path |
| `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` / `EMAIL_FROM` | Maintenance emails | Optional (mock until set) | Your SMTP provider |
| `GIT_SHA` | SHA the parser ingested | CI-injected | Set by the workflow |

---

## Local secrets (set automatically)

### `SECRET_KEY`
Django's signing key. `setup_local.sh` writes a dev-insecure value into `.env`. For any
shared/production use, generate a real one:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

### `DATABASE_URL`, `REDIS_URL`
Connection strings. Locally these default to
`postgresql://cmdb:cmdb@localhost:5432/cmdb` and `redis://localhost:6379/0` and are
written by `setup_local.sh`. Under Docker the host is `db` / `redis`, not `localhost`.

---

## GitHub tokens (CI ingestion only)

These power the automated parser pipeline (infra merge → `repository_dispatch` → parse).
**You do not need them for local development** — `gh auth login` covers any cloning, and
the infra repos are already vendored. Full setup is in
[`parser-github-actions-setup.md`](parser-github-actions-setup.md) and
[`is-infrastructure-integration.md`](is-infrastructure-integration.md).

### `INFRA_READ_TOKEN` — read the infra repo from a CMDB workflow
- **Where:** secret on the **is-cmdb** repo.
- **Permissions:** fine-grained PAT, repo access = the infra repo only, **Contents: Read-only**.
- **Get it:** GitHub → Settings → Developer settings → Personal access tokens →
  Fine-grained tokens → *Generate new token* → restrict to the infra repo, Contents:
  Read-only, set an expiry → add to is-cmdb repo secrets as `INFRA_READ_TOKEN`.
- **Without it:** the CI ingest/full-scrape workflows can't check out the infra repo.
  Local parsing against the vendored `infrastructure-services/` is unaffected.

### `CMDB_DISPATCH_TOKEN` — let infra trigger CMDB (lives in the *infra* repo)
- **Where:** secret on the **is-infrastructure** repo (not this one).
- **Permissions:** fine-grained PAT, repo access = `is-cmdb` only, **Actions: Read and
  write** (+ Contents: Read).
- **Get it:** same flow as above, scoped to `is-cmdb`, then add to the infra repo's
  secrets as `CMDB_DISPATCH_TOKEN`.
- **Without it:** infra merges won't auto-trigger a CMDB parse. The weekly full-scrape
  and manual runs still work.

### `CMDB_DATABASE_URL` — DB connection for GitHub Actions
- **Where:** secret on the **is-cmdb** repo. Not a token — a Postgres URL
  (`postgresql://user:pass@host:5432/cmdb`) pointing at a DB reachable from GitHub
  runners. Use a non-superuser role and network restrictions.

---

## Netbox (physical node data)

### `NETBOX_URL`, `NETBOX_TOKEN`
- **For:** `python manage.py reconcile_netbox` and the webhook receiver — pulls devices
  into `Node`/`NodeInterface`/`NodeCable`. Read access is sufficient.
- **Get it:** log in to Netbox → your profile → **API Tokens** → *Add a token* (uncheck
  "write enabled" for read-only). Copy the key into `NETBOX_TOKEN`. `NETBOX_URL` is the
  API base, e.g. `https://netbox.example.com/api/`.
- **Without it:** node/rack/interface/cable data is empty; node detail, physical-
  completeness badges, and switch-graph views have nothing to show. Environment and
  dependency data (from the parser) is unaffected.

### `NETBOX_WEBHOOK_SECRET`
- **For:** HMAC-SHA512 validation of incoming Netbox webhooks (`X-NetBox-Signature`).
- **Get it:** invent a strong random string; set it identically here and in the Netbox
  webhook config. In dev, if unset the receiver logs a warning and still accepts.

---

## PagerDuty (maintenance windows / audits)

### `PAGERDUTY_API_TOKEN` (read-only)
- **For:** auditing PD services, teams, and on-calls (`scripts/explore_pagerduty.py`,
  audit docs). Read-only is enough.
- **Get it:** PagerDuty → **Integrations → API Access Keys** → *Create New API Key*,
  leave **read-only** checked. (Account-admin scope required to create one.)

### `PAGERDUTY_WRITE_TOKEN` (write)
- **For:** creating/cancelling maintenance windows. **Currently mocked** —
  `cmdb/integrations/pagerduty.py` raises `NotImplementedError` until this is set; the UI
  shows a "requires write token" message rather than failing.
- **Get it:** same screen, but a **read/write** key.
- **Without it:** the maintenance-window UI works end-to-end except the actual PD
  silence/cancel call.

---

## Mattermost (maintenance notifications)

### `MATTERMOST_URL`, `MATTERMOST_TOKEN`, `MATTERMOST_DM_USER`
- **For:** DMing a user when a maintenance window opens.
- **Get it:** Mattermost → **Account Settings → Security → Personal Access Tokens →
  Create Token** (a System Admin must first enable personal access tokens). `MATTERMOST_URL`
  is your server base URL; `MATTERMOST_DM_USER` is the recipient username (default
  `fercc17`).
- **Without it:** notification creation logs a warning and is skipped; the maintenance
  flow itself still completes.

---

## Optional integrations

### AWS — `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME`
- **For:** exporting placement history to S3. Needs the optional `boto3` dependency
  (commented in `requirements.txt`).
- **Get it:** AWS IAM → create a user/role with `s3:PutObject` on the target bucket →
  generate an access key.
- **Without it:** no S3 export; everything else runs.

### Juju — `JUJU_CONTROLLER_URL`, `JUJU_USERNAME`, `JUJU_PASSWORD`
- **For:** the poller reading live placement from a Juju controller. Needs the optional
  `python-libjuju` dependency.
- **Get it:** from your Juju controller admin (`juju show-controller`, a registered
  user's credentials).
- **Without it (local):** seed placement from the bundled fixtures instead —
  `python scripts/seed_placement_from_fixtures.py` (see `AGENTS.md`).

### Kubernetes — `K8S_KUBECONFIG_PATH`
- **For:** the poller reading placement from K8s. Needs the optional `kubernetes`
  dependency. Point it at a kubeconfig with read access to the relevant clusters.

### Email/SMTP — `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_FROM`
- **For:** emailing environment CIA owners about maintenance. **Mocked** until
  `EMAIL_HOST` is set — the code logs "would email …" instead of sending.
- **Get it:** any SMTP provider (host, port 587, username, app password).

---

## Quick start for `.env`

```bash
cp .env.example .env
# Minimum for local work: nothing else — setup_local.sh fills DB/Redis/SECRET_KEY.
# Add tokens only for the integrations you actually want live:
#   NETBOX_TOKEN=...        # node data
#   PAGERDUTY_API_TOKEN=... # PD audits
#   MATTERMOST_TOKEN=...    # maintenance DMs
```

See [`../AGENTS.md`](../AGENTS.md) for the full bootstrap + data-load runbook.
