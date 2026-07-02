# pktLog

Syslog ingest management and visualization platform. Receives syslog data over UDP/TCP, stores events in ClickHouse, and provides a React UI for search, alerting, and reporting.

Part of the **pktSuite** platform.

---

## Quick Start (Docker)

```bash
# 1. Pull and start
docker compose up -d

# Required env var — set before starting:
APP_ADMIN_PASSWORD=yourpassword docker compose up -d
```

HTTPS is available immediately at `https://<host>` with a self-signed certificate. Syslog ingest listens on port 514 (UDP + TCP).

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `APP_ADMIN_PASSWORD` | *(required)* | Admin user password |
| `APP_ADMIN_USER` | `admin` | Admin username |
| `APP_JWT_SECRET` | *(auto-generated)* | JWT signing secret |
| `APP_HTTP_PORT` | `80` | HTTP port |
| `APP_HTTPS_PORT` | `443` | HTTPS port |
| `APP_SYSLOG_PORT` | `514` | Syslog ingest port (UDP + TCP) |

ClickHouse is bundled via Docker Compose and requires no separate configuration.

### Data Persistence

All application data is stored in the `pktlog_data` Docker volume (`/app/data`):
- `pktlog.db` — SQLite: users, settings, alert rules
- `config.yaml` — runtime configuration (auto-managed)
- `certs/` — TLS certificate and key
- `logs/` — application logs

ClickHouse data is in the `clickhouse_data` volume.

### Custom TLS Certificate

Replace the auto-generated cert by mounting your own:

```yaml
volumes:
  - ./my-cert.pem:/app/data/certs/cert.pem:ro
  - ./my-key.pem:/app/data/certs/key.pem:ro
```

---

## Architecture

```
Syslog Collectors ──UDP/TCP──► pktLog Ingest Listener
                                     │
                               Parse + Enrich
                                     │
                               ClickHouse (pktlog.syslog_events)
                                     │
                          FastAPI Backend (HTTPS :443)
                                     │
                          React Frontend (SPA)
```

### Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI (Python 3.11) |
| Frontend | React + TypeScript + Tailwind |
| Log storage | ClickHouse |
| App database | SQLite (users, settings, alerts) |
| Auth | Local + SAML/Okta SSO |
| Ingest | Async UDP + TCP (RFC 3164 / RFC 5424) |

---

## Bare-Metal Installation

See [DEPLOYMENT.md](DEPLOYMENT.md) for bare-metal setup instructions.

---

## Placeholder Reference

Sensitive deployment data is replaced with placeholders throughout this repository:

| Placeholder | Meaning |
|---|---|
| `<PKT_SERVER_IP>` | pkt server IP address |
| `<COLLECTOR_A_HOST>` | Collector A IP/hostname |
| `<COLLECTOR_B_HOST>` | Collector B IP/hostname |
| `<PKT_SERVER_SSH_KEY>` | SSH key filename for pkt server |
| `<COLLECTOR_B_SSH_KEY>` | SSH key filename for Collector B |
| `<DEPLOY_USER>` | OS user running the service |
| `<INSTALL_DIR>` | Bare-metal install directory |
| `<ORG_NAME>` | Organization name |
| `<ORG_DOMAIN>` | Organization domain |

---

## CI/CD

GitHub Actions builds and pushes the Docker image to `ghcr.io/bsnwgit/pktlog` on every push to `feature/docker` or `main` that touches application files.
