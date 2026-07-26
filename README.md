# pktLog

<p align="center">
  <img src="frontend/public/logos/lockup-256h.png" alt="pktLog" height="64">
</p>

Syslog ingest management and visualization platform. Receives syslog data over UDP/TCP, stores events in ClickHouse, and provides a React UI for search, alerting, and reporting.

Part of the **pktSuite** platform (SSO with pktHub/pktFlow via a shared `suite_token`).

---

## Quick Start

Requires a fresh Ubuntu Server 22.04/24.04 LTS host with `sudo` access, and Node.js 20.x LTS installed for the frontend build (not installed by `install.sh` — see [Requirements](#requirements)).

```bash
# 1. Clone the repository
git clone https://github.com/bsnwgit/pktlog.git
cd pktlog

# 2. Install Node.js 20.x LTS first if it isn't already present (see
#    Requirements below) — install.sh builds the frontend automatically
#    when npm is on PATH, and only falls back to a manual build otherwise.

# 3. Run the installer. Interactively, it prompts for the install directory
#    (default /opt/pktlog) and the app port (default 8768); it then handles
#    system packages, ClickHouse, Python deps, ClickHouse schema, config.yaml
#    + secret key, admin user (random password), one seeded collector entry
#    (this host's own IP), the frontend build (if npm is present), and the
#    systemd service (installed + started).
bash install.sh
# Prints the admin username/password at the end — save it, it is not shown again.

# 4. If npm was NOT found during install, install.sh prints the exact
#    fallback commands to build the frontend manually and restart the
#    service — see Installation § 8 below.

# 5. Open the firewall (adjust if you chose a different port, or
#    PKTLOG_SYSLOG_PORT differs from the 5514 default)
sudo ufw allow 8768/tcp
sudo ufw allow 5514/tcp
sudo ufw allow 5514/udp

# 6. Open http://<server-ip>:<port> and log in with the admin credentials
#    from step 3
```

For a fully manual walkthrough of what `install.sh` does (e.g. to customize the install path or run steps individually), see [Installation](#installation).

### Environment variables

All settings in `config.example.yaml` can also be passed as `PKTLOG_*` environment variables instead of editing `config.yaml` — environment variables take priority. Commonly used ones:

| Variable | Default | Description |
|----------|---------|-------------|
| `PKTLOG_CONFIG` | (none) | Path to `config.yaml` to load |
| `PKTLOG_INSTALL_DIR` | (none) | Install directory `install.sh` deployed to; used by `pktlog.service` |
| `PKTLOG_HOST` | `0.0.0.0` | Bind address |
| `PKTLOG_PORT` | `8768` | Listen port (HTTP; HTTPS if SSL cert configured) |
| `PKTLOG_DB_PATH` | `/opt/pktlog/pktlog.db` | SQLite app database path |
| `PKTLOG_CLICKHOUSE_HOST` / `_PORT` / `_DATABASE` / `_USER` / `_PASSWORD` | `localhost` / `9000` / `pktlog` / `default` / `` | ClickHouse connection |
| `PKTLOG_SYSLOG_PORT` | `5514` | Syslog ingest port (UDP + TCP) |
| `PKTLOG_SECRET_KEY` | (required) | JWT signing key — `openssl rand -hex 32` |
| `PKTLOG_CORS_ORIGINS` | `["*"]` | Restrict to your dashboard origin in production |
| `PKTLOG_LOG_LEVEL` / `PKTLOG_LOG_FILE` | `info` / `/opt/pktlog/logs/pktlog.log` | Logging |

---

## Architecture

```
Syslog Collectors ──UDP/TCP:5514──► pktLog Ingest Listener
                                          │
                                    Parse + Enrich
                                    (RFC 3164/5424 → org/group/site)
                                          │
                                    ClickHouse (pktlog.syslog_events)
                                          │
                                    FastAPI Backend (:8768)
                                          │
                                    React Frontend (SPA)
```

### Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI (Python 3.11) |
| Frontend | React + TypeScript + Tailwind |
| Log storage | ClickHouse |
| App database | SQLite (users, settings, alerts, device/collector registry) |
| Auth | Local + SAML/Okta SSO + pktSuite `suite_token` |
| Ingest | Async UDP + TCP (RFC 3164 / RFC 5424) |

### Collectors are an allowlist, not just labels

A device can be actively sending syslog data on the wire, but **nothing is stored until its IP is added under Settings → Collectors and marked enabled.** Data from an unregistered or disabled `collector_ip` is dropped at ingest (`app/ingest/normalizer.py`), not just missing hierarchy metadata — this is intentional, so a stray/misconfigured device on the network can't silently fill up storage. An unregistered sender instead raises an "Unknown collector" alert with a one-click link to pre-fill its registration form.

Settings → Collectors also has **Export CSV / Import CSV / template-download** buttons for provisioning many collectors at once instead of one at a time (columns: `collector_ip, collector_name, org, log_group, site, notes, enabled`). Duplicate IPs are skipped on import, not overwritten — use the existing Edit action for changes to an entry already in the registry.

### Timestamps and device timezone

RFC 3164 syslog (`MMM DD HH:MM:SS`, no timezone marker) is interpreted using the app's configured **Timezone** setting (Settings → General) as the device's local clock, then converted to UTC for storage — not assumed to already be UTC. Many real devices (UniFi APs/gateways observed in practice) log in local time, and getting this wrong silently shifts every such event by the zone offset. The `received_at` column is always the server's own UTC receipt time regardless of this setting, and is the field to check first if stored `timestamp` values ever look wrong.

Alert-event and user last-login timestamps (`alert_events.fired_at`, `users.last_login`) are stored as naive UTC and are explicitly normalized before being parsed for display, so they render correctly in the configured display timezone regardless of the browser's own system timezone.

### Application Logs time-range filtering

The Application Logs page (search + level filter) also has a time-range dropdown — 1h/6h/24h/7d/30d/All time, plus **Custom range…** with two date/time pickers (defaulting to today, 12:00 AM–11:59 PM). The custom range validates that the end is after the start (same-day-with-earlier-end-time counts as invalid too) and disallows future times on either side, showing an inline error instead of silently applying an impossible filter.

Both Application Logs and Syslog Explorer paginate server-side (50/page and 100/page respectively) with a page-number bar above the table: a sliding window of 5 numbers that follows the current page (Next from page 5 moves to 6-10, Prev the same way in reverse), a `1 ..` shortcut back to page 1 once past the first block, and a `.. N` shortcut to the last page.

### Alert rules bulk import/export

Alerts → Rules has Export CSV / Import CSV / template-download buttons alongside "+ New rule", for provisioning many rules at once. Columns: `name, description, rule_type, conditions, time_window_min, severity, channels, cooldown_min, enabled` — `conditions` round-trips as a JSON object string (shape depends on `rule_type`), `channels` as a comma-separated column drawn from the six supported values: `inapp, slack, email, pagerduty, webhook, tracecat` (e.g. `inapp,slack`).

### Alert Investigate button

Every active/history alert card has an **Investigate ↗** button that jumps straight to Syslog Explorer, pre-filtered to the alert's `collector_ip` (a new `collector_ip` search param/filter, distinct from the existing name-based `collector_name` filter) and a time window around when it fired. The "Unknown collector" registration link (Settings → Collectors, pre-filled IP) still appears alongside it for `new_host` alerts.

### `dest_ip` — destination IP from netfilter-style logs

Alongside `source_ip`, the syslog schema has a `dest_ip` column parsed from a `DST=<ip>` key/value pair embedded in the message body (firewall/netfilter-style log lines). It's a separate concept from `collector_ip`/`source_ip` and shows up as its own column and filter in Syslog Explorer. Most syslog lines have no destination-IP concept at all, so `dest_ip` is blank for them — that's expected, not a parsing failure.

### IP intelligence / reputation lookup

Every public IP address shown anywhere in the UI (Syslog Explorer, Dashboard, Alerts) is clickable — it opens a lookup panel combining [ipinfo.io](https://ipinfo.io) (geolocation/ASN/hostname, plus company/privacy/abuse-contact on paid plans), [ipapi.is](https://ipapi.is) (geolocation, ASN/org, company, abuse contact, VPN/proxy/Tor/datacenter/abuser detection — all in one call, no plan gating), [AbuseIPDB](https://www.abuseipdb.com) (abuse confidence score, report history), and [MXToolbox](https://mxtoolbox.com) (reverse DNS/PTR, ASN, and a blacklist/RBL check) via `GET /api/ip-info/{ip}`, all four called concurrently. Private/loopback/link-local/multicast addresses aren't clickable — external providers have nothing useful to say about them.

This is **per-user**, not a global app setting: each user adds their own API keys under **Settings → User Keys**, and lookups run under the logged-in user's own keys/quota. Five providers can have a key stored and tested there (AbuseIPDB, ipinfo.io, ipapi.is, MXToolbox, IPQualityScore), but only four of them are actually used by the lookup panel today — an IPQualityScore key can be saved and tested but isn't consumed anywhere yet.

MXToolbox's other commands — email/DNS record checks (SPF, DMARC, DKIM, MX, DNS, TXT, SOA, BIMI, MTA-STS, TLSRPT, A, AAAA) and active probes (ping, traceroute, TCP/HTTP/HTTPS/SMTP connect, run from MXToolbox's own infrastructure) — are reachable via `POST /api/mxtoolbox/lookup` (`{command, argument, port?}`) but aren't surfaced in the lookup panel yet; that's backend-only reach for now.

### AI Assistant

A floating chat button (bottom corner, every page) opens a slide-in drawer backed by Claude (`POST /api/ai/chat`) — ask it to help interpret log volume, severity/facility patterns, or investigate an alert; it receives the current page's context (recent events, collector status, alert summaries) alongside the question. Requires an Anthropic API key configured at **Settings → Security → AI Assistant**, where the model (Haiku / Sonnet / Opus) is also selectable. Until a key is set, the assistant reports itself as not configured rather than failing silently.

### Contextual help throughout the UI

Small "?" buttons next to section headers (Dashboard, Alerts, Logs, Syslog Explorer, and most Settings sections — Auth, Suite Integration, AI Assistant, Notifications, Data, etc.) open a short explainer of how that feature actually behaves — e.g. what a toggle does, what a bulk-import column means, what "Send Test" actually sends. Worth checking before assuming default behavior when a setting's effect isn't obvious from its label alone.

### Notification channels

Alert rules can dispatch to **six** channels, each configured under **Settings → Notifications**: in-app (`inapp`, the alert itself), Slack (incoming webhook), Email (SMTP), PagerDuty (Events API v2), a generic Webhook (Jinja2-templated payload), and TraceCat SOAR. Each channel has a real "Send Test" button that performs an actual dispatch (real Slack post, real SMTP send, etc.) using whatever's currently filled in, even if unsaved — not a dry run.

### User roles

Three roles: `admin` (full access, incl. user management), `analyst` (read + export, most write actions), `viewer` (read-only). When a request arrives via a pktHub-issued `suite_token`/SSO session, pktHub's own roles map onto these: `admin`→`admin`, `analyst`→`analyst`, and pktHub's `viewer`→pktlog's `analyst` (pktlog's own SSO role map treats "viewer" over SSO as read-only-but-still-useful rather than fully locked down — see `_SUITE_ROLE_MAP` in `app/dependencies.py`). A **locally-created** `viewer` user is genuinely read-only.

---

## Requirements

| Component | Version | Notes |
|-----------|---------|-------|
| OS | Ubuntu Server 22.04 LTS or 24.04 LTS | systemd required |
| Python | 3.10+ (ships with Ubuntu 22.04/24.04) | venv created via `python3-venv` |
| ClickHouse | 24.x+ (installed by `install.sh` from the official apt repo) | |
| Node.js | 20.x LTS | Frontend build only, not installed by `install.sh` |
| npm | 10+ | Frontend build only |
| System packages | `python3-venv`, `python3-pip`, `libxmlsec1-dev`, `libxmlsec1-openssl`, `xmlsec1`, `pkg-config`, `gcc`, `openssl`, `curl`, `ca-certificates`, `gnupg`, `apt-transport-https` | Installed by `install.sh`; `libxmlsec1*`/`pkg-config`/`gcc` are required to build `python3-saml`'s xmlsec bindings |

Node.js is not installed by `install.sh` — install it yourself before the frontend build step, e.g. via [NodeSource](https://github.com/nodesource/distributions):

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
```

### Python packages

See [requirements.txt](requirements.txt). Key dependencies:

- `fastapi`, `uvicorn[standard]` — web framework
- `clickhouse-driver` — ClickHouse backend (default); `duckdb` is an alternate/experimental backend
- `aiosqlite` — app database
- `python-jose[cryptography]`, `passlib[bcrypt]` — JWT auth
- `python3-saml`, `authlib` — SAML/OIDC SSO (Okta)
- `anthropic` — AI assistant (optional, requires API key in Settings)

### Frontend

React 18, TypeScript, Vite, Tailwind CSS, Recharts.

---

## Installation

`install.sh` (see [Quick Start](#quick-start)) automates everything below, **including step 8 (build the frontend)** as long as `npm` is already on `PATH` when it runs — it falls back to printing the manual build commands only if `npm` isn't found. **Step 11 (open the firewall)** is always manual. This section is the full manual walkthrough — useful to customize the install, run steps individually, or understand what the script does.

### 1. Clone the repository

```bash
git clone https://github.com/bsnwgit/pktlog.git
cd pktlog
```

All commands below assume you're in the repo root unless otherwise noted.

### 2. Create the install directory

```bash
INSTALL_DIR=/opt/pktlog
sudo mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/logs"
sudo chown "$(whoami):$(whoami)" "$INSTALL_DIR" "$INSTALL_DIR/logs"
```

`/opt` is root-owned by default, so this needs `sudo`. Steps 5–8 below run as your regular user against this now-owned directory; step 9 re-owns everything to whichever user/group the systemd service runs as.

### 3. System packages + ClickHouse

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    libxmlsec1-dev libxmlsec1-openssl xmlsec1 pkg-config gcc \
    curl ca-certificates gnupg apt-transport-https openssl

# ClickHouse — official apt repo
curl -fsSL https://packages.clickhouse.com/rpm/lts/repodata/repomd.xml.key \
    | sudo gpg --dearmor -o /usr/share/keyrings/clickhouse-keyring.gpg
ARCH="$(dpkg --print-architecture)"
echo "deb [signed-by=/usr/share/keyrings/clickhouse-keyring.gpg arch=${ARCH}] https://packages.clickhouse.com/deb stable main" \
    | sudo tee /etc/apt/sources.list.d/clickhouse.list
sudo apt-get update
sudo apt-get install -y clickhouse-server clickhouse-client
sudo systemctl enable --now clickhouse-server
```

`libxmlsec1-dev`, `libxmlsec1-openssl`, `pkg-config`, and `gcc` are required to build `python3-saml`'s xmlsec native bindings.

### 4. Apply ClickHouse schema

```bash
clickhouse-client --multiquery < clickhouse/schema.sql
```

Creates the `pktlog` database and the `syslog_events` table. See the note at the top of [`clickhouse/schema.sql`](clickhouse/schema.sql) — this file was reconstructed from the app's insert code rather than an existing checked-in schema; if your running deployment's table differs, treat that as the source of truth.

### 5. Install Python dependencies

```bash
python3 -m venv /opt/pktlog/venv
/opt/pktlog/venv/bin/pip install -r requirements.txt
```

### 6. Copy application files

`pktlog.service` runs `uvicorn app.main:app` with `WorkingDirectory=/opt/pktlog`, so the app package must live there:

```bash
cp -r app migrations clickhouse /opt/pktlog/
```

### 7. Configure

```bash
cp config.example.yaml /opt/pktlog/config.yaml
# Edit config.yaml — set secret_key, db_path, cors_origins
openssl rand -hex 32   # use this as secret_key
```

**config.yaml reference:**

| Key | Default | Description |
|-----|---------|--------------|
| `host` | `0.0.0.0` | Bind address |
| `port` | `8768` | Listen port |
| `db_path` | `/opt/pktlog/pktlog.db` | SQLite database path |
| `clickhouse_host` | `localhost` | ClickHouse host |
| `clickhouse_port` | `9000` | ClickHouse native protocol port |
| `clickhouse_database` | `pktlog` | ClickHouse database name |
| `syslog_port` | `5514` | Syslog ingest port (UDP + TCP) |
| `secret_key` | **CHANGE THIS** | JWT signing key (32+ random bytes) |
| `cors_origins` | `["*"]` | Restrict to your dashboard origin in production |
| `log_file` | `/opt/pktlog/logs/pktlog.log` | Log path |

The initial `admin` user is created directly in SQLite (see step 9's Python snippet, or use `seed_admin.py` after install) — there is no `admin_user`/`admin_password` config.yaml field.

### 8. Build the frontend

Requires Node.js 20.x LTS. The frontend must be built on Linux — not on Windows (Windows `node_modules` lacks the Linux rollup native binary).

`install.sh` does this step for you automatically if `npm` is already installed when it runs — the commands below are only needed if you're doing a fully manual install, or if `npm` wasn't present at install time (in which case `install.sh` prints these same commands as a fallback and leaves the web UI returning `{"detail":"Not Found"}` until you run them):

```bash
cp -r frontend /tmp/pktlog-fe
cd /tmp/pktlog-fe
npm install
npm run build > /dev/null 2>&1 && echo "build ok" || echo "BUILD FAILED"
cp -r dist /opt/pktlog/frontend/dist
```

### 9. Initialize the database and create the admin user

```bash
/opt/pktlog/venv/bin/python3 - << 'PYEOF'
import asyncio, sys, os
sys.path.insert(0, '/opt/pktlog')
os.environ['PKTLOG_CONFIG'] = '/opt/pktlog/config.yaml'

from app.database import init_db
from app.auth.local import hash_password
import aiosqlite
from app.config import get_settings

async def setup():
    await init_db()
    async with aiosqlite.connect(get_settings().db_path) as db:
        hashed = hash_password('CHANGE_ME')
        await db.execute(
            "INSERT OR IGNORE INTO users (username, email, hashed_password, role) VALUES (?,?,?,?)",
            ('admin', 'admin@pktlog.local', hashed, 'admin')
        )
        await db.commit()

asyncio.run(setup())
PYEOF
```

Replace `CHANGE_ME` with a real password before running this. `init_db()` also applies all `migrations/*.sql` files (idempotent — safe to call on every startup, which is what `app/main.py` does).

`install.sh` itself does more than this minimal snippet: it generates a random admin password (`openssl rand -base64 12`, printed once at the end of the run — this snippet's hardcoded password is only for a fully manual/scripted install), flags that account as the **default admin** (`is_default_admin` — see [User roles](#user-roles) and the auto-login note under Security below), seeds one `collector_registry` row for the install host's own detected IP (so there's at least one working collector out of the box instead of an empty registry), and stores a `base_url` setting derived from that IP and the chosen port (used to build SAML ACS/metadata URLs on the Auth settings tab).

### 10. Install and start the systemd service

`pktlog.service` is a template — substitute the placeholders before installing it, or just run `install.sh` which does this for you:

```bash
sed \
    -e "s#__INSTALL_DIR__#/opt/pktlog#g" \
    -e "s#__LOG_DIR__#/opt/pktlog/logs#g" \
    -e "s#__SERVICE_USER__#$(whoami)#g" \
    -e "s#__SERVICE_GROUP__#$(whoami)#g" \
    pktlog.service | sudo tee /etc/systemd/system/pktlog.service
sudo systemctl daemon-reload
sudo systemctl enable --now pktlog
sudo systemctl status pktlog
```

### 11. Open the firewall

```bash
sudo ufw allow 8768/tcp        # web UI / API
sudo ufw allow 5514/tcp        # syslog ingest (TCP)
sudo ufw allow 5514/udp        # syslog ingest (UDP)
```

### 12. Verify

```bash
curl -sk https://localhost:8768/api/health
```

Log in at `http://<server-ip>:8768` (or `https://` if SSL is configured) with the admin credentials from step 9.

---

## SSL / HTTPS

pktLog auto-detects SSL on startup. If `<INSTALL_DIR>/ssl/server.crt` and `server.key` exist, it starts in HTTPS mode; otherwise HTTP. `pktlog.service`'s `ExecStart` runs `start.sh` (not `uvicorn` directly), which implements this detection — so SSL just works under systemd once a cert/key are present, no unit changes needed.

**To enable HTTPS:** upload cert/key via **Settings → Security → SSL / TLS**, then restart the service.

**To disable HTTPS:** remove the cert via the same Settings panel (or delete the files under `<INSTALL_DIR>/ssl/`), then restart.

**Gotcha — a previously-installed unit can bypass `start.sh`.** If `/etc/systemd/system/pktlog.service` was installed before this `start.sh`-based `ExecStart` existed (or was hand-edited to invoke `uvicorn` directly), SSL/other `start.sh` behavior silently won't apply — check with `systemctl cat pktlog | grep ExecStart` and reinstall the unit from the repo's `pktlog.service` template if it doesn't match, then `sudo systemctl daemon-reload && sudo systemctl restart pktlog`.

---

## pktSuite Integration

pktLog supports SSO with pktHub/pktFlow via a shared `suite_token`:

- Generated on first call to `GET /api/suite/token`, or set/regenerated via **Settings → Security → Suite Integration** (this tab was labeled "pktHub Integration" before it was renamed to "Suite Integration")
- Stored in SQLite (`settings` table), not in `config.yaml`
- Requests carrying a matching `X-Suite-Token` header are trusted as coming from pktHub (see `app/dependencies.py`, `app/api/suite.py`)
- `GET /api/suite/whoami` is what a sibling app's "Test Connection" button actually calls (not the public `/api/health`), so a wrong/revoked token fails the test instead of silently reporting a healthy connection
- Copying the token from the Settings UI works over plain HTTP as well as HTTPS (falls back off the browser clipboard API, which requires a secure context, when needed)

### Hub-managed direct-access lock

pktHub can remotely lock pktLog's direct UI so users are redirected to sign in via pktHub instead: `POST /api/suite/direct-access {"locked": true}` (authenticated with `X-Suite-Token`). While locked, every non-API/non-asset request is redirected to whichever **Hub Redirect URL** is configured on the Suite Integration tab (also settable via `PATCH /api/suite/hub-redirect-url`).

This can't permanently strand admins out of the UI: a heartbeat (refreshed on every suite-token-authenticated request) auto-clears the lock if it goes stale for more than 5 minutes, and the lock is also cleared automatically at application startup if pktHub itself is unreachable. If direct access ever seems unexpectedly blocked, check `GET /api/suite/mode` (no auth required) for the current `direct_ui_locked`/`hub_redirect_url` state.

### Outbound integrations (sibling apps)

Separately from the inbound `suite_token` above, pktLog has a backend API (`/api/integrations`) for storing named, admin-managed connections *from* pktlog *to* other pkt* apps (pktIPAM, pktFlow, pktSNMP, pktPCAP, pktWiFi, pktHub) — same pattern as the equivalent feature in pktIPAM/pktFlow/pktWiFi. As of this writing there is no Settings tab exposing it and no feature actually consumes a configured connection yet; it exists as forward-looking scaffolding, not a working integration.

---

## Deployment

### Backend changes

1. Copy changed files to `/opt/pktlog/` on the server (same relative path as the repo), e.g. via `deploy_backend.py`
2. `sudo systemctl restart pktlog`
3. Verify: `curl -sk https://localhost:8768/api/health`

### Frontend changes

The frontend must be built on Linux — build on the server itself or a Linux CI runner, not on a Windows machine.

```bash
cp -r frontend /tmp/pktlog-fe
cd /tmp/pktlog-fe
npm install
npm run build
cp -r dist /opt/pktlog/frontend/dist
sudo systemctl restart pktlog
```

`deploy_fe.py` automates this over SSH from a local checkout.

### Operational scripts

These live in the repo root and are SSH/SFTP-based tools for managing a remote deployment (all take `--host`/`--user`/`--key` flags or `PKTLOG_SSH_*` env vars — no hardcoded infrastructure):

| Script | Purpose |
|--------|---------|
| `deploy_backend.py` | Push backend files and restart the service |
| `deploy_fe.py` | Sync frontend source, build remotely, deploy `dist/` |
| `deploy_initial.py` | Fresh install over SSH (mirrors `install.sh` minus system packages/ClickHouse) |
| `check_server.py` | Quick remote status check (service, disk, logs) |
| `seed_admin.py` | Create/reset the admin user's password remotely |
| `backup.py` | Local 2-rotation backup of the project directory |

---

## Directory Structure

```
pktlog/
├── app/
│   ├── api/
│   │   ├── auth.py          Login, SAML, token refresh
│   │   ├── syslog.py        Syslog search/stats/timeseries
│   │   ├── logs.py          App log viewer
│   │   ├── collectors.py    Collector registry CRUD
│   │   ├── settings.py      App settings CRUD
│   │   ├── users.py         User management
│   │   ├── system.py        Health, restart, SSL upload, backup
│   │   ├── suite.py         pktSuite suite_token issuance/registration, hub direct-access lock
│   │   ├── integrations.py  Outbound connections to sibling pkt* apps (backend-only, no UI yet)
│   │   ├── ip_info.py       Per-user IP intelligence/reputation lookup (ipinfo.io + ipapi.is +
│   │   │                     AbuseIPDB + MXToolbox ptr/asn/blacklist)
│   │   ├── mxtoolbox.py     Generic MXToolbox command passthrough (/api/mxtoolbox/lookup) —
│   │   │                     DNS/email records + active probes
│   │   ├── user_api_keys.py Per-user external API key storage (AbuseIPDB/ipinfo.io/ipapi.is/
│   │   │                     MXToolbox/IPQualityScore)
│   │   ├── ai.py             AI Assistant chat (Claude via Anthropic API)
│   │   ├── ws.py             WebSocket for live dashboard/alert updates
│   │   ├── widgets.py       Dashboard widgets
│   │   └── pktlog.py        Misc endpoints
│   ├── auth/                Local (JWT+bcrypt), Okta OIDC, SAML
│   ├── alerts/               Alert evaluation engine (6 notification channels)
│   ├── integrations/          SuiteClient — shared HTTP client for calling sibling pkt* apps
│   ├── ingest/
│   │   ├── listener.py       Async UDP+TCP syslog listener (port 5514)
│   │   ├── parser.py         RFC 3164 / RFC 5424 parsing, dest_ip (DST=) extraction
│   │   ├── normalizer.py     org/group/site enrichment, collector allowlist gate
│   │   └── writer.py         Batch writer → storage backend
│   ├── models/syslog.py       SyslogRecord dataclass
│   ├── storage/
│   │   ├── clickhouse.py     ClickHouse backend (production)
│   │   ├── duckdb.py         DuckDB backend (alternate)
│   │   └── factory.py        Backend selector
│   ├── config.py             Settings loader (YAML + env)
│   ├── database.py           SQLite init + migration runner
│   └── main.py                App factory, lifespan, router registration, direct-access-lock middleware
├── clickhouse/schema.sql       syslog_events table (MergeTree, 18 columns incl. dest_ip)
├── frontend/src/
│   ├── pages/                 Login, Dashboard, Alerts, Settings, Users, Logs, SyslogExplorer
│   ├── components/            Layout, HelpButton (contextual help), IpLink (IP intel lookup), AiAssistant
│   └── api/client.ts           Typed API client
├── migrations/                 SQLite migration scripts (auto-applied on startup)
├── install.sh                  Ubuntu install script (ClickHouse, venv, systemd service)
├── config.example.yaml         Config file template
├── pktlog.service               systemd unit template (placeholders filled in by install.sh)
├── start.sh                     SSL-aware startup wrapper (manual/dev use)
└── requirements.txt
```

---

## Security Notes

- Change `secret_key` in `config.yaml` (or `PKTLOG_SECRET_KEY` env var) before production use — `openssl rand -hex 32`
- Change the default admin password immediately after first login (`install.sh` generates a random one and prints it once; it is not recoverable afterward except via `seed_admin.py` or a direct DB reset)
- `cors_origins` should be restricted to your dashboard origin in production
- **Don't disable both Local auth and SAML SSO** (Settings → Security → Auth) unless the UI is only reachable from a genuinely trusted network. With both off, the login page is skipped entirely and anyone who reaches it is auto-logged in as the designated default admin (`users.is_default_admin`, or the oldest active admin if none is flagged) via `POST /api/auth/auto-login` — there is intentionally no login prompt in that state
- The pktSuite `suite_token` is a shared secret across pktHub/pktFlow/pktLog — never commit a real value to a tracked file, and rotating it requires updating it in all three services simultaneously
- If a `config.yaml` with a real `secret_key` or `suite_token` is ever accidentally committed, treat both as compromised: rotate `secret_key` immediately (it only affects this service), but coordinate before rotating `suite_token` since it will break SSO for pktHub/pktFlow until updated everywhere
