"""
app/api/suite.py — pktHub integration endpoints.

Token flow (new):
  1. pktApp generates a random suite_token on first call to GET /api/suite/token
  2. Admin copies the token from Settings → Integrations → Suite Integration
  3. Admin pastes the token into pktHub App Manager when registering this app
  4. pktHub stores it and sends it as X-Suite-Token on every proxied request

GET  /api/suite/token    — returns current token (generates one if not set)
POST /api/suite/register — stores a new token (manual override)
GET  /api/suite/whoami   — authenticated identity check; a sibling pkt* app's
                           "Test Connection" button calls this (not the public
                           /api/health) so a wrong/revoked token fails the test
                           instead of silently reporting a healthy connection.
"""
import logging
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.dependencies import CurrentUser, AdminUser

log = logging.getLogger("pktlog.api.suite")

router = APIRouter()


@router.get("/token")
async def get_suite_token(request: Request, user: AdminUser):
    """Return the current suite token. Lazily generates one if not set. Admin-only."""
    from app.config import get_settings
    import aiosqlite, secrets as _sec
    settings = get_settings()
    token = ""
    try:
        # Read directly from DB — bypasses stale lru_cache
        async with aiosqlite.connect(settings.db_path) as db:
            async with db.execute("SELECT value FROM settings WHERE key='suite_token'") as cur:
                row = await cur.fetchone()
            _raw = (row[0] or "").strip() if row else ""
            try:
                import json as _json
                token = _json.loads(_raw) if _raw else ""
            except Exception:
                token = _raw
            if not token:
                # First call: generate and persist a fresh token
                new_token = _sec.token_urlsafe(32)
                await db.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES ('suite_token', ?)",
                    (new_token,)
                )
                await db.commit()
                token = new_token
    except Exception:
        token = (settings.suite_token or "").strip()

    return JSONResponse({"suite_token": token, "has_token": bool(token)})


@router.post("/register")
async def suite_register(request: Request, user: AdminUser):
    """
    Manual token override — stores a new suite token. Admin-only.
    In the new flow pktHub no longer calls this automatically,
    but it remains available for manual correction.
    Body: {"suite_token": "<new_token>"}
    """
    from app.config import get_settings
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    new_token = (body.get("suite_token") or "").strip()
    if not new_token:
        return JSONResponse({"error": "suite_token required"}, status_code=400)

    try:
        import aiosqlite
        settings = get_settings()
        async with aiosqlite.connect(settings.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('suite_token', ?)",
                (new_token,)
            )
            await db.commit()
        return JSONResponse({"status": "ok"})
    except Exception:
        # The exception text carries the database path and internal SQL, and
        # this endpoint answers pktHub over the network — log it, don't return it.
        log.exception("suite endpoint failed")
        return JSONResponse({"error": "Internal error"}, status_code=500)


@router.post("/regenerate")
async def regenerate_suite_token(request: Request, user: AdminUser):
    """
    Replace the suite token with a freshly generated one. Admin-only.
    Use when you need to revoke current pktHub access.
    After calling this, re-register the app in pktHub with the new token.
    """
    from app.config import get_settings
    import aiosqlite, secrets as _sec
    settings = get_settings()
    new_token = _sec.token_urlsafe(32)
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('suite_token', ?)",
                (new_token,)
            )
            await db.commit()
        return JSONResponse({"suite_token": new_token, "status": "regenerated"})
    except Exception:
        # The exception text carries the database path and internal SQL, and
        # this endpoint answers pktHub over the network — log it, don't return it.
        log.exception("suite endpoint failed")
        return JSONResponse({"error": "Internal error"}, status_code=500)


@router.post("/direct-access")
async def set_direct_access(request: Request):
    """Hub sends this to lock/unlock direct UI access. Auth: X-Suite-Token."""
    from app.config import get_settings
    import aiosqlite as _aio
    settings = get_settings()
    suite_token = request.headers.get("x-suite-token", "")
    _stored_token = settings.suite_token  # get_settings() is uncached, JSON-decoded
    if not suite_token or not _stored_token or not secrets.compare_digest(suite_token, _stored_token):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    locked = bool(body.get("locked", False))
    # pktHub sends the address it wants locked visitors sent to. It is the only
    # party that can build it — the URL carries the hub's own address and this
    # app's id in the hub's registry, neither of which is visible from here — so
    # it arrives with the lock rather than being typed in locally beforehand.
    redirect_url = (body.get("hub_redirect_url") or "").strip()
    # http/https only. This value arrives over the network, and once the lock is
    # on every visitor to this app follows it, so a "javascript:" target here
    # would be an XSS sink rather than a redirect.
    if redirect_url and not redirect_url.lower().startswith(("http://", "https://")):
        return JSONResponse(
            {"error": "hub_redirect_url must start with http:// or https://"},
            status_code=400,
        )
    try:
        async with _aio.connect(settings.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('direct_ui_locked', ?)",
                ("true" if locked else "false",))
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('lock_heartbeat_at', datetime('now'))")
            if redirect_url:
                await db.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES ('hub_redirect_url', ?)",
                    (redirect_url,))
            await db.commit()
        return JSONResponse({
            "status": "ok",
            "direct_ui_locked": locked,
            "hub_redirect_url": redirect_url,
        })
    except Exception:
        # The exception text carries the database path and internal SQL, and
        # this endpoint answers pktHub over the network — log it, don't return it.
        log.exception("suite endpoint failed")
        return JSONResponse({"error": "Internal error"}, status_code=500)


