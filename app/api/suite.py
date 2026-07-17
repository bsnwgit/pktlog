"""
app/api/suite.py — pktHub integration endpoints.

Token flow (new):
  1. pktApp generates a random suite_token on first call to GET /api/suite/token
  2. Admin copies the token from Settings → Integrations → Suite Integration
  3. Admin pastes the token into pktHub App Manager when registering this app
  4. pktHub stores it and sends it as X-Suite-Token on every proxied request

GET  /api/suite/token    — returns current token (generates one if not set)
POST /api/suite/register — stores a new token (manual override)
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/token")
async def get_suite_token(request: Request):
    """Return the current suite token. Lazily generates one if not set."""
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
async def suite_register(request: Request):
    """
    Manual token override — stores a new suite token.
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
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/regenerate")
async def regenerate_suite_token(request: Request):
    """
    Replace the suite token with a freshly generated one.
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
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/direct-access")
async def set_direct_access(request: Request):
    """Hub sends this to lock/unlock direct UI access. Auth: X-Suite-Token."""
    from app.config import get_settings
    import aiosqlite as _aio
    settings = get_settings()
    suite_token = request.headers.get("x-suite-token", "")
    _stored_token = settings.suite_token  # get_settings() is uncached, JSON-decoded
    if not suite_token or not _stored_token or suite_token != _stored_token:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    locked = bool(body.get("locked", False))
    try:
        async with _aio.connect(settings.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('direct_ui_locked', ?)",
                ("true" if locked else "false",))
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('lock_heartbeat_at', datetime('now'))")
            await db.commit()
        return JSONResponse({"status": "ok", "direct_ui_locked": locked})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/direct-access")
async def get_direct_access(request: Request):
    """Return current lock state. Auth: X-Suite-Token."""
    from app.config import get_settings
    import aiosqlite as _aio
    settings = get_settings()
    suite_token = request.headers.get("x-suite-token", "")
    _stored_token = settings.suite_token  # get_settings() is uncached, JSON-decoded
    if not suite_token or not _stored_token or suite_token != _stored_token:
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
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


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
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.patch("/hub-redirect-url")
async def set_hub_redirect_url(request: Request):
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
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
