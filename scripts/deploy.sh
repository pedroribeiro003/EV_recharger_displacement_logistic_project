#!/usr/bin/env bash
# =============================================================================
# deploy.sh — update the application on the server (zero-downtime where possible)
#
# Run as root (or with sudo) on the server:
#   sudo bash scripts/deploy.sh
#
# What this does:
#   1. Stops the service
#   2. Syncs new code to /opt/ev-demand
#   3. Updates Python dependencies
#   4. Runs pending Alembic migrations
#   5. Restarts the service
# =============================================================================
set -euo pipefail

APP_USER="ev-demand"
APP_DIR="/opt/ev-demand"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE="ev-demand"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

[[ $EUID -ne 0 ]] && error "Run this script with sudo."
[[ ! -d "${APP_DIR}" ]] && error "${APP_DIR} not found. Run scripts/setup.sh first."

# ── 1. Stop service ───────────────────────────────────────────────────────────
info "Stopping ${SERVICE}..."
systemctl stop "${SERVICE}" || warn "Service was not running."

# ── 2. Sync code ──────────────────────────────────────────────────────────────
info "Syncing code to ${APP_DIR}..."
rsync -a --delete \
    --exclude=venv \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='.env' \
    --exclude='*.log' \
    "${REPO_DIR}/" "${APP_DIR}/"

# ── 3. Update dependencies ────────────────────────────────────────────────────
info "Updating Python dependencies..."
"${APP_DIR}/venv/bin/pip" install --upgrade pip -q
"${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt" -q

# ── 4. Migrations ─────────────────────────────────────────────────────────────
info "Running database migrations..."
cd "${APP_DIR}"
sudo -u "${APP_USER}" \
    env "$(cat ${APP_DIR}/.env | grep -v '^#' | xargs)" \
    "${APP_DIR}/venv/bin/alembic" upgrade head

# ── 5. Permissions ────────────────────────────────────────────────────────────
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

# ── 6. Restart service ────────────────────────────────────────────────────────
info "Starting ${SERVICE}..."
systemctl start "${SERVICE}"
sleep 2
systemctl status "${SERVICE}" --no-pager

echo ""
echo -e "${GREEN}Deploy concluido.${NC}"
echo "  Logs: sudo journalctl -u ${SERVICE} -f"
echo ""
