#!/bin/bash
# pktLog install script — Ubuntu Server 22.04/24.04 LTS
# Usage: bash install.sh
# Override defaults with env vars, e.g.:
#   PKTLOG_INSTALL_DIR=/opt/pktlog PKTLOG_SERVICE_USER=pktlog bash install.sh

set -euo pipefail

INSTALL_DIR="${PKTLOG_INSTALL_DIR:-/opt/pktlog}"
LOG_DIR="${PKTLOG_LOG_DIR:-$INSTALL_DIR/logs}"
SERVICE_USER="${PKTLOG_SERVICE_USER:-$(whoami)}"
SERVICE_GROUP="${PKTLOG_SERVICE_GROUP:-$SERVICE_USER}"
VENV="$INSTALL_DIR/venv"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$SCRIPT_DIR"

echo "=== pktLog Installer ==="
echo "Install dir: $INSTALL_DIR"
echo "Service user: $SERVICE_USER"
echo ""

# ── 1. System packages ────────────────────────────────────────────────────────
echo "[1/9] Installing system packages..."
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    libxmlsec1-dev libxmlsec1-openssl xmlsec1 pkg-config gcc \
    curl ca-certificates gnupg apt-transport-https openssl

# ── 2. Create directories ─────────────────────────────────────────────────────
echo "[2/9] Creating directories..."
sudo mkdir -p "$INSTALL_DIR"
sudo mkdir -p "$LOG_DIR"
# Owned by the invoking user for now so the steps below don't need sudo;
# re-owned to $SERVICE_USER:$SERVICE_GROUP at the end (step 9).
sudo chown "$(whoami):$(whoami)" "$INSTALL_DIR" "$LOG_DIR"

# ── 3. Install ClickHouse ─────────────────────────────────────────────────────
echo "[3/9] Checking ClickHouse..."
if ! command -v clickhouse-server &>/dev/null; then
    echo "  Installing ClickHouse..."
    curl -fsSL https://packages.clickhouse.com/rpm/lts/repodata/repomd.xml.key \
        | sudo gpg --dearmor -o /usr/share/keyrings/clickhouse-keyring.gpg

    ARCH="$(dpkg --print-architecture)"
    echo "deb [signed-by=/usr/share/keyrings/clickhouse-keyring.gpg arch=${ARCH}] https://packages.clickhouse.com/deb stable main" \
        | sudo tee /etc/apt/sources.list.d/clickhouse.list > /dev/null

    sudo apt-get update
    sudo apt-get install -y clickhouse-server clickhouse-client
    sudo systemctl enable clickhouse-server
    sudo systemctl start clickhouse-server
    echo "  ClickHouse installed and started."
else
    echo "  ClickHouse already installed. Ensuring it's running..."
    sudo systemctl start clickhouse-server || true
fi

# Wait for ClickHouse to be ready
echo "  Waiting for ClickHouse..."
for i in {1..10}; do
    clickhouse-client --query "SELECT 1" &>/dev/null && break
    sleep 2
done

# ── 4. Initialize ClickHouse schema ──────────────────────────────────────────
echo "[4/9] Initializing ClickHouse schema..."
clickhouse-client --multiquery < "$REPO_DIR/clickhouse/schema.sql" && echo "  Schema applied."

# ── 5. Python virtualenv ──────────────────────────────────────────────────────
echo "[5/9] Setting up Python virtualenv..."
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$REPO_DIR/requirements.txt"
echo "  Python dependencies installed."

# ── 6. Copy app files ─────────────────────────────────────────────────────────
echo "[6/9] Copying application files..."
cp -r "$REPO_DIR/app"         "$INSTALL_DIR/"
cp -r "$REPO_DIR/migrations"  "$INSTALL_DIR/"
cp -r "$REPO_DIR/clickhouse"  "$INSTALL_DIR/"

# ── 7. Config file ────────────────────────────────────────────────────────────
echo "[7/9] Setting up config..."
if [ ! -f "$INSTALL_DIR/config.yaml" ]; then
    cp "$REPO_DIR/config.example.yaml" "$INSTALL_DIR/config.yaml"
    # Generate a random secret key
    SECRET=$(openssl rand -hex 32)
    sed -i "s/CHANGE_ME_generate_with_openssl_rand_hex_32/$SECRET/" "$INSTALL_DIR/config.yaml"
    sed -i "s#/opt/pktlog#$INSTALL_DIR#g" "$INSTALL_DIR/config.yaml"
    echo "  Config created at $INSTALL_DIR/config.yaml"
    echo "  !! Review and update cors_origins before production use !!"
else
    echo "  Config already exists — skipping."
fi

# ── 8. Initialize database + create admin user ───────────────────────────────
echo "[8/9] Initializing database and admin user..."
ADMIN_PASS=$(openssl rand -base64 12 | tr -d '/+=' | head -c 16)

"$VENV/bin/python3" - << PYEOF
import asyncio, sys
sys.path.insert(0, '$INSTALL_DIR')
import os; os.environ['PKTLOG_CONFIG'] = '$INSTALL_DIR/config.yaml'

from app.database import init_db
from app.auth.local import hash_password
import aiosqlite
from app.config import get_settings

async def setup():
    await init_db()
    async with aiosqlite.connect(get_settings().db_path) as db:
        hashed = hash_password('$ADMIN_PASS')
        await db.execute(
            "INSERT OR IGNORE INTO users (username, email, hashed_password, role) VALUES (?,?,?,?)",
            ('admin', 'admin@pktlog.local', hashed, 'admin')
        )
        await db.commit()
    print("  Database initialized.")

asyncio.run(setup())
PYEOF

# ── 9. Install systemd service ────────────────────────────────────────────────
echo "[9/9] Installing systemd service..."
# Re-own the install/log dirs to the service user before starting the service.
sudo chown -R "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR" "$LOG_DIR"
sed \
    -e "s#__INSTALL_DIR__#$INSTALL_DIR#g" \
    -e "s#__LOG_DIR__#$LOG_DIR#g" \
    -e "s#__SERVICE_USER__#$SERVICE_USER#g" \
    -e "s#__SERVICE_GROUP__#$SERVICE_GROUP#g" \
    "$REPO_DIR/pktlog.service" | sudo tee /etc/systemd/system/pktlog.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable pktlog
sudo systemctl start pktlog

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║              pktLog installed successfully!              ║"
echo "╠══════════════════════════════════════════════════════════╣"
printf "║  URL:           https://%-34s║\n" "$(hostname -I | awk '{print $1}'):8768"
echo "║  Username:      admin                                     ║"
printf "║  Password:      %-43s║\n" "$ADMIN_PASS"
echo "║                                                            ║"
echo "║  SAVE THESE CREDENTIALS — they won't be shown again!      ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "Next steps:"
echo "  1. Build the frontend (see README § Installation, step 8) — install.sh"
echo "     does not do this"
echo "  2. Open the firewall for the app port and syslog ingest port (see README)"
echo "  3. Log into pktLog and review Settings"
