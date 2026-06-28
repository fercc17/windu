# Parser GitHub Actions Setup

## Overview

The IS-CMDB automatically ingests environment data from the `is-infrastructure` repository using GitHub Actions workflows. This document describes the setup and operation of these workflows.

## Architecture

```
is-infrastructure (push to main)
    │
    │ (1) GitHub Actions: notify-cmdb.yml
    │     Sends repository_dispatch event
    │
    ▼
is-cmdb
    │
    │ (2) GitHub Actions: ingest.yml
    │     Receives repository_dispatch
    │     Parses changed files
    │     Updates PostgreSQL
    │
    │ (3) GitHub Actions: full-scrape.yml (weekly)
    │     Full repository scan
    │     Ensures consistency
```

## Workflows

### 1. is-infrastructure: notify-cmdb.yml

**Location:** `is-infrastructure/.github/workflows/notify-cmdb.yml`

**Trigger:** On push to `main` branch (only when `services/definitions/**/*.yaml` changes)

**Purpose:** Sends a `repository_dispatch` event to is-cmdb to trigger incremental update

**Behavior:**
- Fires on every merge to main that touches environment definition files
- Sends SHA of the commit to is-cmdb
- Fails silently (`continue-on-error: true`) so infrastructure merges are never blocked

**Required Secret:**
- `CMDB_DISPATCH_TOKEN` - Fine-grained PAT with `actions:write` permission on is-cmdb repository

### 2. is-cmdb: ingest.yml

**Location:** `is-cmdb/.github/workflows/ingest.yml`

**Trigger:** `repository_dispatch` event with type `infra-updated`

**Purpose:** Incremental update when is-infrastructure changes

**Behavior:**
1. Checks out is-cmdb repository
2. Checks out is-infrastructure at the triggered SHA
3. Gets list of changed files (`git diff HEAD~1 HEAD`)
4. Runs parser against is-infrastructure
5. Parser upserts changes to PostgreSQL

**Note:** Currently runs full parse for simplicity (parser is idempotent). Future optimization: parse only changed files.

**Required Secret:**
- `CMDB_DATABASE_URL` - PostgreSQL connection string
- `INFRA_READ_TOKEN` - Fine-grained PAT with `contents:read` on is-infrastructure

### 3. is-cmdb: full-scrape.yml

**Location:** `is-cmdb/.github/workflows/full-scrape.yml`

**Trigger:** 
- Schedule: Every Sunday at 02:00 UTC
- Manual: `workflow_dispatch` (can be triggered manually from GitHub Actions UI)

**Purpose:** Full repository scan to ensure consistency

**Behavior:**
1. Checks out is-cmdb repository
2. Checks out latest is-infrastructure main branch
3. Runs parser against entire repository
4. Ensures CMDB is consistent with source of truth

**Required Secrets:**
- `CMDB_DATABASE_URL` - PostgreSQL connection string
- `INFRA_READ_TOKEN` - Fine-grained PAT with `contents:read` on is-infrastructure

## Setup Instructions

### Prerequisites

1. **is-cmdb repository** must be initialized with:
   - Parser code in `parser/parser.py`
   - Database models created
   - `requirements.txt` with dependencies

2. **is-infrastructure repository** must have:
   - Environment definitions in `services/definitions/` (or appropriate path)
   - YAML files with CMDB labels

### Step 1: Create GitHub Tokens

#### CMDB_DISPATCH_TOKEN (for is-infrastructure)

1. Go to GitHub Settings → Developer settings → Personal access tokens → Fine-grained tokens
2. Create new token with:
   - **Repository access:** Only `fercc17/is-cmdb` (or `canonical/is-cmdb`)
   - **Permissions:** 
     - Actions: Read and write
   - **Name:** `CMDB Dispatch Token`
   - **Expiration:** 1 year (or as per your policy)

3. Add to is-infrastructure repository:
   - Go to is-infrastructure → Settings → Secrets and variables → Actions
   - New repository secret: `CMDB_DISPATCH_TOKEN`
   - Paste token value

#### INFRA_READ_TOKEN (for is-cmdb)

1. Create new fine-grained token with:
   - **Repository access:** Only `fercc17/is-infrastructure` (or `canonical/is-infrastructure`)
   - **Permissions:**
     - Contents: Read-only
   - **Name:** `Infrastructure Read Token`
   - **Expiration:** 1 year

2. Add to is-cmdb repository:
   - Go to is-cmdb → Settings → Secrets and variables → Actions
   - New repository secret: `INFRA_READ_TOKEN`
   - Paste token value

#### CMDB_DATABASE_URL (for is-cmdb)

1. Format: `postgresql://user:password@host:port/database`
2. Add to is-cmdb repository:
   - Go to is-cmdb → Settings → Secrets and variables → Actions
   - New repository secret: `CMDB_DATABASE_URL`
   - Paste connection string

**Security Note:** This should point to a database accessible from GitHub Actions runners. For production, use a database with proper network security and consider using GitHub Actions IP allowlisting.

### Step 2: Deploy Workflows

#### In is-cmdb

Both workflows already exist:
- `.github/workflows/ingest.yml` ✅
- `.github/workflows/full-scrape.yml` ✅

Commit and push if not already in repository:

```bash
cd is-cmdb
git add .github/workflows/
git commit -m "Add GitHub Actions workflows for parser ingestion"
git push
```

#### In is-infrastructure

Create the notify workflow:

