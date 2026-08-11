#!/bin/bash
# pktLog start wrapper — conditionally enables SSL
#
# This is what pktlog.service's ExecStart actually runs. It also works fine
# invoked manually outside systemd, e.g. for local development.
#
# Auto-detects $PKTLOG_INSTALL_DIR/ssl/server.crt + server.key on startup.
# To enable HTTPS: upload cert/key via Settings → Security → SSL / TLS, then restart.
# To disable HTTPS: remove the cert via Settings (or rm $PKTLOG_INSTALL_DIR/ssl/server.*), then restart.
#
# Port comes from config.yaml's `port:` field by default (Settings → General
# → Port writes there) — set $PKTLOG_PORT to override without touching it,
# e.g. for one-off local dev:
#   PKTLOG_INSTALL_DIR=/opt/pktlog PKTLOG_PORT=8768 bash start.sh

set -euo pipefail

INSTALL_DIR="${PKTLOG_INSTALL_DIR:-/opt/pktlog}"
export PKTLOG_INSTALL_DIR="$INSTALL_DIR"
CONFIG_YAML="$INSTALL_DIR/config.yaml"
if [ -n "${PKTLOG_PORT:-}" ]; then
    PORT="$PKTLOG_PORT"
elif [ -f "$CONFIG_YAML" ]; then
    PORT="$(grep -E '^port:' "$CONFIG_YAML" | head -1 | sed -E 's/^port:[[:space:]]*([0-9]+).*/\1/')"
fi
PORT="${PORT:-8768}"
# Single process by default, matching every sibling pkt* app.
#
# pktlog previously defaulted to 2 workers, which made it the only app in the
# suite running its SQLite sidecar DB from two OS processes at once. Under
# uvicorn's multi-worker model the FastAPI lifespan hook runs once per worker,
# so the alert engine, alert cleanup, backup scheduler, SQLite log handler and
# ingest BatchWriter each ran in duplicate, writing the same file concurrently.
# pktlog.db corrupted under that arrangement on 2026-08-04 and again on
# 2026-08-09; no sibling app has ever corrupted. The second worker also bought
# very little — the syslog listener can only bind the port once, so the second
# worker logs "port already bound" and serves HTTP only.
#
# Override with PKTLOG_WORKERS if more HTTP concurrency is ever needed, but
# give the background jobs a single-leader guard first.
WORKERS="${PKTLOG_WORKERS:-1}"

SSL_CERT="$INSTALL_DIR/ssl/server.crt"
SSL_KEY="$INSTALL_DIR/ssl/server.key"
UVICORN="$INSTALL_DIR/venv/bin/uvicorn"
ARGS="app.main:app --host 0.0.0.0 --port $PORT --workers $WORKERS --log-level info --no-access-log"

if [ -f "$SSL_CERT" ] && [ -f "$SSL_KEY" ]; then
    echo "[pktlog] SSL detected — starting HTTPS"
    exec "$UVICORN" $ARGS --ssl-certfile "$SSL_CERT" --ssl-keyfile "$SSL_KEY"
else
    echo "[pktlog] No SSL files — starting HTTP"
    exec "$UVICORN" $ARGS
fi
