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
python "C:\Users\robert.barnett\My Drive\Documents\Claude\Projects\pktLog\backup.py"
```

---

## What This Is

pktLog is a syslog ingest management and UI platform. It receives syslog data, stores it, and provides a management and reporting interface. It shares the same framework as pktFlow (FastAPI backend + React/TypeScript frontend).

**Live URL:** http://172.23.80.5:8768
**Server path:** /mnt/software/pktlog
**DB path:** /mnt/software/pktlog/pktlog.db

---

## Infrastructure

| Role | IP | User | SSH Key |
|------|----|------|---------|
| O2 Server | 172.23.80.5 | ec2-user | `C:\Users\robert.barnett\.ssh\VyneCorpNetInfra.pem` |

**pktLog on O2:**
- Service: `systemctl status pktlog`
- App dir: `/mnt/software/pktlog`
- Venv: `/mnt/software/pktlog/venv`
- Config: `/mnt/software/pktlog/config.yaml`
- Port: **8768**
- Systemd: `/etc/systemd/system/pktlog.service`
- DB: `/mnt/software/pktlog/pktlog.db` (SQLite — users, settings, alert rules)

---

## SSH Rules — CRITICAL

**SentinelOne EDR blocks system ssh.exe.** Always use Python + Paramiko.

- Python path: `C:\Users\robert.barnett\AppData\Local\Programs\Python\Python313\python.exe`
- **ONE script, ONE run, NO retry loops** — hammering the connection locks the server and requires a reboot
- `timeout=15, banner_timeout=15` on every connect call
- Run scripts via Desktop Commander `start_process`, not the bash sandbox

```python
import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8')  # REQUIRED — Windows defaults to cp1252
key = paramiko.RSAKey.from_private_key_file(r"C:\Users\robert.barnett\.ssh\VyneCorpNetInfra.pem")
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("172.23.80.5", username="ec2-user", pkey=key, timeout=15, banner_timeout=15)
_, stdout, _ = client.exec_command("your command", timeout=20)
print(stdout.read().decode('utf-8', errors='replace'))
client.close()
```

**Windows encoding — CRITICAL:** Always include `sys.stdout.reconfigure(encoding='utf-8')` at the top of every Paramiko script. Without it, any Unicode output from O2 causes `UnicodeEncodeError` and the script dies mid-run.

**npm build output:** Vite's build table uses Unicode box-drawing characters. Never try to capture and print `npm run build` output directly. Always redirect to `/dev/null` and echo pass/fail:
```bash
npm run build > /dev/null 2>&1 && echo 'build ok' || echo 'BUILD FAILED'
```

---

## Frontend Build

**Never build the frontend in the project folder on Windows** — `node_modules` there is Windows-only and lacks the Linux `rollup` native binary.

Always build in Linux `/tmp` on O2.

**CRITICAL: always sync the full `frontend/src` from local to O2 first** — the O2 copy can drift from the local project. Skipping this means new pages/components get silently excluded from the bundle.

**USE THE PERMANENT DEPLOY SCRIPT:**
```
C:\Users\robert.barnett\My Drive\Documents\Claude\Projects\pktLog\deploy_fe.py
```
Run via Desktop Commander `start_process`:
```
C:\Users\robert.barnett\AppData\Local\Programs\Python\Python313\python.exe "C:\Users\robert.barnett\My Drive\Documents\Claude\Projects\pktLog\deploy_fe.py"
```

**What the script does (in order):**
```
1. SFTP frontend config files (package.json, vite.config.ts, etc.) to O2
2. SFTP entire frontend/src/ tree → /mnt/software/pktlog/frontend/src/ on O2
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
- Node is installed via nvm on O2: `export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh"` before any npm command.

---

## Deployment Process

### Backend changes
1. Edit local file in `C:\Users\robert.barnett\My Drive\Documents\Claude\Projects\pktLog\`
2. Run `deploy_backend.py` to SFTP changed files to `/mnt/software/pktlog/` on O2
3. `sudo systemctl restart pktlog`
4. Wait 4 seconds, check `systemctl is-active pktlog`
5. Check `curl -s http://localhost:8768/api/health`

### Frontend changes
Run `deploy_fe.py` (full sync + remote build). Do NOT just rebuild from O2's existing source without first syncing local `frontend/src/`.

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

## Bootstrap State

pktLog was scaffolded from pktFlow. Current state:
- Backend: FastAPI app with auth, alerts, settings, users, system APIs
- Frontend: React app with Login, Dashboard (stub), Alerts, Settings pages
- Storage: SQLite sidecar DB for config/users/alerts; DuckDB or ClickHouse for log data
- Service: `pktlog.service` systemd unit on port 8768

**What needs to be built:**
- Syslog ingest pipeline (UDP/TCP syslog receiver → normalizer → storage)
- Dashboard with real syslog analytics (replace stub)
- Log search/explorer page
- Syslog-specific alert rule types

See TODO.md for the full task list.
