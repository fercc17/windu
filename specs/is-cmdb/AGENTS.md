# AGENTS.md — bootstrap IS-CMDB on a fresh Linux machine

A self-contained runbook for an AI agent (or a new engineer) to clone this repo on a
clean Linux box, stand up the stack, and load **all** the data. Everything here has
been verified against the scripts and management commands in this repo.

For **what tokens you need and how to get them**, see [`docs/TOKENS.md`](docs/TOKENS.md).
You do **not** need any token to get a fully-populated local instance — see "Pull all
the data" below.

> Note: the top of `CLAUDE.md` still says the repo is "specs-only". That is stale — the
> Django app, parser, poller, charm, and seed data all exist on disk now. Trust this file
> and the actual tree over that sentence.

---

## TL;DR (no tokens, ~5 min)

```bash
gh repo clone fercc17/is-cmdb        # or: git clone https://github.com/fercc17/is-cmdb
cd is-cmdb
./scripts/setup_local.sh             # installs Postgres + Redis + deps, runs migrations
conda activate cmdb                  # the env setup_local.sh creates (skip if Docker mode)
python manage.py loaddata sample_data.json   # full DB snapshot, no tokens needed
./scripts/run_local.sh               # http://127.0.0.1:8000/  (API docs at /api/docs/)
```

If you want *live* data instead of the bundled snapshot, skip the `loaddata` line and
follow "Pull all the data" → "Primary path" below.

---

## What this project is

A read-only CMDB for Canonical IS. Two data stores answer two questions:

- **PostgreSQL** = *declared* state (what `infrastructure-services` says should exist,
  ownership, criticality, dependency graph). Written only by `parser/parser.py`.
- **Redis** = *live* placement (which node each environment is actually running on).
  Written only by the poller, TTL 480s. A **missing** key is a signal (poller skipped a
  cycle) — the UI renders a stale badge, never assumes the key exists.

The Django UI reads both and writes to neither. Stack: Python 3.12, Django 4.2,
PostgreSQL, Redis. `manage.py` is at the **repo root**; the settings module is
`cmdb.settings` and reads `.env` via `django-environ`.

---

## Prerequisites

- Linux, `git`, and **one** of:
  - **Docker** + `docker compose` (preferred — `setup_local.sh` auto-detects it), or
  - **conda** (Miniconda/Anaconda) for a rootless local Postgres + Redis.
- `gh` (GitHub CLI), optional — only needed to clone private source repos for *live*
  data. `gh auth login` is enough; you do not need a PAT in `.env` for local work.

No root/sudo is required in the conda path.

---

## Step 1 — Clone

```bash
gh repo clone fercc17/is-cmdb && cd is-cmdb
# or: git clone https://github.com/fercc17/is-cmdb && cd is-cmdb
```

`bootstrap.sh` does the same with a "next steps" hint:
`./bootstrap.sh fercc17/is-cmdb`.

The three source repos this CMDB ingests — `infrastructure-services/` (parser),
`is-terraform-models/` and `is-terraform-modules/` (charm/GitOps resolution) — are
**git-ignored siblings, not part of this clone**. `setup_local.sh` (Step 2) clones
all three when the `gh` CLI is available; without `gh`, clone them manually:

```bash
gh repo clone canonical/infrastructure-services
gh repo clone canonical/is-terraform-models
gh repo clone canonical/is-terraform-modules
```

For a fully-offline, no-token instance, skip the clones and seed from the bundled
CSV instead (`python manage.py import_csv environments.csv`) — but the `refresh_*`
charm/GitOps commands need the two `is-terraform-*` checkouts and fail without them.

---

## Step 2 — Set up the stack

```bash
./scripts/setup_local.sh
```

Idempotent; safe to re-run. It auto-detects the environment:

- **Docker present** → `docker compose up --build -d` (db + redis + web). UI on
  `http://localhost:8000/`. Stop with `docker compose down`.
- **No Docker** → creates a conda env named `cmdb` (Python 3.12 + postgresql + redis),
  `pip install -r requirements.txt`, writes a local `.env`, inits Postgres under
  `.pgdata/`, creates the `cmdb` database (user/pass `cmdb`/`cmdb`, port 5432), starts
  Redis on 6379, runs `python manage.py migrate`, and installs a daily sync cron job.

`./scripts/setup_local.sh --no-start` sets up without starting services.

After this you have an empty-but-migrated database. Now load data.

---

## Step 3 — Pull all the data

Pick a path. **None of these require a token** (the GitHub source repos are vendored;
Netbox/PagerDuty/etc. only add enrichment — see `docs/TOKENS.md`).

### Fastest: bundled full snapshot (no tokens, no source repos)

```bash
python manage.py loaddata sample_data.json
```

A Django fixture containing a complete populated database (environments, dependencies,
nodes, charms, etc.). Best loaded into a **fresh** migrated DB; if you hit a
content-type/PK conflict, recreate the DB (conda: `dropdb -h localhost cmdb && createdb
-h localhost -U cmdb cmdb && python manage.py migrate`) before loading.

