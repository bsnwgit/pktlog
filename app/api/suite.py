"""
app/api/suite.py — pktHub integration endpoints.

Token flow (new):
  1. pktApp generates a random suite_token on first call to GET /api/suite/token
  2. Admin copies the token from Settings → Integrations → pktHub Integration
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
    token = (settings.suite_token or "").strip()

    if not token:
        # First call: generate and persist a fresh token
        new_token = _sec.token_urlsafe(32)
        try:
            async with aiosqlite.connect(settings.db_path) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES ('suite_token', ?)",
                    (new_token,)
                )
                await db.commit()
            token = new_token
        except Exception:
            pass

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
