#!/usr/bin/env bash
#
# start_cmdb.sh — one-shot: bring IS-CMDB fully up after a reboot.
#
# Postgres data persists on disk (.pgdata), so environments / incidents / DORA
# data survive a reboot. Redis is in-memory, so the *live placement* is lost and
# must be re-seeded. This script does the whole sequence, idempotently:
#
#   1. start Postgres (if not already running)
#   2. start Redis    (if not already running)
#   3. apply migrations (no-op when up to date)
#   4. re-seed live placement into Redis  (+ link nodes, populate architecture)
#   5. start the Django server
#
# Usage:
#   ./scripts/start_cmdb.sh              # full bring-up; server detached in background
#   ./scripts/start_cmdb.sh --foreground # run the server attached (Ctrl-C to stop)
#   ./scripts/start_cmdb.sh --no-serve   # recover state only; don't start the server
#   BIND=127.0.0.1:8000 ./scripts/start_cmdb.sh
#
# Re-running is safe: already-running services are detected and left alone, and
# re-seeding just refreshes the Redis keys with a fresh TTL.
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
BIND="${BIND:-0.0.0.0:8000}"
SERVE="background"

usage() { sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

for arg in "$@"; do
    case "$arg" in
        --foreground) SERVE="foreground" ;;
        --no-serve)   SERVE="none" ;;
        -h|--help)    usage 0 ;;
        *) printf 'unknown arg: %s\n\n' "$arg" >&2; usage 2 ;;
    esac
done

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# --- conda env ---------------------------------------------------------------
command -v conda >/dev/null 2>&1 || die "conda not found. Run ./scripts/setup_local.sh first."
CONDA_BASE="$(conda info --base)"
# shellcheck disable=SC1091
source "$CONDA_BASE/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV_NAME" 2>/dev/null \
    || die "conda env '$CONDA_ENV_NAME' missing. Run ./scripts/setup_local.sh first."

# --- Postgres (data persists; just (re)start the server) ---------------------
[[ -d "$PGDATA" ]] || die "No Postgres data dir at $PGDATA. Run ./scripts/setup_local.sh first."
if pg_ctl -D "$PGDATA" status >/dev/null 2>&1; then
    log "Postgres already running."
else
    log "Starting Postgres..."
    pg_ctl -D "$PGDATA" -l "$PGDATA/server.log" -o "-k /tmp -p ${PGPORT}" -w start
fi

# --- Redis (in-memory; placement re-seeded below) ----------------------------
if redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1; then
    log "Redis already running."
else
    log "Starting Redis..."
    redis-server --daemonize yes --port "$REDIS_PORT" --dir /tmp
    sleep 1
fi

# --- migrations (safe / idempotent) ------------------------------------------
log "Applying migrations..."
python manage.py migrate --noinput >/dev/null

# --- re-seed live placement into Redis ---------------------------------------
log "Re-seeding live placement into Redis (lost on reboot)..."
DJANGO_SETTINGS_MODULE=cmdb.settings python scripts/seed_placement_from_fixtures.py >/dev/null 2>&1 \
    || die "placement seed failed (scripts/seed_placement_from_fixtures.py)"
python manage.py link_placement_nodes >/dev/null 2>&1 || warn "link_placement_nodes had warnings"
python manage.py populate_architecture_from_redis >/dev/null 2>&1 || warn "populate_architecture_from_redis had warnings"

KEYS=$(redis-cli -p "$REDIS_PORT" --scan --pattern 'env:*:placement' 2>/dev/null | wc -l)
log "Redis placement keys: ${KEYS}"

# --- server ------------------------------------------------------------------
PORT="${BIND##*:}"
case "$SERVE" in
    none)
        log "State recovered. Server not started (--no-serve)."
        ;;
    foreground)
        log "Starting Django on http://${BIND}/  (Ctrl-C to stop)"
        exec python manage.py runserver "$BIND"
        ;;
    background)
        mkdir -p logs
        if ss -ltn 2>/dev/null | grep -q ":${PORT} "; then
            log "A server is already listening on :${PORT} — not starting another."
        else
            log "Starting Django (detached) on http://${BIND}/ ..."
            # setsid so the server survives this script (and the terminal) exiting.
            setsid nohup python manage.py runserver "$BIND" --noreload \
                >> logs/runserver.log 2>&1 &
            sleep 3
        fi
        log "CMDB is up → http://${BIND}/   (DORA: /dora/)   logs: logs/runserver.log"
        ;;
esac
