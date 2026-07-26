"""
Application log viewer endpoints.

GET  /api/logs          — query recent log records (filterable)
GET  /api/logs/stats    — count by level + distinct logger names
DELETE /api/logs        — clear all log records (admin only)
POST /api/logs/level    — change the live capture level (admin only)
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, Query

from app.config import get_settings
from app.database import get_db
from app.dependencies import require_admin, get_current_user

log = logging.getLogger("pktlog.api.logs")
router = APIRouter()

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_LEVEL_NOS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


# ── Query logs ────────────────────────────────────────────────────────────────

@router.get("", dependencies=[Depends(get_current_user)])
async def get_logs(
    level: Optional[str] = Query(None, description="Minimum level: DEBUG|INFO|WARNING|ERROR|CRITICAL"),
    logger: Optional[str] = Query(None, description="Filter by logger name prefix, e.g. pktlog.ingest"),
    search: Optional[str] = Query(None, description="Substring search in message"),
    since: Optional[str] = Query(None, description="ISO-8601 timestamp — only return records after this"),
    until: Optional[str] = Query(None, description="ISO-8601 timestamp — only return records at or before this"),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> dict:
    cfg = get_settings()

    # Build WHERE clauses
    conditions: list[str] = []
    params: list = []

    if level and level.upper() in _VALID_LEVELS:
        conditions.append("level_no >= ?")
        params.append(_LEVEL_NOS[level.upper()])

    if logger:
        conditions.append("logger LIKE ?")
        params.append(f"{logger}%")

    if search:
        conditions.append("message LIKE ?")
        params.append(f"%{search}%")

    if since:
        conditions.append("ts > ?")
        params.append(since)

    if until:
        conditions.append("ts <= ?")
        params.append(until)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    async with aiosqlite.connect(cfg.db_path) as db:
        db.row_factory = aiosqlite.Row

        # Total count for pagination
        async with db.execute(
            f"SELECT COUNT(*) FROM app_logs {where}", params
        ) as cur:
            total = (await cur.fetchone())[0]

        # Fetch rows newest-first
        async with db.execute(
            f"""
            SELECT id, ts, level, level_no, logger, message, exc_info
            FROM app_logs
            {where}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ) as cur:
            rows = await cur.fetchall()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "records": [
            {
                "id":       r["id"],
                "ts":       r["ts"],
                "level":    r["level"],
                "level_no": r["level_no"],
                "logger":   r["logger"],
                "message":  r["message"],
                "exc_info": r["exc_info"],
            }
            for r in rows
        ],
    }


# ── Stats ─────────────────────────────────────────────────────────────────────

@router.get("/stats", dependencies=[Depends(get_current_user)])
async def get_log_stats() -> dict:
    """Return count per level and the distinct logger names seen."""
    cfg = get_settings()

    async with aiosqlite.connect(cfg.db_path) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute(
            "SELECT level, COUNT(*) as cnt FROM app_logs GROUP BY level ORDER BY level_no DESC"
        ) as cur:
            by_level = {r["level"]: r["cnt"] for r in await cur.fetchall()}

        async with db.execute(
            "SELECT DISTINCT logger FROM app_logs ORDER BY logger"
        ) as cur:
            loggers = [r["logger"] for r in await cur.fetchall()]

        async with db.execute("SELECT COUNT(*) FROM app_logs") as cur:
            total = (await cur.fetchone())[0]

        async with db.execute(
            "SELECT ts FROM app_logs ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            latest_ts = row["ts"] if row else None

    handler = _get_sqlite_handler()
    capture_level = logging.getLevelName(handler.level) if handler else "WARNING"

    return {
        "total": total,
        "by_level": by_level,
        "loggers": loggers,
        "latest_ts": latest_ts,
        "capture_level": capture_level,
    }


# ── Clear logs ────────────────────────────────────────────────────────────────

@router.delete("", dependencies=[Depends(require_admin)])
async def clear_logs() -> dict:
    """Delete all log records. Admin only."""
    cfg = get_settings()
    async with aiosqlite.connect(cfg.db_path) as db:
        await db.execute("DELETE FROM app_logs")
        await db.commit()
    log.info("App logs cleared by admin")
    return {"status": "cleared"}


# ── Live level change ─────────────────────────────────────────────────────────

@router.post("/level", dependencies=[Depends(require_admin)])
async def set_log_level(level: str, db: aiosqlite.Connection = Depends(get_db)) -> dict:
    """
    Change the minimum captured log level at runtime.
    Use 'DEBUG' for deep troubleshooting, reset to 'WARNING' when done.

    pktlog runs multiple uvicorn workers (see pktlog.service), each a
    separate process with its own in-memory SQLiteLogHandler — so this
    can't just flip the level in the process that happens to handle this
    request. The level is persisted to the settings table; every worker's
    handler polls it on each background-flush tick (~every
    flush_interval seconds, see app/logging_handler.py) and applies it
    when it changes, in addition to being applied immediately here for
    the current process.
    """
    level = level.upper()
    if level not in _VALID_LEVELS:
        from fastapi import HTTPException
        raise HTTPException(400, f"Invalid level '{level}'. Must be one of {sorted(_VALID_LEVELS)}")

    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('log_capture_level', ?)",
        (json.dumps(level),),
    )
    await db.commit()

    # Apply immediately in this process too, rather than waiting for the
    # next poll cycle.
    try:
        from app.logging_handler import SQLiteLogHandler
        root_logger = logging.getLogger("pktlog")
        for h in root_logger.handlers:
            if isinstance(h, SQLiteLogHandler):
                h.set_capture_level(_LEVEL_NOS[level])
                break
        else:
            # Handler not found — just set the logger level directly
            root_logger.setLevel(_LEVEL_NOS[level])
    except Exception as e:
        log.warning(f"set_log_level: {e}")

    log.info(f"Log capture level changed to {level}")
    return {"status": "ok", "level": level}


# ── Helper ────────────────────────────────────────────────────────────────────

def _get_sqlite_handler():
    """Return the first SQLiteLogHandler found on the pktlog logger, if any."""
    try:
        from app.logging_handler import SQLiteLogHandler
        for h in logging.getLogger("pktlog").handlers:
            if isinstance(h, SQLiteLogHandler):
                return h
    except ImportError:
        pass
    return None