```bash
cd is-infrastructure
git add .github/workflows/notify-cmdb.yml
git commit -m "Add CMDB notification workflow

Sends repository_dispatch to is-cmdb on every push to main
that modifies environment definitions. This enables automatic
CMDB updates when infrastructure changes."
git push
```

### Step 3: Test the Setup

#### Test Incremental Update

1. Make a small change to an environment file in is-infrastructure
2. Commit and push to main
3. Verify:
   - is-infrastructure workflow "Notify CMDB on push" runs and succeeds
   - is-cmdb workflow "Incremental Infrastructure Ingest" is triggered
   - Check CMDB database for updated data

#### Test Full Scrape

1. Go to is-cmdb → Actions → "Full Infrastructure Scrape"
2. Click "Run workflow" → "Run workflow"
3. Watch the workflow execute
4. Verify database is populated

#### Test Manually

You can also run the parser locally:

```bash
# Clone both repos
git clone https://github.com/fercc17/is-infrastructure
git clone https://github.com/fercc17/is-cmdb

cd is-cmdb

# Set up environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run parser
export DATABASE_URL="postgresql://user:pass@localhost/cmdb"
python3 parser/parser.py \
  --source ../is-infrastructure \
  --database-url "$DATABASE_URL" \
  --sha "$(cd ../is-infrastructure && git rev-parse HEAD)"
```

## Monitoring

### Check Workflow Runs

**is-infrastructure:**
- Go to Actions tab → "Notify CMDB on push"
- Should run on every push to main that touches environment files
- Should always succeed (or fail silently without blocking merges)

**is-cmdb:**
- Go to Actions tab → "Incremental Infrastructure Ingest"
- Should be triggered by repository_dispatch from is-infrastructure
- Check for failures and investigate parser errors

- Go to Actions tab → "Full Infrastructure Scrape"
- Should run weekly on Sunday 02:00 UTC
- Can be triggered manually at any time

### Common Issues

#### repository_dispatch not triggering

**Symptom:** is-infrastructure workflow succeeds but is-cmdb workflow doesn't run

**Causes:**
- `CMDB_DISPATCH_TOKEN` expired or has wrong permissions
- Repository name mismatch in notify-cmdb.yml
- Event type mismatch (must be exactly `infra-updated`)

**Fix:**
- Verify token has `actions:write` permission on is-cmdb
- Check repository name in notify-cmdb.yml matches actual is-cmdb repo
- Check workflow logs in is-infrastructure for dispatch errors

#### Parser fails

**Symptom:** ingest.yml or full-scrape.yml workflow fails

**Causes:**
- Database connection issues
- YAML parsing errors in is-infrastructure
- Missing required labels
- Database schema mismatch

**Fix:**
- Check workflow logs for specific error
- Verify `CMDB_DATABASE_URL` is correct and accessible
- Test parser locally to reproduce issue
- Check is-infrastructure YAML files for syntax errors

#### INFRA_READ_TOKEN permission denied

**Symptom:** Cannot checkout is-infrastructure repository

**Causes:**
- Token expired
- Token doesn't have `contents:read` permission
- Token not added to is-cmdb secrets

**Fix:**
- Regenerate token with correct permissions
- Add to is-cmdb repository secrets as `INFRA_READ_TOKEN`

## Security Considerations

### Token Scoping

- ✅ **CMDB_DISPATCH_TOKEN**: Scoped to single repository (is-cmdb), single permission (actions:write)
- ✅ **INFRA_READ_TOKEN**: Scoped to single repository (is-infrastructure), read-only
- ✅ **CMDB_DATABASE_URL**: Not a GitHub token, but should use strong password and network restrictions

### Workflow Security

- ✅ **Read-only by default**: Workflows only read from is-infrastructure, write to database
- ✅ **No secrets in logs**: Database URL and tokens are masked in workflow outputs
- ✅ **continue-on-error**: Dispatch failure doesn't block infrastructure changes
- ✅ **Path filters**: Only trigger on actual environment file changes

### Database Security

For production deployment:
- Use database with network access controls
- Consider GitHub Actions IP allowlisting
- Use read-write user for parser, not superuser
- Enable SSL/TLS for database connections
- Rotate database credentials regularly

## Future Improvements

### Optimization: Parse Only Changed Files

Currently, incremental update runs full parse for simplicity. Future optimization:

```python
# In parser.py
def parse_incremental(source, changed_files, database_url):
    """Parse only changed files instead of full repo."""
    for file_path in changed_files:
        if file_path.endswith('.yaml'):
            parse_single_file(file_path, database_url)
```

Benefits:
- Faster execution (seconds instead of minutes)
- Reduced database load
- Still idempotent and safe

### Notification on Failure

Add Mattermost/Slack notification when parser fails:

```yaml
- name: Notify on failure
  if: failure()
  run: |
    curl -X POST ${{ secrets.MATTERMOST_WEBHOOK_URL }} \
      -d '{"text": "❌ CMDB parser failed: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"}'
```

### Metrics and Observability

Add workflow output for metrics:
- Number of environments parsed
- Number of environments updated
- Number of new environments discovered
- Parse duration

## References

- **Parser Implementation:** `parser/parser.py`
- **Parser Spec:** `PARSER.md`
- **Architecture:** `ARCHITECTURE.md`
- **Database Schema:** `SCHEMA.md`
- **GitHub Issues:** #93 (full scrape), #94 (incremental), #95 (notify trigger)

---

**Status:** ✅ Implemented and ready for testing
**Last Updated:** 2026-05-13
