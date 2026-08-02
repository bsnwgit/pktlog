# pktLog — User Guide

This guide is for people who use pktLog to monitor and investigate syslog activity — not for installing or administering the server. See [ADMIN_GUIDE.md](ADMIN_GUIDE.md) for setup, users, backups, and integrations.

## Logging in

Log in with your username and password (Enter submits from either field), or via Okta SSO if your organization has it configured. If both local login and SSO are disabled by your admin, the app skips the login form entirely and signs everyone in as a designated default admin account — that's an intentional trusted-network setup, not a bug, if you encounter it.

Roles: `admin` (full access), `analyst` (read + export), `viewer` (read-only).

## Navigation

**Dashboard**, **Syslog Explorer**, **Alerts**, **Logs**, **Settings**. Settings is reachable by every role — the page itself hides admin-only tabs (Users, etc.) from analysts and viewers rather than hiding the whole page.

## Dashboard

A live overview of incoming syslog volume and severity breakdown across your registered collectors. Widgets can be filtered (e.g. by minimum severity) depending on how your admin has configured them.

## Syslog Explorer

The main search tool for raw syslog data. Filter by source, severity, and time range; use the page-size selector (25/50/75/100) to control how many results show per page. Click any IP address in a result to look it up (see below). Every log line shown has already passed through pktLog's collector-registry gate, RFC 3164/5424 parsing, and normalization — device/site enrichment (org/group/site) is applied automatically where the sending device is registered.

## Logs

A similar view to Syslog Explorer, oriented around application/system-level log records rather than raw syslog search — use whichever view fits what you're looking for. Same time-range and page-size controls.

## Alerts

Shows alerts fired automatically — most commonly a **new/unknown collector** alert when a device sends syslog from an IP that isn't yet registered (with a one-click "Register collector →" link), plus any other rule types your admin has configured. Acknowledge an alert if your role allows it, without needing to resolve the underlying condition yourself.

## Looking up an IP address

Any IP shown anywhere in the app is clickable, opening a lookup combining ipinfo.io, ipapi.is, AbuseIPDB, and MXToolbox (reverse DNS/ASN/blacklist) data — using **your own** per-user API keys (Settings → User Keys). Each provider can be individually shown/hidden from the same tab if you don't want to see certain sections.

## Getting help in the app

A **?** button near almost every page and Settings section opens a short explanation of what that feature does and anything non-obvious about its behavior.

For longer-form documentation, click **Documentation** in the sidebar (just above your account info) — it opens this guide and the Administrator Guide as in-app tabs, so you don't need the repo checked out to read them.

## Your account

Manage your own password from the user menu (unless your account uses SSO, in which case that's handled by your identity provider). Your personal lookup-provider API keys live under Settings → User Keys, visible only to you.

## AI Assistant

If your admin has configured an Anthropic API key, a floating chat button is available on every page — ask it questions about what you're looking at.
