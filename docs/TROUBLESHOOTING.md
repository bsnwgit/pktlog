# pktLog — Troubleshooting

Symptom, cause, and the command that proves which cause it is.

`<INSTALL_DIR>` is the install directory (`/opt/pktlog` by default),
`<SENDER_IP>` a device sending syslog, `<APP_SERVER_IP>` this server.

---

## Contents

- [The first five minutes](#the-first-five-minutes)
- [The service will not start](#the-service-will-not-start)
- [The service runs but nothing answers](#the-service-runs-but-nothing-answers)
- [The UI is blank, stale, or 404](#the-ui-is-blank-stale-or-404)
- [Login and accounts](#login-and-accounts)
- [No syslog is arriving](#no-syslog-is-arriving)
- [Messages arrive but are not stored](#messages-arrive-but-are-not-stored)
- [Storage, the journal, and ClickHouse](#storage-the-journal-and-clickhouse)
- [Search and timestamps](#search-and-timestamps)
- [Alerts and notifications](#alerts-and-notifications)
- [A config change did not take effect](#a-config-change-did-not-take-effect)
- [TLS / HTTPS](#tls--https)
- [Backup and restore](#backup-and-restore)
- [Upgrades and migrations](#upgrades-and-migrations)
- [Performance and disk](#performance-and-disk)
- [Uninstalling and reinstalling](#uninstalling-and-reinstalling)
- [What to capture before reporting a problem](#what-to-capture-before-reporting-a-problem)

---

## The first five minutes

```bash
sudo systemctl status pktlog --no-pager
```

```bash
sudo journalctl -u pktlog -n 100 --no-pager
```

```bash
sudo tail -n 100 <INSTALL_DIR>/logs/pktlog.log
```

```bash
sudo ss -ltnp | grep 8768; sudo ss -lunp | grep 5514
```

```bash
curl -s http://127.0.0.1:8768/api/health
```

```bash
curl -s http://127.0.0.1:8768/api/approval/count
```

| What you see | Go to |
|---|---|
| `inactive (dead)` or `failed` | [The service will not start](#the-service-will-not-start) |
| Running, nothing on 8768 | [The service runs but nothing answers](#the-service-runs-but-nothing-answers) |
| Health 200, UI blank or 404 | [The UI is blank, stale, or 404](#the-ui-is-blank-stale-or-404) |
| Health 200, no events | [No syslog is arriving](#no-syslog-is-arriving) |
| **`/api/approval/count` is non-zero** | **Senders are queued awaiting approval** — [Messages arrive but are not stored](#messages-arrive-but-are-not-stored) |

That last row is the single most common cause of "pktLog is not working", and it
is not a fault. Check it before anything else.

Read the log file **and** the journal. The unit appends stdout and stderr to
`<INSTALL_DIR>/logs/pktlog.log`, so the journal can look empty while the real
traceback is in the file.

---

## The service will not start

```bash
sudo journalctl -u pktlog -n 200 --no-pager
sudo tail -n 200 <INSTALL_DIR>/logs/pktlog.log
```

pktLog launches through `start.sh`, not `python -m app.server` directly — the
wrapper auto-detects `ssl/server.crt` + `ssl/server.key` and adds the uvicorn
SSL flags. To reproduce a startup failure by hand:

```bash
sudo -u <service-user> \
  PKTLOG_CONFIG=<INSTALL_DIR>/config.yaml \
  PKTLOG_INSTALL_DIR=<INSTALL_DIR> \
  <INSTALL_DIR>/start.sh
```

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError` | venv missing packages, or built against a different Python | `<INSTALL_DIR>/venv/bin/pip install -r requirements.txt`; rebuild the venv if Python was upgraded under it |
| `yaml.scanner.ScannerError` | `config.yaml` is not valid YAML | `python3 -c "import yaml; yaml.safe_load(open('<INSTALL_DIR>/config.yaml'))"` |
| Complaint about `secret_key` / `credential_key` | Left at `CHANGE_ME_…` | `openssl rand -hex 32`; and `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `Address already in use` on 8768 | Something else holds the port | `sudo ss -ltnp \| grep 8768` |
| `Address already in use` on the syslog port | Another syslog daemon — `rsyslog` or `syslog-ng` — already holds it | Stop it, or move pktLog's port. This is why the default is **5514**, not 514 |
| `Permission denied` binding port 514 | Privileged port | The pktLog unit does **not** set `CAP_NET_BIND_SERVICE`. Keep 5514, or add the capability deliberately |
| `Permission denied` on the DB, logs, or journal dir | Install dir not owned by the service user | `sudo chown -R <service-user>:<service-group> <INSTALL_DIR>` |
| Fernet `InvalidToken` | `credential_key` changed after credentials were stored | See [A config change did not take effect](#a-config-change-did-not-take-effect) |
| `start.sh: Permission denied` | Not executable after a manual copy | `chmod +x <INSTALL_DIR>/start.sh` |

### It restarts forever

The unit uses `Restart=always`, not `on-failure`. This is deliberate: the app's
in-UI restart falls back to sending itself SIGTERM when it lacks passwordless
sudo for `systemctl restart`, and a clean SIGTERM is not a "failure" — under
`on-failure` the service would stop and stay down.

So a genuinely broken app restarts forever. Read the log, and stop it while you
investigate:

```bash
sudo systemctl stop pktlog
```

### It runs by hand but not under systemd

The unit sets only `PKTLOG_CONFIG` and `PKTLOG_INSTALL_DIR`. Resolution order is
`PKTLOG_*` env vars, then `config.yaml` via `$PKTLOG_CONFIG` →
`$PKTLOG_INSTALL_DIR/config.yaml` → `./config.yaml` → `~/.pktlog/config.yaml`,
then defaults.

```bash
systemctl cat pktlog
systemctl show pktlog -p Environment
```

---

## The service runs but nothing answers

```bash
sudo ss -ltnp | grep 8768
```

`host:` and `port:` are read from `config.yaml` at every process start, so a
port change needs only a restart, never a unit edit.

- Bound to `127.0.0.1` → reachable only from the host. Set `host: "0.0.0.0"`.
- Then work outward:

```bash
curl -sv http://127.0.0.1:8768/api/health
curl -sv http://<APP_SERVER_IP>:8768/api/health   # from another machine
```

| Where it breaks | Cause |
|---|---|
| Fails on loopback | Not a network problem — see [the service will not start](#the-service-will-not-start) |
| Works on loopback only | Bound to loopback, or a host firewall |
| Fails from elsewhere | `ufw`/`nftables`/`iptables`, a security group, or routing |
| TLS error | HTTPS against an HTTP listener or the reverse — see [TLS / HTTPS](#tls--https) |

---

## The UI is blank, stale, or 404

| Symptom | Cause | Fix |
|---|---|---|
| `{"detail":"Not Found"}` at the root | The frontend was never built | `cd frontend && npm install && npm run build`, then restart. `install.sh` builds it only if `npm` is already on `PATH` — Node.js 20.x LTS is a prerequisite it does not install |
| Blank page, console 404s on `/assets/*` | `dist` is stale or half-built | Rebuild, then hard-refresh |
| Old UI after an upgrade | Cached `index.html` pinning old hashed bundles | Hard refresh (Ctrl/Cmd-Shift-R) |
| Every API call 401 | Session expired — see [Login and accounts](#login-and-accounts) |
| CORS errors | Frontend on a different origin from the API | Empty `cors_origins` is correct for a normal install — the UI is served from this same origin. Only list an exact origin if something genuinely external calls the API; never `"*"` with credentialed requests |

---

## Login and accounts

bcrypt password hashing plus JWT. Roles are `admin` / `analyst` / `viewer`.
Sessions time out after `session_timeout_minutes` (default 480).

Okta SAML is supported and **disabled by default** (`okta_saml_enabled`).

| Symptom | Cause | Fix |
|---|---|---|
| 401 immediately after logging in | Clock skew invalidates the token's `exp` | `timedatectl`; fix NTP |
| Logged out after 8 hours | `session_timeout_minutes` | Raise it in Settings if that is wrong for you |
| SAML login loops or errors | IdP metadata mismatch | Check `okta_saml_idp_entity_id`, `okta_saml_idp_sso_url` and `okta_saml_idp_cert` (the cert goes in **without** header/footer lines). `okta_saml_sp_entity_id` defaults to `base_url/api/auth/saml/metadata` |
| SAML works, local login does not | `auth_local_enabled` is off | Re-enable it, or use SAML |
| Locked out of every account | No admin session left | Reset the hash directly — below |

### Resetting the admin password

```bash
sudo systemctl stop pktlog
sqlite3 <INSTALL_DIR>/pktlog.db ".schema users"
```

Generate the hash with the app's own venv so the bcrypt version matches:

```bash
<INSTALL_DIR>/venv/bin/python -c "import bcrypt; print(bcrypt.hashpw(b'NewPassword1!', bcrypt.gensalt()).decode())"
```

`UPDATE` the row, restart, then change it again through the UI.

---

## No syslog is arriving

### Step 1 — which port is it actually listening on?

**The SQLite setting wins over `config.yaml`.** `syslog_port` in `config.yaml`
(default 5514) is only the fallback — a value set in Settings → Ingest
overrides it. Editing `config.yaml` when a setting exists changes nothing.

```bash
sudo ss -lunp | grep pktlog     # UDP
sudo ss -ltnp | grep pktlog     # TCP
```

The listener binds **both** UDP and TCP on that one port.

### Step 2 — is anything on the wire?

```bash
sudo tcpdump -ni any port 5514 -c 20
```

No packets means the sender or the network, not pktLog:

- **The device is sending to 514.** The default here is **5514**. A device left
  on the standard port sends into nothing. Either repoint the device or change
  the setting.
- Firewall: `sudo ufw status verbose`. Remember UDP and TCP are separate rules.
- Some devices only send over UDP; some only over TCP. Confirm which.

### Step 3 — it is on the wire but rejected

| Symptom | Cause |
|---|---|
| TCP connects then drops | Framing. pktLog expects non-transparent, newline-delimited framing (RFC 6587 §3.4.2). A device using octet-counted framing will not parse |
| Very long messages truncated or dropped | 64 KB cap per message, and per UDP datagram |
| Messages arrive but parse oddly | The parser handles RFC 3164 and RFC 5424. A proprietary format will not map cleanly — check what the device actually emits |

### Step 4 — it is arriving and being dropped on purpose

That is the next section, and it is the usual answer.

---

## Messages arrive but are not stored

**A sender that is not in `collector_registry` has every message dropped.** This
is the gate, and it is deliberate:

> The collector registry is the gateway for what's allowed to persist … until it
> is approved (Approval page) and marked enabled.
> — [`app/ingest/normalizer.py`](../app/ingest/normalizer.py)

Dropped senders are accumulated and surfaced on the **Approval** page, so the
device is visible to an admin even when no `new_host` alert rule is enabled.

```bash
curl -s http://127.0.0.1:8768/api/approval/count
curl -s http://127.0.0.1:8768/api/approval/pending
```

| Symptom | Cause | Fix |
|---|---|---|
| Nothing stored, Approval page shows the sender | Awaiting approval | Approve it. `POST /api/approval/approve` registers the sender and clears the queue entry |
| Approved, still nothing stored | The registry row is present but **not enabled** | The cache only loads rows `WHERE enabled = 1`. Approving is not the same as enabling |
| Sender is not on the Approval page either | It was **ignored** | `POST /api/approval/{ip}/unignore` brings it back. Ignored senders keep being dropped, silently, by design |
| Sender appears on the Approval page minutes late | Pending counters accumulate in memory on the hot path and are flushed to SQLite from the alert engine's tick — deliberately, so a device blasting syslog does not become one write per message | Wait for a tick |
| Just approved a sender, still dropping | The normaliser cache has not refreshed | It refreshes periodically and on restart. Restart if you need it immediately |
| Everything dropped after a restore | The registry lives in SQLite — restoring an older DB restores an older registry | Re-approve |

---

## Storage, the journal, and ClickHouse

`storage_backend` is a **setting** (`clickhouse` or `duckdb`), not a
`config.yaml` entry. `config.yaml` carries only the connection details.

```bash
sudo systemctl status clickhouse-server --no-pager
clickhouse-client --query "SELECT 1"
clickhouse-client --query "SELECT count() FROM pktlog.syslog_events"
```

| Symptom | Cause | Fix |
|---|---|---|
| `Connection refused` | ClickHouse down, or wrong `clickhouse_host`/`clickhouse_port` | Native protocol is 9000 |
| `Authentication failed` | Wrong `clickhouse_user`/`clickhouse_password` | A default install uses `default` with an empty password |
| `Table doesn't exist` | Schema not applied, or applied elsewhere | Re-apply `clickhouse/schema.sql` — safe to re-run |
| ClickHouse will not start | Often memory on a small host | `journalctl -u clickhouse-server` |
| Switching backend "lost" data | The two backends are separate stores | Switch back to see it |
| Disk filling | Retention | `retention_days_raw` (90) and `retention_days_hourly` (365) |

### The ingest journal

When ClickHouse is unreachable, the writer retries and then **overflows to a
file journal** at `journal_dir` (default `<INSTALL_DIR>/ingest_journal`). On
startup it replays whatever is left there.

| Symptom | Meaning |
|---|---|
| Data appears in a late burst | The journal drained after ClickHouse came back. Working as designed |
| `ingest_journal/` growing | ClickHouse has been down long enough to matter. Fix it before the cap is hit |
| Journal stops growing, data lost | The `journal_max_gb` cap was reached |
| Log line: `ClickHouse unavailable after N retries — writing … to journal` | The overflow path engaging. The error is ClickHouse, not the journal |

---

## Search and timestamps

| Symptom | Cause | Fix |
|---|---|---|
| Timestamps shifted by a constant offset | The device logs local time while the app assumes otherwise | Settings → General → Timezone, set to match how the device actually sends |
| Events out of order | Multiple senders with unsynchronised clocks | NTP on the senders |
| A search returns nothing | Time range first, before anything else | Widen it |
| Searches slow on wide ranges | Scan volume | Narrow the range; check retention |
| An expected field is empty | The message did not carry it, or did not parse | Compare against the raw sample on the Approval page |

---

## Alerts and notifications

Rule types: `data_gap`, `new_host`, `threshold`, `rate_spike`, `top_talker`,
`ingest_rate_low`, `clickhouse_size`.

Channels are in-app, Email (SMTP), Slack, PagerDuty, generic Webhook and
Tracecat. Senders are written never to raise — a broken channel must not stop an
alert reaching the others — so **a failing channel looks like nothing
happening**. Use each channel's Send Test to get the real error.

| Symptom | Cause |
|---|---|
| No alerts at all | Nothing to evaluate, because nothing is being stored. Fix ingest first |
| `new_host` firing constantly | An unregistered sender is being dropped on every batch — approve or ignore it |
| `data_gap` / `ingest_rate_low` firing | The pipeline has stopped. Go to [No syslog is arriving](#no-syslog-is-arriving) |
| `clickhouse_size` firing | Disk or retention |
| Email never arrives | SMTP host, port (default 587), TLS, credentials, or a relay refusing the sender |
| Slack 4xx | Webhook revoked or malformed |
| Webhook target sees nothing | Method, headers, or the Jinja2 payload template failing to render |

---

## A config change did not take effect

Four causes, easily confused.

**Wrong file.** Env vars beat the file silently:

```bash
systemctl show pktlog -p Environment
```

**Not restarted.** Nothing in `config.yaml` is re-read live, and restoring a
backed-up `config.yaml` never restarts the service.

**The setting is not in `config.yaml`.** That file holds startup and
infrastructure only: host, port, workers, secrets, paths, ClickHouse/DuckDB
connection details, and the *fallback* syslog port. Storage backend, retention,
the effective syslog port, the collector registry, alert rules, notification
channels and SAML all live in **SQLite** and are managed in the UI.

**A setting shadows the file.** `syslog_port` is the one that catches people:
the SQLite value wins, so the `config.yaml` line looks authoritative and is not.

### `credential_key` changed or lost

Stored secrets are Fernet-encrypted with it. Change it and everything encrypted
becomes undecryptable — `InvalidToken`, not a helpful message. Restore the old
key or re-enter every credential. This is why `uninstall.sh` keeps `config.yaml`
by default.

---

## TLS / HTTPS

`start.sh` detects `<INSTALL_DIR>/ssl/server.crt` and `server.key` and adds the
uvicorn SSL flags. Plain HTTP by default; upload a cert via Settings → SSL/TLS
and restart. No other config is needed.

| Symptom | Cause | Fix |
|---|---|---|
| Still HTTP after uploading a cert | Not restarted | Restart |
| Still HTTP after a restart | The files are not where `start.sh` looks, or are unreadable by the service user | Confirm `ssl/server.crt` and `ssl/server.key` exactly — the names matter |
| Will not start after upload | Key does not match the cert | Compare `openssl x509 -noout -modulus -in server.crt \| openssl md5` with `openssl rsa -noout -modulus -in server.key \| openssl md5` |
| Certificate warning | Self-signed, or the SAN does not cover the hostname used | Expected for self-signed |

```bash
curl -k https://127.0.0.1:8768/api/health
```

---

## Backup and restore

Scheduled backups write timestamped `backup_*` directories. The settings live in
SQLite, not `config.yaml`.

| Symptom | Cause |
|---|---|
| No backups appearing | Schedule off, or settings unset |
| Backups fail | Backup root not writable, or disk full |
| Restore "worked" but nothing changed | A restored `config.yaml` never restarts the service |
| Restored elsewhere and secrets fail | `credential_key` differs — restore `config.yaml` too |
| Senders dropping after a restore | An older collector registry came back with the DB |

**Never copy a live SQLite database with `cp`.** Take `pktlog.db`, `-wal` and
`-shm` with the service stopped, or use `sqlite3 … ".backup"`.

---

## Upgrades and migrations

Numbered `.sql` files, run on startup, tracked in `_migrations`, idempotent.

```bash
git pull
cd frontend && npm install && npm run build && cd ..
sudo systemctl restart pktlog
```

Re-running `install.sh` is better when a release drops or renames a file: it
detects the existing install, reports the version, and offers to uninstall first
so no stale module is left importable. Data is kept, and the port you enter is
applied to the existing `config.yaml` without touching another line.
`PKTLOG_REMOVE_EXISTING=1` (or `0`) answers that prompt from a script.

| Symptom | Cause |
|---|---|
| `no such column` / `no such table` | Migrations did not run — the app failed earlier in startup |
| A migration fails | Compare `SELECT * FROM _migrations` against `ls migrations/`. Restore from backup before experimenting |
| App upgraded, UI did not | Frontend not rebuilt, or browser cache |
| `VERSION` looks wrong | Bumped by `scripts/bump_version.py`, never by hand |

---

## Performance and disk

```bash
df -h
du -sh <INSTALL_DIR>/*
```

| Symptom | Where to look |
|---|---|
| Disk filling | `ingest_journal/`, `logs/`, `backups/`, `pktlog_data.duckdb`, and ClickHouse's own store — which `du` here will not show |
| Ingest dropping under load | The writer's batching and ClickHouse's ability to keep up |
| Searches slow | Time range, then retention, then ClickHouse |
| Memory climbing | Pending-sender counters accumulate in memory between flushes; a very large number of unregistered senders is not free |
| Slow at one time of day | A scheduled job — backup or retention |

---

## Uninstalling and reinstalling

```bash
bash <INSTALL_DIR>/uninstall.sh
```

Stops and removes the service, deletes the code and the venv. **Data is kept by
default** — `config.yaml`, `pktlog.db` and its `-wal`/`-shm`, `logs/`,
`backups/`, `ssl/`, `pktlog_data.duckdb` and `ingest_journal/`. It asks
separately about those, defaulting to no.

| Flag | Effect |
|---|---|
| *(none)* | Remove service, code and venv; keep data |
| `--purge` | Also delete config, database, logs, backups and TLS material. Not recoverable |
| `--dry-run` | Print what would be removed |
| `--yes` | Skip prompts — required non-interactively |
| `--dir PATH` | Install directory, if the unit is already gone |

- Re-running `install.sh` against the same directory picks the kept data back up.
- An in-place git checkout is detected and its source tree is never deleted.
- `--purge` does **not** drop the ClickHouse database — it may be shared with
  the rest of the suite. The uninstaller prints the `DROP DATABASE` for you.

**Never mirror over an install directory with `rsync --delete`.** Live state
sits beside the code: `config.yaml`, the database and its `-wal`/`-shm`,
`venv/`, `logs/`, `backups/`, `ssl/`, `ingest_journal/`, `pktlog_data.duckdb`,
`frontend/node_modules`.

---

## What to capture before reporting a problem

1. `VERSION`, and how it was installed.
2. `systemctl status pktlog` plus the last 200 lines of **both** the journal and
   `logs/pktlog.log`.
3. `config.yaml` **with `secret_key`, `credential_key` and passwords removed**.
4. `curl -s http://127.0.0.1:8768/api/health` and
   `curl -s http://127.0.0.1:8768/api/approval/count`, run **on the host**.
5. The effective syslog port — the setting, not the `config.yaml` line — and
   `ss -lunp | grep pktlog`.
6. For a data problem: `tcpdump` output proving packets arrive, and whether the
   sender is registered *and enabled* in the collector registry.
7. What changed immediately before it broke.

Never paste real secrets, tokens, keys, or an unredacted `config.yaml`.
