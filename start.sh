#!/bin/bash
# pktLog start wrapper — conditionally enables SSL
#
# Not used by pktlog.service (which invokes uvicorn directly); this is for
# running pktlog manually outside systemd, e.g. for local development.
#
# Auto-detects $PKTLOG_INSTALL_DIR/ssl/server.crt + server.key on startup.
# To enable HTTPS: upload cert/key via Settings → Integrations → SSL / TLS, then restart.
# To disable HTTPS: remove the cert via Settings (or rm $PKTLOG_INSTALL_DIR/ssl/server.*), then restart.
#
# Override defaults with env vars, e.g.:
#   PKTLOG_INSTALL_DIR=/opt/pktlog PKTLOG_PORT=8768 bash start.sh

set -euo pipefail

INSTALL_DIR="${PKTLOG_INSTALL_DIR:-/opt/pktlog}"
PORT="${PKTLOG_PORT:-8768}"
WORKERS="${PKTLOG_WORKERS:-2}"

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
