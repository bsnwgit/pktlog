"""
Data retention scheduler.

`retention_days_raw` was applied only at the instant an admin *saved* the
setting: app/api/settings.py calls `update_retention_ttl()` on write, and
nothing called it again afterwards.

On ClickHouse that is survivable, because the TTL clause it sets is then
enforced continuously by the database itself. On the DuckDB and SQLite
backends there is no such thing — `update_retention_ttl()` *is* the delete, so
retention ran once when the value was typed and never again. Rows accumulated
indefinitely while the Settings page showed a retention window that was being
honoured by nobody.

That is the same gap that let pktSNMP's poll table reach 129 million rows, and
it is closed here the same way: run it on a schedule, and log every run
including the ones that remove nothing.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import aiosqlite

from app.config import get_settings

log = logging.getLogger("pktlog.retention")

# Retention is expressed in days, so once a day is enough.
_INTERVAL_SECONDS = 86_400

# Let startup settle — migrations and the first ingest sweep run first.
_FIRST_RUN_DELAY_SECONDS = 300

_DEFAULT_RETENTION_DAYS = 90


class DataRetention:
    def __init__(self, interval_seconds: int = _INTERVAL_SECONDS):
        self._interval = interval_seconds
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run_loop())
        log.info(f"Data retention started (interval={self._interval}s)")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _retention_days(self) -> int:
        cfg = get_settings()
        try:
            async with aiosqlite.connect(cfg.db_path) as db:
                async with db.execute(
                    "SELECT value FROM settings WHERE key = 'retention_days_raw'"
                ) as cur:
                    row = await cur.fetchone()
            if not row:
                return _DEFAULT_RETENTION_DAYS
            return int(json.loads(row[0]))
        except Exception as e:
            log.warning(f"Could not read retention_days_raw ({e}) — using default")
            return _DEFAULT_RETENTION_DAYS

    async def run_once(self) -> dict:
        days = await self._retention_days()
        if days <= 0:
            log.info("Data retention disabled (retention_days_raw <= 0) — skipping")
            return {"skipped": True, "retention_days": days}

        from app.storage.factory import get_storage
        storage = get_storage()
        if storage is None:
            log.warning("Data retention: no storage backend available — skipping")
            return {"skipped": True, "reason": "no storage"}

        await storage.update_retention_ttl(days)
        log.info(
            f"Data retention run complete (retention={days}d, "
            f"backend={storage.__class__.__name__})"
        )
        return {"retention_days": days, "backend": storage.__class__.__name__}

    async def _run_loop(self) -> None:
        await asyncio.sleep(_FIRST_RUN_DELAY_SECONDS)
        while True:
            try:
                await self.run_once()
            except Exception as e:
                log.error(f"Data retention error: {e}")
            await asyncio.sleep(self._interval)
