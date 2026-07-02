# ── Stage 1: Build React frontend ─────────────────────────────────────────────
FROM node:20-slim AS frontend-builder
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim

# Install system packages:
#   openssl, xmlsec1, libxmlsec1-openssl — runtime requirements
#   libxmlsec1-dev, pkg-config, gcc      — build-time only (purged after pip install)
RUN apt-get update && apt-get install -y --no-install-recommends \
        openssl \
        xmlsec1 \
        libxmlsec1-openssl \
        libxmlsec1-dev \
        pkg-config \
        gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove gcc pkg-config libxmlsec1-dev \
    && rm -rf /var/lib/apt/lists/*

# App source
COPY app/ ./app/
COPY migrations/ ./migrations/

# Frontend build
COPY --from=frontend-builder /build/dist ./frontend/dist/

# Persistent data directory (overridden by mounted volume at runtime)
RUN mkdir -p /app/data/logs /app/data/certs

COPY docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# HTTP  HTTPS  Syslog UDP  Syslog TCP
EXPOSE 80 443 514/udp 514/tcp

VOLUME ["/app/data"]

ENTRYPOINT ["/entrypoint.sh"]
