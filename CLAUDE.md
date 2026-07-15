# pktLog — Project Context for Claude

This file is the ground truth for working in this project. Read it before doing anything.

---

## ⛔ HARD RULES — THESE OVERRIDE EVERYTHING. NO EXCEPTIONS. EVER.

**RULE 1 — NEVER MARK TODO ITEMS COMPLETE WITHOUT EXPLICIT USER INSTRUCTION.**
The user must say "mark complete" or click the Mark Complete button. Claude never marks items done on its own, even if the work is finished. Not ever.

**RULE 2 — NEVER WRITE CODE OR MAKE FILE CHANGES WITHOUT EXPLICIT USER APPROVAL.**
"Let's work on X" = discussion only. Do not write a single line of code until the user says to proceed. Always discuss and plan first. Wait for explicit go-ahead.

**RULE 3 — NEVER DEPLOY WITHOUT BEING TOLD TO.**
Do not run the deploy script unless the user explicitly says "deploy."

Violating these rules is unacceptable regardless of context, intent, or how obvious the action seems.

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
- Syslog ingest port: **8761** (UDP + TCP)
- Systemd: `/etc/systemd/system/pktlog.service`
- DB: `/mnt/software/pktlog/pktlog.db` (SQLite — users, settings, alert rules)
- Log data: ClickHouse `pktlog.syslog_events`
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

- Backend: FastAPI, HTTPS on 8768, ClickHouse storage, syslog ingest live on 8761
- Ingest: UDP+TCP listener → RFC 3164/5424 parser → normalizer (org/group/site enrichment) → ClickHouse batch writer with file journal fallback
- Collectors: Collector-A (<COLLECTOR_A_HOST>) and Collector-B (<COLLECTOR_B_HOST>) syslog-ng forwarding to pkt server:8761 with disk-buffer
- Storage: ClickHouse `pktlog.syslog_events` (17 columns), SQLite for app config/auth/alerts/collector_registry
- Frontend: React app with Login, Dashboard (stub), Alerts, Settings, Users, Logs pages

**What still needs to be built (Phases 3–5):**
- Syslog API endpoints (`/api/syslog/search`, `/api/syslog/stats`, `/api/syslog/timeseries`)
- Dashboard with real analytics (replace stub)
- Syslog Explorer page
- Settings → Ingest tab (port config, retention, journal cap)
- Syslog-specific alert rule types

**New backlog items (added 2026-07-15):**
- **Collector registry should gate ingest, not just enrich it.** Right now `app/ingest/normalizer.py`'s `enrich()` looks up `collector_registry` (filtered to `enabled = 1`) purely for org/log_group/site labeling — a syslog message from an unregistered or disabled collector still gets fully processed and written to ClickHouse, just without hierarchy metadata (falls back to using the raw IP as the name). The user wants this to work like pktflow's collector model: the Collectors section becomes the actual gateway/allowlist for what's allowed to persist — a remote device can be configured and start sending data over the network, but if its IP isn't listed in Collectors *and* marked enabled, that data should be dropped, not stored. Needs a hard gate somewhere in the ingest path (listener → normalizer → writer) that rejects/discards records from unregistered-or-disabled collector_ips before they reach the ClickHouse batch writer's queue. Check how pktflow actually implements this (referenced as the model to match) before designing pktlog's version.
- **"Capture level" on the Logs page doesn't actually enforce anything.** `POST /api/logs/level` (app/api/logs.py) calls `SQLiteLogHandler.set_capture_level()` on the handler attached to the *current process's* `pktlog` logger — but pktlog runs with `--workers 2` (see `pktlog.service`/`start.sh`), meaning two independent uvicorn worker processes, each with its own `SQLiteLogHandler` instance created fresh in `app/main.py`'s lifespan. Changing the level via the UI only updates whichever one worker happened to handle that HTTP request; the sibling worker's handler keeps its previous level indefinitely, so roughly half of all log activity (including, notably, the syslog UDP/TCP listener, which only runs in whichever worker won the port bind) is unaffected by the change. Needs a cross-worker mechanism — e.g. store the desired level in SQLite settings and have each worker's handler poll/check it periodically, similar to how other runtime settings already work — rather than pure in-memory per-process state.
