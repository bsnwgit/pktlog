#!/bin/bash
# pktLog install script — Ubuntu Server 22.04/24.04 LTS
# Usage: bash install.sh
# Prompts for the install directory (default /opt/pktlog) and port (default
# 8768) when run interactively.
# Override defaults with env vars to skip the prompts, e.g.:
#   PKTLOG_INSTALL_DIR=/opt/pktlog PKTLOG_SERVICE_USER=pktlog PKTLOG_PORT=8768 bash install.sh

set -euo pipefail

if [ -z "${PKTLOG_INSTALL_DIR:-}" ] && [ -t 0 ]; then
    read -rp "Install directory [/opt/pktlog]: " INSTALL_DIR_INPUT
    INSTALL_DIR="${INSTALL_DIR_INPUT:-/opt/pktlog}"
else
    INSTALL_DIR="${PKTLOG_INSTALL_DIR:-/opt/pktlog}"
fi
if [ -z "${PKTLOG_PORT:-}" ] && [ -t 0 ]; then
    read -rp "Port [8768]: " PORT_INPUT
    PORT="${PORT_INPUT:-8768}"
else
    PORT="${PKTLOG_PORT:-8768}"
fi
LOG_DIR="${PKTLOG_LOG_DIR:-$INSTALL_DIR/logs}"
SERVICE_USER="${PKTLOG_SERVICE_USER:-$(whoami)}"
SERVICE_GROUP="${PKTLOG_SERVICE_GROUP:-$SERVICE_USER}"
VENV="$INSTALL_DIR/venv"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$SCRIPT_DIR"
LOCAL_IP="$(hostname -I | awk '{print $1}')"

echo "=== pktLog Installer ==="
echo "Install dir: $INSTALL_DIR"
echo "Service user: $SERVICE_USER"
echo "Port: $PORT"
echo ""

# ── 1. System packages ────────────────────────────────────────────────────────
echo "[1/10] Installing system packages..."
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    libxmlsec1-dev libxmlsec1-openssl xmlsec1 pkg-config gcc \
    curl ca-certificates gnupg apt-transport-https openssl

# ── 2. Create directories ─────────────────────────────────────────────────────
echo "[2/10] Creating directories..."
sudo mkdir -p "$INSTALL_DIR"
sudo mkdir -p "$LOG_DIR"
# Owned by the invoking user for now so the steps below don't need sudo;
# re-owned to $SERVICE_USER:$SERVICE_GROUP at the end (step 9).
sudo chown "$(whoami):$(whoami)" "$INSTALL_DIR" "$LOG_DIR"

# ── 3. Install ClickHouse ─────────────────────────────────────────────────────
echo "[3/10] Checking ClickHouse..."
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
echo "[4/10] Initializing ClickHouse schema..."
clickhouse-client --multiquery < "$REPO_DIR/clickhouse/schema.sql" && echo "  Schema applied."

# ── 5. Python virtualenv ──────────────────────────────────────────────────────
echo "[5/10] Setting up Python virtualenv..."
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$REPO_DIR/requirements.txt"
echo "  Python dependencies installed."

# ── 6. Copy app files ─────────────────────────────────────────────────────────
echo "[6/10] Copying application files..."
if [ "$REPO_DIR" = "$INSTALL_DIR" ]; then
    echo "  Install dir is the repo checkout itself — nothing to copy."
else
    cp -r "$REPO_DIR/app"         "$INSTALL_DIR/"
    cp -r "$REPO_DIR/migrations"  "$INSTALL_DIR/"
    cp -r "$REPO_DIR/clickhouse"  "$INSTALL_DIR/"
    cp -r "$REPO_DIR/docs"        "$INSTALL_DIR/"
    cp "$REPO_DIR/start.sh"       "$INSTALL_DIR/"
fi
chmod +x "$INSTALL_DIR/start.sh"

