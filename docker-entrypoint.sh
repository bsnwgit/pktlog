#!/bin/bash
set -euo pipefail

# ── Required env vars ─────────────────────────────────────────────────────────
if [ -z "${APP_ADMIN_PASSWORD:-}" ]; then
    echo "ERROR: APP_ADMIN_PASSWORD is required but not set." >&2
    exit 1
fi

# ── Defaults ──────────────────────────────────────────────────────────────────
APP_HTTP_PORT="${APP_HTTP_PORT:-80}"
APP_HTTPS_PORT="${APP_HTTPS_PORT:-443}"
APP_SYSLOG_PORT="${APP_SYSLOG_PORT:-514}"
APP_ADMIN_USER="${APP_ADMIN_USER:-admin}"
CLICKHOUSE_HOST="${CLICKHOUSE_HOST:-clickhouse}"
CLICKHOUSE_PORT="${CLICKHOUSE_PORT:-9000}"
CLICKHOUSE_DB="${CLICKHOUSE_DB:-pktlog}"
CLICKHOUSE_USER="${CLICKHOUSE_USER:-default}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-}"

# Auto-generate JWT secret if not provided
if [ -z "${APP_JWT_SECRET:-}" ]; then
    APP_JWT_SECRET=$(openssl rand -hex 32)
fi

export PKTLOG_CONFIG=/app/data/config.yaml

# ── Seed config on first run ──────────────────────────────────────────────────
if [ ! -f "$PKTLOG_CONFIG" ]; then
    echo "[entrypoint] First run — seeding $PKTLOG_CONFIG"
    mkdir -p /app/data
    cat > "$PKTLOG_CONFIG" <<EOF
# pktLog runtime configuration — managed by Docker entrypoint
host: "0.0.0.0"
port: ${APP_HTTP_PORT}
workers: 2
debug: false

db_path: "/app/data/pktlog.db"
duckdb_path: "/app/data/pktlog_data.duckdb"

clickhouse_host: "${CLICKHOUSE_HOST}"
clickhouse_port: ${CLICKHOUSE_PORT}
clickhouse_database: "${CLICKHOUSE_DB}"
clickhouse_user: "${CLICKHOUSE_USER}"
clickhouse_password: "${CLICKHOUSE_PASSWORD}"

syslog_port: ${APP_SYSLOG_PORT}

secret_key: "${APP_JWT_SECRET}"

cors_origins:
  - "*"

log_level: "info"
log_file: "/app/data/logs/pktlog.log"
EOF
else
    echo "[entrypoint] Existing config found — syncing env vars"
    python3 - <<PYEOF
import yaml, os, sys

cfg_path = "/app/data/config.yaml"
with open(cfg_path) as f:
    cfg = yaml.safe_load(f) or {}

cfg["secret_key"]          = os.environ["APP_JWT_SECRET"]
cfg["clickhouse_host"]     = os.environ.get("CLICKHOUSE_HOST", "clickhouse")
cfg["clickhouse_port"]     = int(os.environ.get("CLICKHOUSE_PORT", "9000"))
cfg["clickhouse_database"] = os.environ.get("CLICKHOUSE_DB", "pktlog")
cfg["clickhouse_user"]     = os.environ.get("CLICKHOUSE_USER", "default")
cfg["clickhouse_password"] = os.environ.get("CLICKHOUSE_PASSWORD", "")
cfg["db_path"]             = "/app/data/pktlog.db"
cfg["duckdb_path"]         = "/app/data/pktlog_data.duckdb"

with open(cfg_path, "w") as f:
    yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)

print("[entrypoint] Config synced.")
PYEOF
fi

# ── SSL certificate ────────────────────────────────────────────────────────────
CERT_FILE="/app/data/certs/cert.pem"
KEY_FILE="/app/data/certs/key.pem"

if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
    echo "[entrypoint] Generating self-signed SSL certificate..."
    mkdir -p /app/data/certs
    openssl req -x509 -newkey rsa:4096 \
        -keyout "$KEY_FILE" -out "$CERT_FILE" \
        -days 3650 -nodes -subj "/CN=pktlog"
    echo "[entrypoint] SSL certificate generated."
fi

# ── Wait for ClickHouse ────────────────────────────────────────────────────────
echo "[entrypoint] Waiting for ClickHouse at ${CLICKHOUSE_HOST}:${CLICKHOUSE_PORT}..."
for i in $(seq 1 30); do
    if python3 -c "
import socket, sys
try:
    s = socket.create_connection(('${CLICKHOUSE_HOST}', ${CLICKHOUSE_PORT}), timeout=2)
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
        echo "[entrypoint] ClickHouse is ready."
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "[entrypoint] WARNING: ClickHouse not reachable after 60s — continuing anyway." >&2
    fi
    sleep 2
done

# ── Seed / update admin user ──────────────────────────────────────────────────
echo "[entrypoint] Seeding admin user..."
ADMIN_USER="$APP_ADMIN_USER" ADMIN_PASS="$APP_ADMIN_PASSWORD" python3 - <<'PYEOF'
import asyncio, os, sys
sys.path.insert(0, "/app")
os.environ["PKTLOG_CONFIG"] = "/app/data/config.yaml"

from app.database import init_db
import aiosqlite, bcrypt

async def seed():
    await init_db()
    username = os.environ["ADMIN_USER"]
    password = os.environ["ADMIN_PASS"]
    pwd_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    async with aiosqlite.connect("/app/data/pktlog.db") as db:
        row = await db.execute_fetchall(
            "SELECT id FROM users WHERE username=?", (username,)
        )
        if not row:
            await db.execute(
                "INSERT INTO users (username, hashed_password, role, is_active) VALUES (?,?,?,1)",
                (username, pwd_hash, "admin"),
            )
            await db.commit()
            print(f"[entrypoint] Admin user '{username}' created.")
        else:
            await db.execute(
                "UPDATE users SET hashed_password=?, is_active=1 WHERE username=?",
                (pwd_hash, username),
            )
            await db.commit()
            print(f"[entrypoint] Admin user '{username}' updated.")

asyncio.run(seed())
PYEOF

# ── Store SSL cert paths in SQLite so UI shows them correctly ─────────────────
python3 - <<'PYEOF'
import asyncio, os, json, sys
sys.path.insert(0, "/app")
os.environ["PKTLOG_CONFIG"] = "/app/data/config.yaml"
import aiosqlite

async def set_ssl():
    async with aiosqlite.connect("/app/data/pktlog.db") as db:
        for key, val in [
            ("ssl_enabled",  json.dumps(True)),
            ("ssl_certfile", json.dumps("/app/data/certs/cert.pem")),
            ("ssl_keyfile",  json.dumps("/app/data/certs/key.pem")),
        ]:
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, val),
            )
        await db.commit()
    print("[entrypoint] SSL settings written to DB.")

asyncio.run(set_ssl())
PYEOF

# ── Start HTTP server (background) ────────────────────────────────────────────
echo "[entrypoint] Starting HTTP on port ${APP_HTTP_PORT}..."
uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${APP_HTTP_PORT}" \
    --workers 1 \
    --log-level info \
    --no-access-log &

# ── Start HTTPS server (foreground — Docker tracks this PID) ──────────────────
echo "[entrypoint] Starting HTTPS on port ${APP_HTTPS_PORT}..."
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${APP_HTTPS_PORT}" \
    --workers 2 \
    --ssl-certfile "$CERT_FILE" \
    --ssl-keyfile "$KEY_FILE" \
    --log-level info \
    --no-access-log
