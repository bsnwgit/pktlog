# pktLog — Project Context for Claude

This file is the ground truth for working in this project. Read it before doing anything.

---

## ⛔ HARD RULES — THESE OVERRIDE EVERYTHING. NO EXCEPTIONS. EVER.

**RULE 1 — NEVER MARK TODO ITEMS COMPLETE WITHOUT EXPLICIT USER INSTRUCTION.**
The user must say "mark complete" or click the Mark Complete button. Claude never marks items done on its own, even if the work is finished. Not ever.

Violating this rule is unacceptable regardless of context, intent, or how obvious the action seems.

---

**Deploying:** deploy is a normal part of the edit → verify → deploy → test loop, same as every other pkt* app — no magic word required each time. Code changes proceed like any other pkt* app (normal engineering judgment, no per-change approval gate) — only todo completion (Rule 1) still needs an explicit user instruction.

---

**CRITICAL — Backup before marking complete:** Every time the user says to mark a todo item as done, run the local backup script FIRST, then mark the item. Never mark complete without backing up.

```bash
python "C:\Users\<LOCAL_USER>\My Drive\Documents\Claude\Projects\pktLog\backup.py"
```

Backups rotate to: `C:\Users\<LOCAL_USER>\My Drive\Documents\Claude\Projects\pktLog_backups\` (backup_1 = most recent, backup_2 = previous)

---

## What This Is

pktLog is a syslog ingest management and UI platform. It receives syslog data, stores it, and provides a management and reporting interface. It shares the same framework as pktFlow (FastAPI backend + React/TypeScript frontend).

**Live URL:** https://<PKT_SERVER_IP>:8768
**Server path:** /mnt/software/pktlog
**DB path:** /mnt/software/pktlog/pktlog.db

---

## Infrastructure

| Role | IP | User | SSH Key |
|------|----|------|---------|
| pkt server | <PKT_SERVER_IP> | <DEPLOY_USER> | `C:\Users\<LOCAL_USER>\.ssh\<PKT_SERVER_SSH_KEY>` |
| Collector-A Collector | <COLLECTOR_A_HOST> | <DEPLOY_USER> | `C:\Users\<LOCAL_USER>\.ssh\<PKT_SERVER_SSH_KEY>` |
| Collector-B Collector | <COLLECTOR_B_HOST> | <DEPLOY_USER> | `C:\Users\<LOCAL_USER>\.ssh\<COLLECTOR_B_SSH_KEY>` |

**pktLog on pkt server:**
- Service: `systemctl status pktlog`
- App dir: `/mnt/software/pktlog`
- Venv: `/mnt/software/pktlog/venv`
- Config: `/mnt/software/pktlog/config.yaml`
- HTTP port: **8768** (HTTPS)
- Syslog ingest port: **5514** (UDP + TCP) — current code default (`config.example.yaml`); was 8761 before commit `47f98fd`, confirm this host's `config.yaml`/`PKTLOG_SYSLOG_PORT` actually matches if in doubt
- Systemd: `/etc/systemd/system/pktlog.service`
- DB: `/mnt/software/pktlog/pktlog.db` (SQLite — users, settings, alert rules)
- Log data: ClickHouse `pktlog.syslog_events` — verify against this host's actual `config.yaml`/`systemctl cat pktlog`; the code default changed from 8761 to 5514 (commit `47f98fd`) and an older live install may predate that change
- Ingest journal: `/mnt/software/pktlog/ingest_journal/`

---

## SSH Rules — CRITICAL

**SentinelOne EDR blocks system ssh.exe.** Always use Python + Paramiko.

- Python path: `C:\Users\<LOCAL_USER>\AppData\Local\Programs\Python\Python313\python.exe`
- **ONE script, ONE run, NO retry loops** — hammering the connection locks the server and requires a reboot
- `timeout=15, banner_timeout=15` on every connect call
- Run scripts via Desktop Commander `start_process`, not the bash sandbox

```python
import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8')  # REQUIRED — Windows defaults to cp1252
key = paramiko.RSAKey.from_private_key_file(r"C:\Users\<LOCAL_USER>\.ssh\<PKT_SERVER_SSH_KEY>")
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("<PKT_SERVER_IP>", username="<DEPLOY_USER>", pkey=key, timeout=15, banner_timeout=15)
_, stdout, _ = client.exec_command("your command", timeout=20)
print(stdout.read().decode('utf-8', errors='replace'))
client.close()
```

**Windows encoding — CRITICAL:** Always include `sys.stdout.reconfigure(encoding='utf-8')` at the top of every Paramiko script. Without it, any Unicode output from the pkt server causes `UnicodeEncodeError` and the script dies mid-run.

**npm build output:** Vite's build table uses Unicode box-drawing characters. Never try to capture and print `npm run build` output directly. Always redirect to `/dev/null` and echo pass/fail:
```bash
npm run build > /dev/null 2>&1 && echo 'build ok' || echo 'BUILD FAILED'
```

---

## Frontend Build

**Never build the frontend in the project folder on Windows** — `node_modules` there is Windows-only and lacks the Linux `rollup` native binary.

Always build in Linux `/tmp` on the pkt server.

**CRITICAL: always sync the full `frontend/src` from local to pkt server first** — the server copy can drift from the local project. Skipping this means new pages/components get silently excluded from the bundle.

**USE THE PERMANENT DEPLOY SCRIPT:**
```
C:\Users\<LOCAL_USER>\My Drive\Documents\Claude\Projects\pktLog\deploy_fe.py
```
Run via Desktop Commander `start_process`:
```
C:\Users\<LOCAL_USER>\AppData\Local\Programs\Python\Python313\python.exe "C:\Users\<LOCAL_USER>\My Drive\Documents\Claude\Projects\pktLog\deploy_fe.py"
```

**What the script does (in order):**
```
1. SFTP frontend config files (package.json, vite.config.ts, etc.) to pkt server
2. SFTP entire frontend/src/ tree → /mnt/software/pktlog/frontend/src/ on pkt server
3. SSH: cp -r frontend to /tmp/pktlog-fe
4. SSH: npm install   ← REQUIRED every time
5. SSH: npm run build > /dev/null 2>&1 && echo 'build ok' || echo 'BUILD FAILED'
6. SSH: cp dist back to /mnt/software/pktlog/frontend/dist
7. SSH: sudo systemctl restart pktlog
8. Wait 4s, check systemctl is-active
```

**Critical rules:**
- **Always use script files** — never `python -c "..."` in cmd shell.
- **`npm install` is mandatory** — `cp -r` does not copy `node_modules`.
- **Redirect npm build output** — never capture directly.
- Node is installed via nvm on pkt server: `export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh"` before any npm command.