### Primary path: parse declared state from source, then enrich

```bash
# 1. Declared state — parse the vendored (or freshly cloned) infra repo into Postgres.
#    The DB URL matches the local conda setup; adjust for Docker (host db -> db).
python parser/parser.py \
    --source infrastructure-services \
    --database-url postgresql://cmdb:cmdb@localhost:5432/cmdb
#    No source repo? Seed declared envs from the bundled CSV instead:
#    python manage.py import_csv environments.csv

# 2. Classify / enrich the declared rows (each is idempotent; run with --help for flags)
python manage.py classify_k8s              # tag kubernetes_cluster environments
python manage.py import_charms             # ingest charm definitions
python manage.py refresh_charms            # resolve charm -> environment links
python manage.py refresh_service_charms    # service-level charm resolution
python manage.py refresh_gitops            # map env -> model -> module (gitops chain)
python manage.py refresh_dependency_cache  # materialise dependency graph
python manage.py update_cloud_capacity     # per-cloud capacity rollups

# 3. Physical nodes from Netbox (NEEDS NETBOX_TOKEN — see docs/TOKENS.md; skip if absent)
python manage.py reconcile_netbox          # upsert Node/NodeInterface/NodeCable
python manage.py load_host_aggregates      # OpenStack host-aggregate CSVs in data/
python manage.py build_switch_graph        # switch uplink graph from cable data

# 4. Live placement into Redis. In prod the poller writes this every 5 min; for local
#    dev derive it from the bundled juju fixtures:
DJANGO_SETTINGS_MODULE=cmdb.settings python scripts/seed_placement_from_fixtures.py
python manage.py link_placement_nodes              # Environment.primary/secondary_node
python manage.py populate_architecture_from_redis  # arch fields from placement

# 5. Storage (RadosGW ingestion is stubbed until creds exist; seed demo data instead)
DJANGO_SETTINGS_MODULE=cmdb.settings python scripts/seed_storage_demo.py
```

Steps 3 and the token-gated parts of this list degrade gracefully — if a token is
missing the command logs a warning and continues. See `docs/TOKENS.md` for exactly which
features each token unlocks.

### Keeping it fresh

`setup_local.sh` installs a cron job (`scripts/sync_infra_services.sh`, daily 03:00) that
pulls `infrastructure-services` and re-runs the parser. Logs land in `logs/sync.log`. Run
it by hand any time: `./scripts/sync_infra_services.sh`.

---

## Step 4 — Run the server

```bash
./scripts/run_local.sh          # starts Postgres + Redis if needed, then runserver
# or, with the conda env active:
python manage.py runserver
```

Then open `http://127.0.0.1:8000/` (API docs at `/api/docs/`). Stop the background
Postgres/Redis with `./scripts/stop_local.sh`.

---

## Step 5 — Verify

```bash
python manage.py shell -c "
from cmdb.apps.environments.models import Environment, EnvironmentDependency
print('environments:', Environment.objects.count())          # ~1833 with full data
print('dependencies:', EnvironmentDependency.objects.count()) # ~3042 with full data
"
curl -s http://127.0.0.1:8000/api/health/        # liveness
pytest tests/                                     # full test suite
```

A populated instance has roughly **1,833 environments** and **3,042 dependency edges**.
Lower counts mean a partial load (e.g. CSV-only without enrichment).

---

## Non-negotiable invariants (don't break these)

- **Parser is idempotent**: `INSERT ... ON CONFLICT DO UPDATE`, never `get_or_create`.
  Re-running on the same SHA must produce identical rows.
- **Soft delete only on `environments`**: a vanished `git_path` sets `end_date = NOW()`
  and `status = 'decommissioning'`. Never `DELETE` — history is the point.
- **Redis TTL is the health signal**: views must handle `None` from `get_placement()`
  and render a stale indicator. Never assume the key exists.
- **Single Redis client**: all Redis access goes through `cmdb/redis_client.py`. Never
  instantiate `redis.Redis(...)` directly.
- **Read-only tool**: no admin/superuser views. The SRE team is the only audience.
- Type hints on all signatures; no `print()` (use `logging.getLogger(__name__)`);
  all SQL parameterised.

---

## Where to read more

| Topic | File |
|-------|------|
| Tokens — what's needed, how to get them | [`docs/TOKENS.md`](docs/TOKENS.md) |
| System overview & data flow | `ARCHITECTURE.md` |
| DB tables, Redis keys, label convention | `SCHEMA.md` |
| Parser (GitHub Actions ingestion) | `PARSER.md`, `docs/parser-github-actions-setup.md` |
| Poller (live placement CronJob) | `POLLER.md` |
| Django UI / REST API | `DJANGO_UI.md` |
| Netbox webhook + reconciliation | `NETBOX_INTEGRATION.md` |
| Code-style rules & directory layout | `.github/copilot-instructions.md` |
| Docker-based dev workflow | `docs/development.md` |
| Issue backlog (phased) | `GITHUB_ISSUES.md` |
