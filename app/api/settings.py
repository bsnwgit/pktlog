"""
GET/PUT /api/settings — runtime application settings.
All settings are stored as JSON values in the SQLite settings table.
"""
from __future__ import annotations

import json
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database import get_db
from app.dependencies import AdminUser, CurrentUser

router = APIRouter()

# ── Default settings (applied on first run) ───────────────────────────────────
DEFAULTS: dict[str, Any] = {
    # Storage
    "storage_backend": "clickhouse",       # clickhouse | duckdb
    "retention_days_raw": 90,
    "retention_days_hourly": 365,

    # Auth
    "auth_local_enabled": True,
    "session_timeout_minutes": 480,

    # SAML 2.0
    "okta_saml_enabled": False,
    "okta_saml_idp_entity_id": "",       # From Okta metadata: IdP Entity ID
    "okta_saml_idp_sso_url": "",         # From Okta metadata: IdP SSO URL
    "okta_saml_idp_cert": "",            # From Okta metadata: X.509 cert (no header/footer)
    "okta_saml_sp_entity_id": "",        # Defaults to base_url/api/auth/saml/metadata
    "okta_saml_sp_cert": "",             # Optional: SP cert for signed requests
    "okta_saml_sp_key": "",              # Optional: SP private key for signed requests

    # Notifications
    "notify_email_enabled": False,
    "notify_email_smtp_host": "",
    "notify_email_smtp_port": 587,
    "notify_email_smtp_tls": True,
    "notify_email_username": "",
    "notify_email_password": "",
    "notify_email_from": "",
    "notify_email_default_to": [],

    "notify_slack_enabled": False,
    "notify_slack_webhook_url": "",
    "notify_slack_channel": "#alerts",

    "notify_pagerduty_enabled": False,
    "notify_pagerduty_integration_key": "",

    "notify_webhook_enabled": False,
    "notify_webhook_url": "",
    "notify_webhook_method": "POST",
    "notify_webhook_headers": {},
    "notify_webhook_payload_template": '{"alert": "{{ alert_name }}", "message": "{{ message }}"}',

    "notify_tracecat_enabled": False,
    "notify_tracecat_webhook_url": "",   # TraceCat workflow webhook URL
    "notify_tracecat_api_token": "",     # Bearer token for TraceCat API auth (optional)

    # General
    "app_name": "pktLog",
    "base_url": "http://172.23.80.5:8768",
    "timezone": "UTC",

    # AI assistant
    "anthropic_api_key": "",          # Anthropic API key for in-app Claude assistant
    "ai_model": "claude-haiku-4-5-20251001",

    # Integrations
    "lucid_api_token": "",            # Lucidchart Personal Access Token for diagram export

    # SSL / TLS
    "ssl_enabled": False,             # Enable HTTPS/WSS
    "ssl_certfile": "",               # Absolute path to PEM cert file on server
    "ssl_keyfile": "",                # Absolute path to PEM private key on server

    # Alerts
    "alert_event_retention_days": 90, # Days to keep alert_events + notification_log rows

    # Ingest
    "journal_max_gb": 5,            # Max disk for ingest file journal (GB)

    # Backup
    "backup_enabled": False,
    "backup_interval_hours": 24,
    "backup_rotation_count": 5,
    "backup_path": "/mnt/software/pktlog_backups",
    "backup_include_clickhouse": True,
}


# Sentinel mask written over secret values in GET responses.
# If the UI sends this value back on Save, we treat it as "unchanged" and skip the write.
_MASK = "••••••••"
_SECRET_KEYS = frozenset({
    "notify_email_password",
    "notify_pagerduty_integration_key", "anthropic_api_key", "lucid_api_token",
    "okta_saml_sp_key", "notify_tracecat_api_token",
})


async def _ensure_defaults(db: aiosqlite.Connection) -> None:
    for key, value in DEFAULTS.items():
        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )
    await db.commit()


