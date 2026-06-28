# is-infrastructure Integration

This document describes changes needed in the `is-infrastructure` repository to enable CMDB ingestion.

## Workflow to add: `.github/workflows/notify-cmdb.yml`

This workflow must be added to the `is-infrastructure` repository. It sends a `repository_dispatch` event to the `is-cmdb` repository on every push to `main`.

```yaml
name: Notify CMDB

on:
  push:
    branches:
      - main

jobs:
  dispatch:
    runs-on: ubuntu-latest
    steps:
      - name: Send repository_dispatch to is-cmdb
        uses: peter-evans/repository-dispatch@v3
        with:
          token: ${{ secrets.CMDB_DISPATCH_TOKEN }}
          repository: canonical/is-cmdb
          event-type: infra-updated
          client-payload: '{"sha": "${{ github.sha }}"}'
        continue-on-error: true

      - name: Log dispatch status
        if: always()
        run: |
          echo "Sent infra-updated event to is-cmdb"
          echo "SHA: ${{ github.sha }}"
```

## Required secrets in is-infrastructure

### `CMDB_DISPATCH_TOKEN`

A GitHub Personal Access Token (PAT) or GitHub App token with the following permissions:

- **Repository:** `canonical/is-cmdb`
- **Permissions:**
  - `contents: read` (to trigger workflows)
  - `actions: write` (to send repository_dispatch events)

### How to create the token

1. Go to GitHub Settings → Developer settings → Personal access tokens → Fine-grained tokens
2. Create a new token with:
   - **Repository access:** Only select repositories → `canonical/is-cmdb`
   - **Permissions:**
     - Repository permissions:
       - Contents: Read-only
       - Actions: Read and write
3. Copy the token and add it as `CMDB_DISPATCH_TOKEN` in `canonical/is-infrastructure` repository secrets

## Required secrets in is-cmdb

### `INFRA_READ_TOKEN`

A GitHub PAT with read access to `canonical/is-infrastructure`:

- **Repository:** `canonical/is-infrastructure`
- **Permissions:**
  - `contents: read`

### `CMDB_DATABASE_URL`

PostgreSQL connection string for the CMDB database:

```
postgresql://username:password@hostname:5432/cmdb
```

## Behavior

1. On every push to `main` in `is-infrastructure`, the `notify-cmdb.yml` workflow runs
2. It sends a `repository_dispatch` event with type `infra-updated` and SHA to `is-cmdb`
3. The `ingest.yml` workflow in `is-cmdb` is triggered
4. `is-cmdb` checks out `is-infrastructure` at the triggered SHA
5. Parser runs and upserts environments to PostgreSQL

## Error handling

- `continue-on-error: true` ensures that CMDB ingestion failures do not block infrastructure reconciliation
- If the dispatch fails, the is-infrastructure workflow continues normally
- If the is-cmdb parser fails, it only affects the CMDB; no infrastructure changes are rolled back

## Testing

To manually trigger the ingestion workflow in `is-cmdb`:

```bash
gh workflow run ingest.yml \
  --repo canonical/is-cmdb \
  --field infra_sha=<commit-sha>
```

Or use the GitHub UI: Actions → Incremental Infrastructure Ingest → Run workflow

## Rollout plan

1. Add `CMDB_DISPATCH_TOKEN` secret to `is-infrastructure`
2. Add `INFRA_READ_TOKEN` and `CMDB_DATABASE_URL` secrets to `is-cmdb`
3. Deploy the CMDB database (PostgreSQL + Redis)
4. Merge the `notify-cmdb.yml` workflow to `is-infrastructure` main branch
5. Verify that the first dispatch event triggers successfully
6. Monitor `is-cmdb` Actions tab for ingestion runs

## Monitoring

- **is-infrastructure:** Check Actions tab for `notify-cmdb` workflow runs
- **is-cmdb:** Check Actions tab for `ingest.yml` workflow runs
- **Database:** Query `environments` table to verify row count and `declared_at` timestamps

```sql
SELECT COUNT(*), MAX(declared_at) FROM environments;
```
