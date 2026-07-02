#!/bin/bash
# pktLog start wrapper — conditionally enables SSL
# Auto-detects <INSTALL_DIR>/ssl/server.crt + server.key on startup.
# To enable HTTPS: upload cert/key via Settings → Integrations → SSL / TLS, then restart.
# To disable HTTPS: remove the cert via Settings (or rm <INSTALL_DIR>/ssl/server.*), then restart.

SSL_CERT="<INSTALL_DIR>/ssl/server.crt"
SSL_KEY="<INSTALL_DIR>/ssl/server.key"
UVICORN="<INSTALL_DIR>/venv/bin/uvicorn"
ARGS="app.main:app --host 0.0.0.0 --port 8768 --workers 2 --log-level info --no-access-log"

if [ -f "$SSL_CERT" ] && [ -f "$SSL_KEY" ]; then
    echo "[pktlog] SSL detected — starting HTTPS"
    exec "$UVICORN" $ARGS --ssl-certfile "$SSL_CERT" --ssl-keyfile "$SSL_KEY"
else
    echo "[pktlog] No SSL files — starting HTTP"
    exec "$UVICORN" $ARGS
fi
