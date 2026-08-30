# pktLog — Administrator Guide

Covers installing, configuring, and operating pktLog. For day-to-day usage (Syslog Explorer, alerts, dashboard), see [USER_GUIDE.md](USER_GUIDE.md). See the [README](../README.md) for the full technical/API reference.

## Installation

Requires a fresh Ubuntu Server 22.04/24.04 LTS host with `sudo`, and Node.js 20.x LTS installed beforehand for the frontend build.

```bash
git clone https://github.com/bsnwgit/pktlog.git
cd pktlog
bash install.sh
```

Prompts for install directory (default `/opt/pktlog`) and app port (default `8768`), then handles system packages, ClickHouse, Python deps/schema, `config.yaml` + secret key, the admin user, one seeded collector entry (this host's own IP), the frontend build, and the systemd service. Prints the admin credentials at the end — save them.

Open the firewall for the app port and the syslog ingest port (`8768/tcp`, `5514/tcp`+`udp` by default — confirm against your `config.yaml`, since the code default changed from `8761` to `5514` at one point and an older install may still use the previous value).

## First-time setup checklist

1. **Change the admin password.**
2. **Approve your syslog-sending devices** (Approval page, admin-only, directly above Settings). This is a hard ingest gate, not just labeling — a device sending syslog on the wire won't have anything persisted until it's approved and marked enabled. Senders pktLog has seen and dropped are queued on that page with a dropped-message count and a sample line; approving one creates its collector registry entry. Settings → Collectors remains where you edit collectors you've already approved.
3. **Set the Timezone** (Settings → General) — this affects both UI display and how RFC 3164 timestamps (which carry no timezone marker of their own) are interpreted for storage. Get this right before you rely on timestamps for investigation; many real devices (UniFi APs/gateways observed in practice) log in local time, not UTC.
4. **Configure alert notification channels** so the team hears about new/unknown collectors and anything else you set up rules for.
5. **Set up backups** (Data → Backups) and confirm a manual run succeeds.
6. **Create accounts** for your team with appropriate roles.

## Users & roles

`admin`, `analyst`, `viewer`. Manage at Settings → Security → Users (admin-only). If login breaks and you're locked out entirely, reset the admin password directly against the SQLite DB:

```python
import sqlite3, bcrypt
conn = sqlite3.connect('<install_dir>/pktlog.db')
new_hash = bcrypt.hashpw(b'NewPassword1!', bcrypt.gensalt()).decode()
conn.execute("UPDATE users SET hashed_password=?, is_active=1 WHERE username='admin'", (new_hash,))
conn.commit()
```

### Okta SAML SSO

Settings → Security → Auth: paste Okta's IdP metadata XML (auto-fills SSO URL/Entity ID/certificate) or enter manually.

### Auto-login fallback

If you disable *both* Local auth and SAML SSO, the login page is skipped and the app auto-signs everyone in as the default admin (`POST /api/auth/auto-login`). Only appropriate for a trusted, access-controlled network — this removes the login prompt for anyone who can reach the UI at all.

## Settings reference

A section bar at the top of the page splits Settings into **Common** — the tabs every pkt* app shares — and **pktLog**, this app's own. The tab bar below shows only the selected section, so switch sections if a tab isn't where you expect it. A deep link to a specific tab selects the right section automatically.

- **pktLog**: **Collectors · Ingest**

| Section | Tab | What it controls |
|---|---|---|
| **Common** | General | App name, timezone (also governs RFC 3164 timestamp localization), Restart Service |
| | Security → Users | Accounts, roles (admin-only) |
| | Security → Auth | Local auth toggle, SAML SSO config |
| | Security → Suite Integration | Suite token for pktHub proxying |
| | Security → SSL/TLS | HTTPS toggle, cert/key upload |
| | Data → Storage | Storage backend settings |
| | Data → Backups | Schedule, retention, manual trigger, restore |
| | Notifications | Slack, Email (SMTP), PagerDuty, generic Webhook, TraceCat SOAR — six channels total |
| | Resonance | Embedded assistant — server address, key, who may use it, placement (admin-only) |
| | User Keys | Per-user IP-lookup provider keys (ipinfo.io, ipapi.is, AbuseIPDB, MXToolbox, IPQualityScore — IPQualityScore can be saved/tested but isn't consumed by the lookup endpoint yet) |
| | System | Version/build info, host and runtime details, open-source notices |
| **pktLog** | Collectors | Registered syslog sources — the ingest allowlist |
| | Ingest | Syslog listener configuration |

## Collector registry (ingest gating)

`app/ingest/normalizer.py`'s `enrich()` returns `None` — record dropped, never reaches storage — for any `collector_ip` not present and `enabled=1` in the collector registry. Approve every real syslog source on the Approval page before expecting its data to show up. The drop path also records the sender in `pending_collectors` (via `app/ingest/pending.py`, flushed once per 60s alert tick) so it appears in that queue, and fires a `new_host`/"Unknown collector" alert linking to it, rather than silently vanishing without a trace.

## Timestamp handling

RFC 3164 syslog has no timezone marker in its timestamp. `_parse_ts_3164()` localizes the parsed naive datetime using the app's configured **Timezone** setting (Settings → General) before converting to UTC for storage — get this setting right, since many real devices log local time rather than UTC. `received_at` (always server-side UTC at receipt) is unaffected either way and is the reliable field to sanity-check against if timestamps ever look off. The parser also handles headerless/non-standard lines (e.g. UniFi CEF security events, kernel/wireless-driver debug lines with no syslog header at all) rather than dropping `source_ip`/`timestamp` for them.

## Storage

ClickHouse holds `pktlog.syslog_events` (18 columns, including `dest_ip` lifted from `DST=` KV pairs in netfilter/firewall-style lines — blank for lines without that concept, not broken). SQLite holds app config, auth, alerts, the collector registry, per-user API keys, and outbound integrations. Dashboard/severity-breakdown queries bound their time window on both ends (excluding future-timestamped rows from clock-skewed or malformed device timestamps) so a few bad records don't skew counts.

## Backup & Restore

Configure schedule, rotation, and path at Data → Backups (or trigger immediately with **Run Backup Now**). Each snapshot is a timestamped directory containing `pktlog.db`, `config.yaml`, and (if enabled) a ClickHouse `syslog_events` export.

**Restoring:**
- Every listed snapshot has a **Restore…** link — restores directly from that on-server snapshot, no download/upload needed. Expanding it shows a checkbox per file present, so you can restore just one piece instead of everything.
- A full bundle can also be exported/imported as a `.tar.gz`, with the same per-file selection on upload.
- Restoring `config.yaml` invalidates existing sessions and needs a service restart to actually apply.

## Suite Integration (pktHub)

Settings → Security → Suite Integration → copy the token, register in pktHub's App Manager. pktHub can then proxy pktLog with users already signed in, and can remotely lock this app's Settings page (shows a "remotely managed" banner here) or force all direct browser access into a redirect (`POST /api/suite/direct-access`). That lock auto-clears if pktHub goes unreachable for more than 5 minutes or is down at this app's startup, so a lock can't permanently strand admins out.

Where that redirect points is no longer set here. pktHub sends the address along with the lock, because it is built from the hub's own Base URL and this app's id in the hub's registry — neither of which is visible from this side, so asking an operator to type it locally meant guessing a foreign key. The field at Settings → Integrations → pktHub Integration remains for an install with no pktHub in front of it; it takes http/https only, since every visitor follows it while the lock is on, and pktHub overwrites it whenever it applies a lock.

There's also an **outbound** Integrations API (`app/api/integrations.py`) for named connections *from* pktLog *to* sibling pkt apps (pktipam/pktflow/pktsnmp/pktpcap/pktwifi/pkthub) — backend/DB only as of this writing, no consumer feature reads from it yet.

## Resonance (embedded assistant)

Settings → Resonance. Adds an assistant launcher to the bottom corner of every page, in the same place the old in-app AI Assistant used to sit. The assistant itself runs on the resonance server; pktLog only decides who may open it.

**Setting it up.** Paste the **interface server** address — not resonance's admin portal, which answers on a different address and serves `embed.js` too, so it looks right until the session call returns "not found" — and the key you were issued, tick which roles may use it, press **Test Connection**, then switch **Enabled** on. Test Connection works whether or not the feature is enabled — you should always prove a key before putting the widget in front of users.

Two things have to line up on the resonance side, and both cause silent failures if they don't:

- **This install's origin must be on the key.** The exact text to copy is shown under Diagnostics. If it isn't listed, the launcher appears and the panel stays blank.
- **The key's session length should be 480 minutes**, matching pktLog's own session timeout. The panel warns if they differ.

Turn on **Speakers Name** on the key too. Without it resonance records nothing — no trace of who asked what.

**What a successful test tells you.** Test Connection reads back what the key actually permits — ask, microphone, speech, the rate limits, and the session length — so you can see, for example, that voice is switched off on this key rather than wondering why the button never appears.

**Requirements that are easy to miss.**

- Resonance must be reachable **from the browser**, over HTTPS, with a certificate the browser already trusts. A self-signed certificate gives an empty widget and nothing in the console to explain it.
- Voice needs pktLog itself on HTTPS. Over plain HTTP the microphone cannot work at all, so pktLog hides it rather than showing a dead button. Text chat is unaffected.

**What the assistant will and won't answer** is configured on the resonance side, by the profile the key is authorised against — not on this page. This page controls who may open it, not what it may discuss. Resonance can be pointed at `GET /api/resonance/docs` to keep its knowledge of pktLog in step with the installed version.

**If it doesn't appear.** Diagnostics reports how many users could not load the widget in the last week; the usual causes are an ad blocker, a wrong server address, or resonance being unreachable. Repeated failures pause the integration for a few minutes rather than hammering resonance — the panel says so when that happens, and a successful Test Connection clears it.

## Alert engine

Rule types, grouped as they appear in the New Rule picker:

| Group | Rule types |
|---|---|
| Volume | Threshold, Rate spike (vs. rolling baseline), Top talker |
| Infrastructure | Data gap (silent collector), New host (unrecognized collector), Ingest rate low, ClickHouse table size |

## Notification channels

Six channels, all configured on the Notifications tab and dispatched from `app/alerts/engine.py`: in-app, Slack, Email (SMTP), PagerDuty, generic Webhook, TraceCat SOAR. Enabling a channel doesn't send anything by itself — it makes it available to alert rules. **Send Test** performs a real dispatch with whatever's currently filled in, even unsaved.

## Known deployment gotcha

**A live systemd unit can drift silently from the repo template.** The repo's `pktlog.service` runs `ExecStart=<install_dir>/start.sh`, but an already-installed unit is a separate file on disk that only updates by re-running the relevant part of `install.sh` or reinstalling the unit — editing `start.sh` alone has no effect if the installed unit bypasses it with a direct `uvicorn ...` `ExecStart`. If a backend fix doesn't seem to take effect after a restart, compare `systemctl cat pktlog | grep ExecStart` against the repo's `pktlog.service` before assuming the code is wrong.

## Troubleshooting

| Symptom | Check |
|---|---|
| Service won't start | `journalctl -u pktlog -n 50`; check `config.yaml` paths and secret key |
| A device is sending syslog but nothing shows up | Check the Approval page — it is almost certainly queued there awaiting approval. Also confirm an already-approved collector is still marked enabled |
| Timestamps look shifted by a fixed offset | Check the Timezone setting (Settings → General) against how the sending device actually logs (local time vs. UTC) |
| Locked out of every account | Reset the admin password directly against SQLite (see Users & roles above) |
| A restored `config.yaml` didn't take effect | Restart the service — restoring never does this automatically |

## Upgrading

Pull the latest code, rebuild the frontend if you build manually (`cd frontend && npm install && npm run build`), then restart the service. Database/schema migrations run automatically on startup.