---

## Deployment Process

### Backend changes
1. Edit local file in `C:\Users\<LOCAL_USER>\My Drive\Documents\Claude\Projects\pktLog\`
2. Run `deploy_backend.py` to SFTP changed files to `/mnt/software/pktlog/` on pkt server
3. `sudo systemctl restart pktlog`
4. Wait 5 seconds, check `systemctl is-active pktlog`
5. Check `curl -sk https://localhost:8768/api/health`

### Frontend changes
Run `deploy_fe.py` (full sync + remote build). Do NOT just rebuild from pkt server's existing source without first syncing local `frontend/src/`.

---

## User Management

SQLite DB: `/mnt/software/pktlog/pktlog.db`, table `users`.

Roles: `admin` (full access), `analyst` (read + export), `viewer` (read-only).

**If login is broken:**
```python
import sqlite3, bcrypt
conn = sqlite3.connect('/mnt/software/pktlog/pktlog.db')
new_hash = bcrypt.hashpw(b'NewPassword1!', bcrypt.gensalt()).decode()
conn.execute("UPDATE users SET hashed_password=?, is_active=1 WHERE username='admin'", (new_hash,))
conn.commit()
```

---

## Current State

- Backend: FastAPI on 8768 (HTTP by default; HTTPS auto-enabled if `ssl/server.crt`+`server.key` are present), ClickHouse storage, syslog ingest live on **5514** (UDP+TCP)
- Ingest: UDP+TCP listener → RFC 3164/5424 parser → normalizer (org/group/site enrichment) → ClickHouse batch writer with file journal fallback
- **Collector registry is a hard ingest gate, not just enrichment.** `app/ingest/normalizer.py`'s `enrich()` returns `None` (record dropped, never reaches the writer) for any `collector_ip` not present and `enabled=1` in `collector_registry`. A device can be sending syslog on the wire, but nothing persists until it's added under Settings → Collectors and marked enabled. Unregistered senders instead fire a `new_host`/"Unknown collector" alert with a one-click "Register collector →" link.
- **RFC 3164 timestamps are interpreted in the app's configured device timezone, not assumed UTC.** RFC 3164 has no timezone marker; many real devices (UniFi APs/gateways observed in practice) log in local time. `_parse_ts_3164()` (app/ingest/parser.py) localizes the parsed naive datetime using the `timezone` setting (Settings → General — same value used for display) before converting to UTC for storage. Getting this wrong silently shifts every such event's `timestamp` column by the zone offset — `received_at` (always server-side `datetime.now(utc)`) is unaffected either way and is the reliable field to sanity-check against if timestamps ever look off again.
- **Parser also handles headerless lines.** Some devices send RFC-3164-shaped lines with no `<PRI>` at all (e.g. UniFi CEF security events: `TIMESTAMP HOSTNAME CEF:0|...`), and some send no syslog header whatsoever (e.g. UniFi AP kernel/wireless-driver debug lines: `{tag} [uptime] ...`). Both are recognized and stripped down to a real `message` instead of falling through to "unparseable" (which previously left `message == raw` and dropped `source_ip`/`timestamp` entirely).
- Storage: ClickHouse `pktlog.syslog_events` (18 columns, incl. `dest_ip` added `b1349c5`), SQLite for app config/auth/alerts/collector_registry/user_api_keys/integrations
- Frontend: React app with Login, Dashboard, Alerts, Settings, Users, Logs, Syslog Explorer pages — all timestamp displays use the configured `timezone` setting via `frontend/src/hooks/useTimezone.ts`, not the browser's local zone
- **`dest_ip`** (`b1349c5`): parser lifts a `DST=<ip>` KV pair (netfilter/firewall-style log lines) into its own field, separate from `source_ip`/`collector_ip`. Only populated for lines that embed `DST=` — most syslog lines have no destination-IP concept, so it's blank (not broken) for those. Filterable/displayed in Syslog Explorer.
- **IP intelligence / reputation lookup** (`432b9a7`, superseding the earlier `826871c` ipinfo+AbuseIPDB-only version): click any public IP anywhere in the UI (`frontend/src/components/IpLink.tsx`) to look it up via `GET /api/ip-info/{ip}`, which now calls **four** providers concurrently — ipinfo.io, ipapi.is, AbuseIPDB, and MXToolbox (ptr/asn/blacklist commands). Uses **per-user** API keys stored via `/api/user-api-keys` (Settings → User Keys tab), not a global app key. `SUPPORTED_PROVIDERS` in `app/api/user_api_keys.py` lists **five** storable/testable providers (AbuseIPDB, IPQualityScore, ipinfo.io, ipapi.is, MXToolbox); IPQualityScore can be saved/tested but still isn't consumed by the lookup endpoint. Each of the four active providers has a per-user enabled/field-visibility toggle gating what the modal shows. MXToolbox's other commands (DNS/email record checks, active network probes) are reachable via `POST /api/mxtoolbox/lookup` but not surfaced in the lookup modal.
- **Page-size selector** (`183ff3d`): Logs, Syslog Explorer, and Alerts (active/history tabs, sized independently) each have a 25/50/75/100 page-size dropdown next to their page-number bar, defaulting to 25 — resets to page 1 on change. Logs/Syslog Explorer thread it into the server-side `limit`/`offset` fetch; Alerts re-slices its already-fetched active/history set client-side.
- **AI Assistant** (Claude chat, pre-existing but easy to miss): floating button + slide-in drawer on every page (`frontend/src/components/AiAssistant.tsx`), `POST /api/ai/chat`. Requires an Anthropic API key in Settings → Security → AI Assistant; model choice (Haiku/Sonnet/Opus) also configured there.
- **App-wide contextual help** (`a0831da`): "?" `HelpButton` components on Dashboard, Alerts, Logs, SyslogExplorer, and 7 Settings sections (Auth, Suite Integration, AI Assistant, Notifications, etc.) — 11 `<HelpButton>` usages as of this writing. Note: a follow-up commit adding one to the Users page (`dc3f9d4`, "parity with other pkt* apps") exists only on an unmerged local branch (`fix/log-capture-level-display-2026-07-26`, 1 commit ahead of what actually got PR'd) — not in `main`, so Users is currently the one page still without it.
- **"Suite Integration" label** (renamed from "pktHub Integration", `065d47f`) lives at Settings → Security → Suite Integration, not a separate top-level "Integrations" tab — the top-level Settings tabs are General / Security (Users, Auth, Suite Integration, AI Assistant, SSL-TLS) / Data (Storage, Backups) / Notifications / User Keys / Collectors / Ingest. There is no top-level "Integrations" settings tab.
- **Hub-managed direct-UI lock** (pre-existing, previously undocumented here): pktHub can call `POST /api/suite/direct-access {"locked": true}` (authenticated via `X-Suite-Token`) to force all direct browser access into a redirect to a `hub_redirect_url` (set at Settings → Security → Suite Integration, or via `PATCH /api/suite/hub-redirect-url`). A heartbeat (`lock_heartbeat_at`, refreshed on every suite-token-authenticated request) auto-clears the lock if it goes >5 min stale or if pktHub is unreachable at app startup — so a lock can't permanently strand admins out of the direct UI if pktHub disappears. See the `_direct_access_lock` middleware and lifespan startup failsafe in `app/main.py`.
- **Auth "auto-login" fallback**: if *both* Local auth and SAML SSO are turned off (Settings → Security → Auth), the Login page skips the form entirely and auto-logs in as the designated default admin (`users.is_default_admin`, falling back to the oldest active admin) via `POST /api/auth/auto-login` — intentional for trusted-network-only setups, but means turning off all auth methods removes the login prompt for anyone who can reach the UI. Not something to do casually.
- **Outbound "Integrations" API** (`app/api/integrations.py`, migration `008_integrations.sql`): CRUD for named connections *from* pktlog *to* sibling pkt* apps (pktipam/pktflow/pktsnmp/pktpcap/pktwifi/pkthub). Backend/DB only — no frontend tab surfaces it yet, and per its own docstring "no consumer feature reads from this table yet." Distinct from `/api/suite/*`, which is the inbound side (pktHub calling into pktlog).
- Alert notification channels are actually **six**: `inapp`, `slack`, `email` (SMTP), `pagerduty`, `webhook`, `tracecat` (TraceCat SOAR) — all configured on the Notifications settings tab, all dispatched from `app/alerts/engine.py::_dispatch`.

**Known gotcha — deployed systemd unit can silently drift from the repo template.** `pktlog.service` in the repo runs `ExecStart=__INSTALL_DIR__/start.sh` (so SSL auto-detect and any future `start.sh` changes take effect), but an *already-installed* `/etc/systemd/system/pktlog.service` on a live host is a separate file that only gets updated by re-running the relevant part of `install.sh` or reinstalling the unit — editing `start.sh` alone has zero effect if the installed unit bypasses it with a direct `uvicorn ...` `ExecStart`. If a "backend fix" doesn't seem to take effect after a restart, compare `systemctl cat pktlog | grep ExecStart` against the repo's `pktlog.service` before assuming the code is wrong.

**Backlog:** none open as of this writing — the three items previously tracked here (collector-registry gating, cross-worker log capture level, Syslog Explorer full-field detail view) are all implemented; see git history (`d9f1e3f`, `deb95aa`) for details.