@router.get("/")
async def get_all_settings(_: CurrentUser, db: aiosqlite.Connection = Depends(get_db)):
    """Return all settings as a flat dict. Sensitive values are masked."""
    await _ensure_defaults(db)
    async with db.execute("SELECT key, value FROM settings") as cur:
        rows = await cur.fetchall()

    result = {}
    for r in rows:
        try:
            result[r[0]] = json.loads(r[1])
        except (json.JSONDecodeError, TypeError, ValueError):
            result[r[0]] = r[1]  # fall back to raw string for non-JSON values

    # Mask secrets in API response
    for secret_key in _SECRET_KEYS:
        if result.get(secret_key):
            result[secret_key] = _MASK

    return result


@router.get("/{key}")
async def get_setting(key: str, _: CurrentUser, db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")
    return {key: json.loads(row[0])}


class SettingUpdate(BaseModel):
    value: Any


class TestNotificationRequest(BaseModel):
    channel: str


@router.put("/{key}")
async def update_setting(
    key: str,
    body: SettingUpdate,
    _: AdminUser,
    db: aiosqlite.Connection = Depends(get_db),
):
    if key not in DEFAULTS:
        raise HTTPException(status_code=400, detail=f"Unknown setting key: {key}")

    # Never overwrite a secret with the display mask
    if key in _SECRET_KEYS and body.value == _MASK:
        return {"key": key, "updated": False, "skipped": "mask value"}

    await db.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, json.dumps(body.value)),
    )
    await db.commit()

    # Side effects for certain settings
    if key == "retention_days_raw":
        from app.storage.factory import get_storage
        await get_storage().update_retention_ttl(int(body.value))

    return {"key": key, "updated": True}


