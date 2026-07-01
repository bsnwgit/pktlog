"""
Storage backend factory.
Reads the 'storage_backend' setting from SQLite and returns the right backend.
Falls back to ClickHouse on startup (before settings DB is available).
"""
from __future__ import annotations

import logging
from typing import Optional

from app.storage.base import StorageBackend

log = logging.getLogger("pktlog.storage")
_instance: Optional[StorageBackend] = None


async def init_storage(backend: str = "clickhouse") -> None:
    """Called on app startup. Backend can be overridden by runtime settings."""
    global _instance

    # Try to read from settings DB first
    try:
        import aiosqlite
        from app.config import get_settings
        async with aiosqlite.connect(get_settings().db_path) as db:
            async with db.execute(
                "SELECT value FROM settings WHERE key = 'storage_backend'"
            ) as cur:
                row = await cur.fetchone()
                if row:
                    import json
                    backend = json.loads(row[0])
    except Exception:
        pass  # Use the passed-in default

    if backend == "duckdb":
        from app.storage.duckdb import DuckDBBackend
        _instance = DuckDBBackend()
    else:
        from app.storage.clickhouse import ClickHouseBackend
        _instance = ClickHouseBackend()

    await _instance.connect()
    log.info(f"Storage backend initialized: {backend}")


def get_storage() -> StorageBackend:
    if _instance is None:
        raise RuntimeError("Storage not initialized — call init_storage() first")
    return _instance
