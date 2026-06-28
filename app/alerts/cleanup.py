"""
Alert event + notification log auto-cleanup.

Runs once per day. Deletes notification_log rows and alert_events rows
whose fired_at is older than `alert_event_retention_days` days (default 90).

notification_log has a FK → alert_events.id, so notification_log rows
must be deleted first.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import aiosqlite

from app.config import get_settings

log = logging.getLogger("pktlog.cleanup")
settings = get_settings()

# How often the cleanup loop runs (seconds). Default: once per day.
_CLEANUP_INTERVAL = 86_400


class AlertCleanup:
    _instance: "Optional[AlertCleanup]" = None

    def __init__(self, interval_seconds: int = _CLEANUP_INTERVAL):
        self._interval = interval_seconds
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        AlertCleanup._instance = self
        self._task = asyncio.create_task(self._run_loop())
        log.info(f"Alert cleanup started (interval={self._interval}s)")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self) -> None:
        # Run once at startup, then repeat on interval
        while True:
            try:
                await self._cleanup()
            except Exception as e:
                log.error(f"Alert cleanup error: {e}")
            await asyncio.sleep(self._interval)

    async def _cleanup(self) -> None:
        db_path = settings.db_path
        async with aiosqlite.connect(db_path) as db:
            # Read retention days setting (default 90)
            retention_days = 90
            async with db.execute(
                "SELECT value FROM settings WHERE key = 'alert_event_retention_days'"
            ) as cur:
                row = await cur.fetchone()
                if row:
                    try:
                        retention_days = int(json.loads(row[0]))
                    except (ValueError, TypeError):
                        pass

            # Delete notification_log rows for old events (FK constraint — must go first)
            await db.execute(
                """
                DELETE FROM notification_log
                WHERE event_id IN (
                    SELECT id FROM alert_events
                    WHERE fired_at < datetime('now', ?)
                )
                """,
                (f"-{retention_days} days",),
            )

            # Delete old alert_events
            result = await db.execute(
                "DELETE FROM alert_events WHERE fired_at < datetime('now', ?)",
                (f"-{retention_days} days",),
            )
            deleted = result.rowcount
            await db.commit()

        if deleted > 0:
            log.info(f"Alert cleanup: removed {deleted} events older than {retention_days} days")
        else:
            log.debug(f"Alert cleanup: nothing to purge (retention={retention_days}d)")
