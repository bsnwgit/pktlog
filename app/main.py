"""
pktLog — FastAPI application entry point.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import init_db
from app.storage.factory import init_storage, get_storage

# ── Routers ───────────────────────────────────────────────────────────────────
from app.api import settings as settings_router, auth, users, system as system_router
from app.api import pktlog as pktlog_router
from app.api import logs as logs_router
from app.api import syslog as syslog_router
from app.api import collectors as collectors_router
from app.api import suite as suite_router
from app.api import widgets as widgets_router
from app.api import alerts as alerts_router
from app.api import ws as ws_router
from app.api import user_api_keys as user_api_keys_router
from app.api import ip_info as ip_info_router
from app.api import mxtoolbox as mxtoolbox_router
from app.api import ai as ai_router
from app.api import integrations as integrations_router
from app.api import docs as docs_router

settings = get_settings()
log = logging.getLogger("pktlog")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    # ── Startup ───────────────────────────────────────────────────────────────
    log.info("pktLog starting up")

    # Attach SQLite log handler (captures INFO+ by default)
    from app.logging_handler import SQLiteLogHandler
    _log_handler = SQLiteLogHandler(db_path=settings.db_path)
    _log_handler.attach_to_root_logger("pktlog")

    # Run SQLite migrations
    await init_db()
    log.info("Database migrations applied")

    # ── Direct access lock failsafe ───────────────────────────────────────────
    try:
        import aiosqlite as _aio, httpx as _hx
        async with _aio.connect(settings.db_path) as _db:
            async with _db.execute("SELECT value FROM settings WHERE key='direct_ui_locked'") as _cur:
                _lrow = await _cur.fetchone()
            if _lrow and _lrow[0] == "true":
                async with _db.execute("SELECT value FROM settings WHERE key='hub_redirect_url'") as _cur:
                    _rrow = await _cur.fetchone()
                _hub_url = _rrow[0] if _rrow and _rrow[0] else ""
                _hub_up = False
                if _hub_url:
                    try:
                        async with _hx.AsyncClient(verify=False, timeout=5) as _hc:
                            _r = await _hc.get(f"{_hub_url.rstrip('/')}/api/health")
                            _hub_up = _r.status_code == 200
                    except Exception:
                        pass
                if not _hub_up:
                    await _db.execute(
                        "INSERT OR REPLACE INTO settings (key, value) VALUES ('direct_ui_locked', 'false')")
                    await _db.commit()
                    log.warning("Startup failsafe: direct_ui_locked was set but hub unreachable — lock cleared")
                else:
                    log.info("Startup: direct_ui_locked=true, hub reachable — lock maintained")
    except Exception as _e:
        log.warning(f"Startup lock check: {_e}")
    # ─────────────────────────────────────────────────────────────────────────

    # Connect to storage backend
    await init_storage()
    log.info(f"Storage ready: {get_storage().__class__.__name__}")

    # Start alert engine
    from app.alerts.engine import AlertEngine
    engine = AlertEngine()
    await engine.start()
    log.info("Alert engine started")

    # Start alert event cleanup job
    from app.alerts.cleanup import AlertCleanup
    cleanup = AlertCleanup()
    await cleanup.start()
    log.info("Alert cleanup started")

    # Start backup scheduler
    from app.backup import BackupScheduler
    backup_scheduler = BackupScheduler()
    await backup_scheduler.start()
    log.info("Backup scheduler started")

    # Start syslog ingest listener
    from app.ingest.listener import get_listener
    listener = get_listener()
    await listener.start()

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    log.info("pktLog shutting down")
    await listener.stop()
    await engine.stop()
    await cleanup.stop()
    await backup_scheduler.stop()
    storage = get_storage()
    if hasattr(storage, "close"):
        await storage.close()
    _log_handler.stop()
    log.info("Shutdown complete")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="pktLog",
    description="Syslog Ingest Management & Visualization Platform",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

# ── Middleware ────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def _direct_access_lock(request: Request, call_next):
    """Block direct UI access when hub-managed mode is active."""
    from fastapi.responses import RedirectResponse
    import aiosqlite as _aio
    path = request.url.path
    _ALLOW_PFX = ("/api/health", "/api/suite/", "/api/auth/", "/assets/", "/logos/", "/static/", "/widgets/")
    if any(path == p or path.startswith(p) for p in _ALLOW_PFX):
        return await call_next(request)
    _cfg = get_settings()
    _suite_tk = request.headers.get("x-suite-token", "")
    try:
        async with _aio.connect(_cfg.db_path) as _db:
            _stored_token = _cfg.suite_token  # get_settings() is uncached, JSON-decoded
            if _suite_tk and _stored_token and _suite_tk == _stored_token:
                await _db.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES ('lock_heartbeat_at', datetime('now'))")
                await _db.commit()
                return await call_next(request)
            async with _db.execute("SELECT value FROM settings WHERE key='direct_ui_locked'") as _cur:
                _lrow = await _cur.fetchone()
            if _lrow and _lrow[0] == "true":
                async with _db.execute("SELECT value FROM settings WHERE key='lock_heartbeat_at'") as _cur:
                    _hrow = await _cur.fetchone()
                _expired = True
                if _hrow and _hrow[0]:
                    from datetime import datetime as _dt
                    try:
                        _last = _dt.fromisoformat(str(_hrow[0]).replace(" ", "T"))
                        _expired = (_dt.now() - _last).total_seconds() > 300
                    except Exception:
                        pass
                if _expired:
                    await _db.execute(
                        "INSERT OR REPLACE INTO settings (key, value) VALUES ('direct_ui_locked', 'false')")
                    await _db.commit()
                else:
                    async with _db.execute("SELECT value FROM settings WHERE key='hub_redirect_url'") as _cur:
                        _rrow = await _cur.fetchone()
                    _rurl = _rrow[0] if _rrow and _rrow[0] else ""
                    if _rurl:
                        return RedirectResponse(url=_rurl, status_code=302)
    except Exception:
        pass
    return await call_next(request)


# ── API Routers ───────────────────────────────────────────────────────────────

app.include_router(auth.router,            prefix="/api/auth",     tags=["auth"])
app.include_router(users.router,           prefix="/api/users",    tags=["users"])
app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])
app.include_router(system_router.router,   prefix="/api/system",   tags=["system"])
app.include_router(pktlog_router.router,   prefix="/api/pktlog",   tags=["pktlog"])
app.include_router(logs_router.router,     prefix="/api/logs",     tags=["logs"])
app.include_router(syslog_router.router,    prefix="/api/syslog",      tags=["syslog"])
app.include_router(collectors_router.router, prefix="/api/collectors", tags=["collectors"])
app.include_router(suite_router.router, prefix="/api/suite", tags=["suite"])
app.include_router(widgets_router.router,  prefix="/api",          tags=["widgets"])
app.include_router(alerts_router.router,   prefix="/api/alerts",   tags=["alerts"])
app.include_router(ws_router.router,       prefix="/api/ws",       tags=["websocket"])
app.include_router(user_api_keys_router.router, prefix="/api/user-api-keys", tags=["user-api-keys"])
app.include_router(ip_info_router.router,       prefix="/api/ip-info",       tags=["ip-info"])
app.include_router(mxtoolbox_router.router,     prefix="/api/mxtoolbox",     tags=["mxtoolbox"])
app.include_router(ai_router.router,            prefix="/api/ai",            tags=["ai"])
app.include_router(integrations_router.router,  prefix="/api/integrations",  tags=["integrations"])
app.include_router(docs_router.router,          prefix="/api/docs-content",  tags=["docs"])

# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/api/health", tags=["system"])
async def health(request: Request):
    import aiosqlite as _aio
    _locked = False; _rurl = ""
    try:
        async with _aio.connect(settings.db_path) as _db:
            async with _db.execute("SELECT value FROM settings WHERE key='direct_ui_locked'") as _cur:
                _row = await _cur.fetchone()
            _locked = bool(_row and _row[0] == "true")
            async with _db.execute("SELECT value FROM settings WHERE key='hub_redirect_url'") as _cur:
                _rrow = await _cur.fetchone()
            _rurl = _rrow[0] if _rrow and _rrow[0] else ""
            _suite_tk = request.headers.get("x-suite-token", "")
            if _suite_tk:
                _stored_token = get_settings().suite_token  # uncached, JSON-decoded
                if _stored_token and _suite_tk == _stored_token:
                    await _db.execute(
                        "INSERT OR REPLACE INTO settings (key, value) VALUES ('lock_heartbeat_at', datetime('now'))")
                    await _db.commit()
    except Exception:
        pass
    return {"status": "ok", "version": "0.1.0", "direct_ui_locked": _locked, "hub_redirect_url": _rurl}

# ── Serve React frontend (production build) ───────────────────────────────────
# In development, Vite's dev server handles this on a different port.
_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    # Serve static assets (JS, CSS, images) from /assets
    app.mount("/assets", StaticFiles(directory=str(_frontend_dist / "assets")), name="assets")

    # Serve public assets (logos, favicons, etc.) from /logos
    if (_frontend_dist / "logos").exists():
        app.mount("/logos", StaticFiles(directory=str(_frontend_dist / "logos")), name="logos")

    # Catch-all: serve index.html for all non-API routes (SPA client-side routing)
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(request: Request, full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        static_file = _frontend_dist / full_path
        if static_file.exists() and static_file.is_file():
            return FileResponse(str(static_file))
        index = _frontend_dist / "index.html"
        response = FileResponse(str(index))

        _cfg = settings
        _suite_tk = request.headers.get("x-suite-token", "")
        _spa_stored = get_settings().suite_token if _suite_tk else ""
        if _suite_tk and _spa_stored and _suite_tk == _spa_stored:
            from datetime import datetime, timedelta, timezone
            from jose import jwt as _jose_jwt
            from app.dependencies import _SUITE_ROLE_MAP
            _hub_user = request.headers.get("x-suite-user", "hub_user")
            _hub_role = request.headers.get("x-suite-role", "viewer")
            _local_role = _SUITE_ROLE_MAP.get(_hub_role, "analyst")
            _expire = datetime.now(tz=timezone.utc) + timedelta(hours=8)
            _payload = {"sub": "0", "role": _local_role, "exp": _expire, "type": "access"}
            _jwt = _jose_jwt.encode(_payload, _cfg.secret_key, algorithm=_cfg.algorithm)
            response.set_cookie("sso_access_token", _jwt,    max_age=60, httponly=False, samesite="lax")
            response.set_cookie("sso_role",         _local_role, max_age=60, httponly=False, samesite="lax")

        return response


# ── Entrypoint (used by systemd: python -m app.main) ─────────────────────────
if __name__ == "__main__":
    import json
    import sqlite3
    import uvicorn

    # Read SSL settings from SQLite before uvicorn starts
    _db_path = Path(__file__).parent.parent / "pktlog.db"
    _ssl_enabled = False
    _ssl_certfile = None
    _ssl_keyfile = None
    try:
        _conn = sqlite3.connect(str(_db_path))
        for _key in ("ssl_enabled", "ssl_certfile", "ssl_keyfile"):
            _row = _conn.execute("SELECT value FROM settings WHERE key=?", (_key,)).fetchone()
            if _row:
                _val = json.loads(_row[0])
                if _key == "ssl_enabled":
                    _ssl_enabled = bool(_val)
                elif _key == "ssl_certfile":
                    _ssl_certfile = _val if _val else None
                elif _key == "ssl_keyfile":
                    _ssl_keyfile = _val if _val else None
        _conn.close()
    except Exception as _e:
        log.warning(f"Could not read SSL settings from DB: {_e}. Starting without SSL.")

    _uvicorn_kwargs: dict = dict(
        host="0.0.0.0",
        port=8768,
        workers=2,
        log_level="info",
        access_log=False,
    )
    if _ssl_enabled and _ssl_certfile and _ssl_keyfile:
        _uvicorn_kwargs["ssl_certfile"] = _ssl_certfile
        _uvicorn_kwargs["ssl_keyfile"] = _ssl_keyfile
        log.info(f"SSL enabled — cert: {_ssl_certfile}")
    else:
        if _ssl_enabled:
            log.warning("SSL enabled in settings but cert/key paths are missing — starting without SSL.")

    uvicorn.run("app.main:app", **_uvicorn_kwargs)
