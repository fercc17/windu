# windu — IS one-stop shop

Unified app merging three internal tools into one Django + React (Pragma CSS) shell
over a single Postgres schema:

- **is-cmdb** → CMDB, IS Services, GitOps, Change management (CAB / maintenance)
- **jira-analysis** → ISReq, ISDB, PagerDuty
- **standup-dashboard** → Stand up

## Layout

```
backend/    Django project (config package: cmdb/) — REST API + unified models
  cmdb/apps/{environments,netbox,maintenance,changes,storage,dora,api}   # from is-cmdb
  cmdb/apps/{jira,pagerduty,standup}                                      # merged-in domains
  cmdb/apps/api/management/commands/etl_import.py                         # legacy DB -> windu ETL
  cmdb/apps/api/{identity.py,sections.py,pages_views.py}                  # shell API
frontend/   React 19 + Vite + Pragma CSS (@canonical/styles); generic section renderer
libs/       Vendored reusable logic from the two folded-in apps (metrics, presenters)
```

## Data model

One Postgres database (`windu`), one `public` schema, Django owns every table.
PagerDuty is de-duplicated to the canonical `pd_*` tables (standup + DORA derive
from them). See `backend/cmdb/apps/{jira,pagerduty,standup}/models.py`.

## Run (dev)

```bash
./dev.sh
# backend API: http://127.0.0.1:8010   frontend: http://127.0.0.1:5173
```

Or manually:

```bash
# backend
cd backend && <python> manage.py migrate && <python> manage.py runserver 127.0.0.1:8010
# load data from the three legacy DBs (re-runnable)
<python> manage.py etl_import
# frontend (proxies /api -> :8010)
cd frontend && npm install && npm run dev
```

`<python>` is an env with the `backend/requirements.txt` deps (Django 4.2). DB
config is in `backend/.env`.

## Identity (phase 1, stub)

IS-membership gates the IS-only tabs (Stand up / ISReq / ISDB / PagerDuty). The
current user comes from the `X-Windu-User` header or `?as=` param (default: a
roster member). Real auth via Canonical identity-charmers / OIDC is a later phase.
Try the **"view as email…"** box in the header to impersonate a non-member.