@router.get("/direct-access")
async def get_direct_access(request: Request):
    """Return current lock state. Auth: X-Suite-Token."""
    from app.config import get_settings
    import aiosqlite as _aio
    settings = get_settings()
    suite_token = request.headers.get("x-suite-token", "")
    _stored_token = settings.suite_token  # get_settings() is uncached, JSON-decoded
    if not suite_token or not _stored_token or not secrets.compare_digest(suite_token, _stored_token):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        async with _aio.connect(settings.db_path) as db:
            async with db.execute("SELECT value FROM settings WHERE key='direct_ui_locked'") as cur:
                row = await cur.fetchone()
            async with db.execute("SELECT value FROM settings WHERE key='hub_redirect_url'") as cur:
                rrow = await cur.fetchone()
        locked = bool(row and row[0] == "true")
        redirect_url = rrow[0] if rrow and rrow[0] else ""
        return JSONResponse({"direct_ui_locked": locked, "hub_redirect_url": redirect_url})
    except Exception:
        # The exception text carries the database path and internal SQL, and
        # this endpoint answers pktHub over the network — log it, don't return it.
        log.exception("suite endpoint failed")
        return JSONResponse({"error": "Internal error"}, status_code=500)


@router.get("/mode")
async def get_mode():
    """Public mode endpoint — returns direct_ui_locked and hub_redirect_url. No auth needed."""
    from app.config import get_settings
    import aiosqlite as _aio
    settings = get_settings()
    try:
        async with _aio.connect(settings.db_path) as db:
            async with db.execute("SELECT value FROM settings WHERE key='direct_ui_locked'") as cur:
                row = await cur.fetchone()
            async with db.execute("SELECT value FROM settings WHERE key='hub_redirect_url'") as cur:
                rrow = await cur.fetchone()
        return JSONResponse({
            "direct_ui_locked": bool(row and row[0] == "true"),
            "hub_redirect_url": rrow[0] if rrow and rrow[0] else "",
        })
    except Exception:
        # The exception text carries the database path and internal SQL, and
        # this endpoint answers pktHub over the network — log it, don't return it.
        log.exception("suite endpoint failed")
        return JSONResponse({"error": "Internal error"}, status_code=500)


@router.patch("/hub-redirect-url")
async def set_hub_redirect_url(request: Request, user: CurrentUser):
    """Set hub_redirect_url. Regular session auth (not suite token required)."""
    from app.config import get_settings
    import aiosqlite as _aio
    body = await request.json()
    url = (body.get("hub_redirect_url") or "").strip()
    settings = get_settings()
    try:
        async with _aio.connect(settings.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('hub_redirect_url', ?)", (url,))
            await db.commit()
        return JSONResponse({"status": "ok", "hub_redirect_url": url})
    except Exception:
        # The exception text carries the database path and internal SQL, and
        # this endpoint answers pktHub over the network — log it, don't return it.
        log.exception("suite endpoint failed")
        return JSONResponse({"error": "Internal error"}, status_code=500)


@router.post("/settings-lock")
async def set_settings_lock(request: Request, user: CurrentUser):
    """
    Called by pktHub on register/deregister to flag whether this app's
    Settings page should show a "remotely managed" banner and disable local
    editing. Narrower than a whole-app direct-access lock — everything else
    in the app keeps working normally, only the Settings UI is affected.
    Body: {"locked": true|false}
    """
    from app.config import get_settings
    import aiosqlite, json
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    locked = bool(body.get("locked"))
    settings = get_settings()
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('hub_settings_managed', ?)",
                (json.dumps(locked),)
            )
            await db.commit()
    except Exception:
        # The exception text carries the database path and internal SQL, and
        # this endpoint answers pktHub over the network — log it, don't return it.
        log.exception("suite endpoint failed")
        return JSONResponse({"error": "Internal error"}, status_code=500)

    return JSONResponse({"hub_settings_managed": locked})


@router.get("/whoami")
async def suite_whoami(user: CurrentUser):
    """
    Requires a valid X-Suite-Token (or a normal session) — used by callers to
    prove they can actually authenticate here, not just reach the port.
    """
    return {
        "authenticated": True,
        "app": "pktlog",
        "via_suite_token": bool(user.get("_via_suite")),
        "role": user["role"],
    }