# ── 7. Config file ────────────────────────────────────────────────────────────
echo "[7/10] Setting up config..."
if [ ! -f "$INSTALL_DIR/config.yaml" ]; then
    cp "$REPO_DIR/config.example.yaml" "$INSTALL_DIR/config.yaml"
    # Generate a random secret key
    SECRET=$(openssl rand -hex 32)
    sed -i "s/CHANGE_ME_generate_with_openssl_rand_hex_32/$SECRET/" "$INSTALL_DIR/config.yaml"
    sed -i "s/^port: 8768/port: $PORT/" "$INSTALL_DIR/config.yaml"
    # Pin install_dir explicitly (app/config.py derives every other path —
    # db, logs, ingest journal, ssl, backups — from this by default).
    echo "install_dir: \"$INSTALL_DIR\"" >> "$INSTALL_DIR/config.yaml"
    echo "  Config created at $INSTALL_DIR/config.yaml"
    echo "  !! Review and update cors_origins before production use !!"
else
    echo "  Config already exists — skipping."
fi

# ── 8. Initialize database + create admin user ───────────────────────────────
echo "[8/10] Initializing database and admin user..."
ADMIN_PASS=$(openssl rand -base64 12 | tr -d '/+=' | head -c 16)

"$VENV/bin/python3" - << PYEOF
import asyncio, json, sys
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
            "INSERT OR IGNORE INTO users (username, email, hashed_password, role, is_default_admin) VALUES (?,?,?,?,1)",
            ('admin', 'admin@pktlog.local', hashed, 'admin')
        )

        # Seed exactly one collector — this host's own detected IP — instead
        # of leaving the registry empty or full of placeholder sample rows.
        local_ip = '$LOCAL_IP'
        if local_ip:
            await db.execute(
                "INSERT OR IGNORE INTO collector_registry (collector_ip, collector_name) VALUES (?, ?)",
                (local_ip, 'local')
            )
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES ('base_url', ?)",
                (json.dumps(f'http://{local_ip}:$PORT'),)
            )

        await db.commit()
    print("  Database initialized.")

asyncio.run(setup())
PYEOF

# ── 9. Build frontend ─────────────────────────────────────────────────────────
# Not installing Node.js itself here (see README Requirements — version
# management is left to the operator), but if it's already present (as the
# Quick Start instructs installing beforehand), just build it — there's no
# reason to leave this as a manual step when we can.
echo "[9/10] Building frontend..."
FRONTEND_BUILT=0
if command -v npm &>/dev/null; then
    ( cd "$REPO_DIR/frontend" && npm install --no-audit --no-fund && npm run build )
    if [ "$REPO_DIR/frontend/dist" != "$INSTALL_DIR/frontend/dist" ]; then
        mkdir -p "$INSTALL_DIR/frontend"
        rm -rf "$INSTALL_DIR/frontend/dist"
        cp -r "$REPO_DIR/frontend/dist" "$INSTALL_DIR/frontend/dist"
    fi
    FRONTEND_BUILT=1
    echo "  Frontend built and deployed."
else
    echo "  npm not found — skipping (Node.js 20.x is required; see README Requirements)."
    echo "  The web UI will return \"Not Found\" until you build it manually — see the"
    echo "  banner at the end of this script for the exact commands."
fi

# ── 10. Install systemd service ───────────────────────────────────────────────
echo "[10/10] Installing systemd service..."
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
printf "║  URL:           http://%-35s║\n" "$LOCAL_IP:$PORT"
echo "║  Username:      admin                                     ║"
printf "║  Password:      %-43s║\n" "$ADMIN_PASS"
echo "║                                                            ║"
echo "║  SAVE THESE CREDENTIALS — they won't be shown again!      ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
if [ "$FRONTEND_BUILT" -eq 0 ]; then
    echo "!! Frontend was NOT built (npm not found) — the web UI will show"
    echo "!! {\"detail\":\"Not Found\"} until you run:"
    echo "!!   cd $REPO_DIR/frontend && npm install && npm run build"
    if [ "$REPO_DIR/frontend/dist" != "$INSTALL_DIR/frontend/dist" ]; then
        echo "!!   mkdir -p $INSTALL_DIR/frontend && cp -r $REPO_DIR/frontend/dist $INSTALL_DIR/frontend/dist"
    fi
    echo "!!   sudo systemctl restart pktlog"
    echo ""
fi
echo "Next steps:"
echo "  1. Open the firewall for the app port and syslog ingest port (see README)"
echo "  2. Log into pktLog and review Settings"
