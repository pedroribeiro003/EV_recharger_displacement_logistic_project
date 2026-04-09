#!/usr/bin/env bash
# =============================================================================
# setup.sh — first-time server provisioning (Ubuntu 22.04+)
#
# Run as root (or with sudo):
#   sudo bash scripts/setup.sh
#
# What this does:
#   1. Installs PostgreSQL 15 + PostGIS
#   2. Creates the 'ev_demand' database and user
#   3. Creates the system user 'ev-demand'
#   4. Installs the app into /opt/ev-demand
#   5. Creates the Python virtualenv and installs dependencies
#   6. Installs and enables the systemd service
# =============================================================================
set -euo pipefail

APP_USER="ev-demand"
APP_DIR="/opt/ev-demand"
DB_NAME="ev_demand"
DB_USER="ev_demand"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

[[ $EUID -ne 0 ]] && error "Run this script with sudo."

# ── 1. System packages ────────────────────────────────────────────────────────
info "Installing system packages..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3-pip \
    postgresql-15 postgresql-15-postgis-3 \
    libpq-dev \
    curl git

# ── 2. PostgreSQL setup ───────────────────────────────────────────────────────
info "Configuring PostgreSQL..."
systemctl enable --now postgresql

DB_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

sudo -u postgres psql <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
    CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASSWORD}';
  END IF;
END
\$\$;

SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}') \gexec

GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};
SQL

sudo -u postgres psql -d "${DB_NAME}" -c "CREATE EXTENSION IF NOT EXISTS postgis;"

info "Database '${DB_NAME}' ready."

# ── 3. System user ────────────────────────────────────────────────────────────
info "Creating system user '${APP_USER}'..."
if ! id "${APP_USER}" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "${APP_USER}"
fi

# ── 4. Application directory ──────────────────────────────────────────────────
info "Installing app to ${APP_DIR}..."
mkdir -p "${APP_DIR}"
rsync -a --exclude=venv --exclude='.git' --exclude='__pycache__' \
    "${REPO_DIR}/" "${APP_DIR}/"

# ── 5. Virtualenv + dependencies ──────────────────────────────────────────────
info "Creating Python virtualenv..."
python3.11 -m venv "${APP_DIR}/venv"
"${APP_DIR}/venv/bin/pip" install --upgrade pip -q
"${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt" -q

# ── 6. Environment file ───────────────────────────────────────────────────────
ENV_FILE="${APP_DIR}/.env"
if [[ ! -f "${ENV_FILE}" ]]; then
    info "Creating .env from template..."
    cp "${APP_DIR}/.env.example" "${ENV_FILE}"
    # Inject the generated DB credentials
    sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+psycopg2://${DB_USER}:${DB_PASSWORD}@localhost:5432/${DB_NAME}|" "${ENV_FILE}"
    sed -i "s|^DB_PASSWORD=.*|DB_PASSWORD=${DB_PASSWORD}|" "${ENV_FILE}"
    warn "Review ${ENV_FILE} and fill in any missing API keys before starting the service."
else
    warn ".env already exists at ${ENV_FILE} — skipping generation."
fi

# ── 7. Permissions ────────────────────────────────────────────────────────────
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
chmod 600 "${ENV_FILE}"

# ── 8. Alembic migrations ─────────────────────────────────────────────────────
info "Running database migrations..."
cd "${APP_DIR}"
sudo -u "${APP_USER}" \
    env "$(cat ${ENV_FILE} | grep -v '^#' | xargs)" \
    "${APP_DIR}/venv/bin/alembic" upgrade head

# ── 9. Systemd service ────────────────────────────────────────────────────────
info "Installing systemd service..."
cp "${APP_DIR}/deploy/ev-demand.service" /etc/systemd/system/ev-demand.service
systemctl daemon-reload
systemctl enable ev-demand

echo ""
echo -e "${GREEN}======================================================${NC}"
echo -e "${GREEN}  Setup complete!${NC}"
echo -e "${GREEN}======================================================${NC}"
echo ""
echo "  Start the service:   sudo systemctl start ev-demand"
echo "  Check status:        sudo systemctl status ev-demand"
echo "  Follow logs:         sudo journalctl -u ev-demand -f"
echo ""
echo "  DB credentials saved to: ${ENV_FILE}"
echo ""