@router.post("/bulk")
async def bulk_update(
    updates: dict[str, Any],
    _: AdminUser,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Update multiple settings at once (Settings page Save button)."""
    unknown = [k for k in updates if k not in DEFAULTS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown keys: {unknown}")

    skipped = []
    for key, value in updates.items():
        # Never overwrite a secret with the display mask (user saved without changing it)
        if key in _SECRET_KEYS and value == _MASK:
            skipped.append(key)
            continue
        await db.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value)),
        )
    await db.commit()
    written = [k for k in updates if k not in skipped]
    return {"updated": written, "skipped": skipped}


@router.post("/test-notification")
async def test_notification(
    body: TestNotificationRequest,
    _: AdminUser,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Send a test notification on the specified channel using saved settings."""
    channel = body.channel
    valid = {"slack", "email", "pagerduty", "webhook", "tracecat"}
    if channel not in valid:
        raise HTTPException(status_code=400, detail=f"Unknown channel: {channel}. Valid: {sorted(valid)}")

    async def _get(key: str):
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
        return json.loads(row[0]) if row else None

    TEST_RULE   = "pktLog Test"
    TEST_MSG    = "pktLog test notification — your configuration is working correctly."
    TEST_SEV    = "info"

    try:
        if channel == "slack":
            enabled = await _get("notify_slack_enabled")
            if not enabled:
                return {"status": "skipped", "detail": "Slack is not enabled"}
            url = await _get("notify_slack_webhook_url") or ""
            if not url:
                return {"status": "skipped", "detail": "No webhook URL configured"}
            import httpx
            payload = {"text": f":white_circle: *pktLog Test — {TEST_RULE}*\n{TEST_MSG}"}
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                return {"status": "sent", "detail": "Slack message delivered"}
            return {"status": "failed", "detail": f"Slack returned HTTP {resp.status_code}: {resp.text[:200]}"}

        elif channel == "email":
            enabled = await _get("notify_email_enabled")
            if not enabled:
                return {"status": "skipped", "detail": "Email is not enabled"}
            host      = await _get("notify_email_smtp_host")   or ""
            port      = await _get("notify_email_smtp_port")   or 587
            tls       = await _get("notify_email_smtp_tls")
            use_tls   = tls if tls is not None else True
            username  = await _get("notify_email_username")    or ""
            password  = await _get("notify_email_password")    or ""
            from_addr = await _get("notify_email_from")        or "pktlog@localhost"
            to_addrs  = await _get("notify_email_default_to")  or []
            if not host or not to_addrs:
                return {"status": "skipped", "detail": "SMTP host or recipient list not configured"}
            import aiosmtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[pktLog Test] {TEST_RULE}"
            msg["From"]    = from_addr
            msg["To"]      = ", ".join(to_addrs)
            msg.attach(MIMEText(f"pktLog Test Notification\n\n{TEST_MSG}", "plain"))
            await aiosmtplib.send(
                msg,
                hostname=host, port=int(port), use_tls=bool(use_tls),
                username=username or None, password=password or None,
            )
            return {"status": "sent", "detail": f"Email sent to {', '.join(to_addrs)}"}

        elif channel == "pagerduty":
            enabled = await _get("notify_pagerduty_enabled")
            if not enabled:
                return {"status": "skipped", "detail": "PagerDuty is not enabled"}
            key = await _get("notify_pagerduty_integration_key") or ""
            if not key:
                return {"status": "skipped", "detail": "No integration key configured"}
            import httpx
            payload = {
                "routing_key": key,
                "event_action": "trigger",
                "payload": {
                    "summary": f"[pktLog Test] {TEST_RULE}: {TEST_MSG}",
                    "severity": "info",
                    "source": "pktlog",
                },
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://events.pagerduty.com/v2/enqueue", json=payload, timeout=10
                )
            if resp.status_code in (200, 202):
                return {"status": "sent", "detail": "PagerDuty event triggered"}
            return {"status": "failed", "detail": f"PagerDuty returned HTTP {resp.status_code}: {resp.text[:200]}"}

        elif channel == "webhook":
            enabled = await _get("notify_webhook_enabled")
            if not enabled:
                return {"status": "skipped", "detail": "Webhook is not enabled"}
            url      = await _get("notify_webhook_url")              or ""
            method   = await _get("notify_webhook_method")           or "POST"
            template = await _get("notify_webhook_payload_template") or ""
            headers  = await _get("notify_webhook_headers")          or {}
            if not url:
                return {"status": "skipped", "detail": "No webhook URL configured"}
            try:
                from jinja2 import Template
                from datetime import datetime, timezone
                rendered = Template(template).render(
                    alert_name=TEST_RULE, message=TEST_MSG,
                    severity=TEST_SEV, fired_at=datetime.now(tz=timezone.utc).isoformat(),
                )
                body_json = json.loads(rendered)
            except Exception as e:
                return {"status": "failed", "detail": f"Template render error: {e}"}
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.request(
                    method.upper(), url, json=body_json, headers=headers, timeout=10
                )
            if resp.status_code < 300:
                return {"status": "sent", "detail": f"Webhook returned HTTP {resp.status_code}"}
            return {"status": "failed", "detail": f"Webhook returned HTTP {resp.status_code}: {resp.text[:200]}"}

        elif channel == "tracecat":
            enabled = await _get("notify_tracecat_enabled")
            if not enabled:
                return {"status": "skipped", "detail": "TraceCat is not enabled"}
            webhook_url = await _get("notify_tracecat_webhook_url") or ""
            api_token   = await _get("notify_tracecat_api_token")   or ""
            if not webhook_url:
                return {"status": "skipped", "detail": "No webhook URL configured"}
            from datetime import datetime, timezone
            payload = {
                "source": "pktlog",
                "event_id": 0,
                "alert_name": TEST_RULE,
                "severity": TEST_SEV,
                "message": TEST_MSG,
                "fired_at": datetime.now(tz=timezone.utc).isoformat(),
                "details": {"test": True},
            }
            headers: dict = {"Content-Type": "application/json"}
            if api_token:
                headers["Authorization"] = f"Bearer {api_token}"
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.post(webhook_url, json=payload, headers=headers, timeout=10)
            if resp.status_code < 300:
                return {"status": "sent", "detail": f"TraceCat webhook returned HTTP {resp.status_code}"}
            return {"status": "failed", "detail": f"TraceCat returned HTTP {resp.status_code}: {resp.text[:200]}"}

    except Exception as e:
        return {"status": "failed", "detail": str(e)}
