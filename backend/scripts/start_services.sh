#!/usr/bin/env bash
#
# start_services.sh — ensure the local Postgres + Redis are running. Does NOT
# start the web server. Safe to run repeatedly. Used as the PyCharm "Django
# runserver" before-launch step, and usable on its own.
#
# Note: no `set -u` — conda's activation hooks reference unset vars and would trip it.
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PGDATA="$ROOT_DIR/.pgdata"
PGPORT="5432"
REDIS_PORT="6379"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

CONDA_BASE="$(conda info --base 2>/dev/null || true)"
[[ -n "$CONDA_BASE" && -f "$CONDA_BASE/etc/profile.d/conda.sh" ]] && source "$CONDA_BASE/etc/profile.d/conda.sh"
conda activate cmdb 2>/dev/null || die "conda env 'cmdb' missing. Run ./scripts/setup_local.sh first."

[[ -d "$PGDATA" ]] || die "No Postgres data dir at $PGDATA. Run ./scripts/setup_local.sh first."

if pg_ctl -D "$PGDATA" status >/dev/null 2>&1; then
    log "Postgres already running."
else
    log "Starting Postgres on port ${PGPORT}..."
    pg_ctl -D "$PGDATA" -l "$PGDATA/server.log" -o "-k /tmp -p ${PGPORT}" -w start
fi

if redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1; then
    log "Redis already running."
else
    log "Starting Redis on port ${REDIS_PORT}..."
    redis-server --daemonize yes --port "$REDIS_PORT" --dir /tmp
fi

log "Services ready."