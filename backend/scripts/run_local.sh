#!/usr/bin/env bash
#
# run_local.sh — ensure local Postgres + Redis are running, then start the
# Django dev server. Assumes ./scripts/setup_local.sh has been run at least once.
#
# With Docker available the canonical command is `docker compose up`; this script
# is for the conda-based local setup.
#
# Note: no `set -u` — conda's activation hooks reference unset vars and would trip it.
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

CONDA_ENV_NAME="cmdb"
PGDATA="$ROOT_DIR/.pgdata"
PGPORT="5432"
REDIS_PORT="6379"
BIND="${1:-127.0.0.1:8000}"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 \
   && [[ ! -d "$PGDATA" ]]; then
    log "Docker detected and no local .pgdata — using docker compose."
    exec docker compose up
fi

command -v conda >/dev/null 2>&1 || die "conda not found. Run ./scripts/setup_local.sh first."
CONDA_BASE="$(conda info --base)"
# shellcheck disable=SC1091
source "$CONDA_BASE/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV_NAME" 2>/dev/null || die "conda env '$CONDA_ENV_NAME' missing. Run ./scripts/setup_local.sh first."

[[ -d "$PGDATA" ]] || die "No Postgres data dir at $PGDATA. Run ./scripts/setup_local.sh first."

if ! pg_ctl -D "$PGDATA" status >/dev/null 2>&1; then
    log "Starting Postgres..."
    pg_ctl -D "$PGDATA" -l "$PGDATA/server.log" -o "-k /tmp -p ${PGPORT}" -w start
fi

if ! redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1; then
    log "Starting Redis..."
    redis-server --daemonize yes --port "$REDIS_PORT" --dir /tmp
fi

log "Starting Django dev server on http://${BIND}/ (Ctrl-C to stop the server)"
exec python manage.py runserver "$BIND"
