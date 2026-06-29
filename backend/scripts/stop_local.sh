#!/usr/bin/env bash
#
# stop_local.sh — stop the local Postgres and Redis started by setup_local.sh /
# run_local.sh. (Stop the Django dev server itself with Ctrl-C.)
#
# Note: no `set -u` — conda's activation hooks reference unset vars and would trip it.
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PGDATA="$ROOT_DIR/.pgdata"
REDIS_PORT="6379"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 \
   && [[ ! -d "$PGDATA" ]]; then
    log "Stopping docker compose stack..."
    cd "$ROOT_DIR" && exec docker compose down
fi

CONDA_BASE="$(conda info --base 2>/dev/null || true)"
[[ -n "$CONDA_BASE" && -f "$CONDA_BASE/etc/profile.d/conda.sh" ]] && source "$CONDA_BASE/etc/profile.d/conda.sh"
conda activate cmdb 2>/dev/null || true

if [[ -d "$PGDATA" ]] && pg_ctl -D "$PGDATA" status >/dev/null 2>&1; then
    log "Stopping Postgres..."
    pg_ctl -D "$PGDATA" -m fast stop
else
    log "Postgres not running."
fi

if command -v redis-cli >/dev/null 2>&1 && redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1; then
    log "Stopping Redis..."
    redis-cli -p "$REDIS_PORT" shutdown nosave 2>/dev/null || true
else
    log "Redis not running."
fi

log "Done."