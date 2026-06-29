#!/usr/bin/env bash
# sync_infra_services.sh — pull latest infrastructure-services and re-run the parser.
#
# Designed to be run as a daily cron job on a dev machine:
#   0 3 * * * /home/fer/projects/is-cmdb/scripts/sync_infra_services.sh >> /home/fer/projects/is-cmdb/logs/sync.log 2>&1
#
# The script is idempotent: safe to run manually at any time.
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$ROOT_DIR/infrastructure-services"
LOG_DIR="$ROOT_DIR/logs"
CONDA_ENV_NAME="cmdb"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

mkdir -p "$LOG_DIR"

# --- Resolve conda -------------------------------------------------------
CONDA_BASE="$(conda info --base 2>/dev/null)" || die "conda not found"
# shellcheck disable=SC1091
source "$CONDA_BASE/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV_NAME" 2>/dev/null || die "conda env '$CONDA_ENV_NAME' not found — run ./scripts/setup_local.sh"

# --- Clone or pull infrastructure-services -------------------------------
if [[ -d "$INFRA_DIR/.git" ]]; then
    log "Pulling latest infrastructure-services..."
    git -C "$INFRA_DIR" pull --ff-only
else
    log "Cloning infrastructure-services..."
    gh repo clone canonical/infrastructure-services "$INFRA_DIR"
fi

GIT_SHA="$(git -C "$INFRA_DIR" rev-parse HEAD)"
log "infrastructure-services at $GIT_SHA"

# --- Load DATABASE_URL from .env -----------------------------------------
if [[ -f "$ROOT_DIR/.env" ]]; then
    # shellcheck disable=SC2046
    export $(grep -v '^#' "$ROOT_DIR/.env" | grep DATABASE_URL | xargs)
fi
DATABASE_URL="${DATABASE_URL:-postgresql://cmdb:cmdb@localhost:5432/cmdb}"

# --- Run parser ----------------------------------------------------------
log "Running parser..."
python "$ROOT_DIR/parser/parser.py" \
    --source "$INFRA_DIR" \
    --database-url "$DATABASE_URL"

log "Sync complete."
