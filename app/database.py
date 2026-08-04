"""
SQLite async database engine for the pktFlow app sidecar DB
(users, settings, devices, alert rules, alert events, notification log).
"""
from __future__ import annotations

import aiosqlite
from pathlib import Path
from typing import AsyncGenerator

from app.config import get_settings

_settings = get_settings()
DB_PATH = _settings.db_path


async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """FastAPI dependency — yields an open aiosqlite connection per request."""
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        yield conn


async def init_db() -> None:
    """Run migrations on startup. Safe to call multiple times (idempotent SQL)."""
    migration_dir = Path(__file__).parent.parent / "migrations"
    migration_files = sorted(migration_dir.glob("*.sql"))

    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")

        # Track which migrations have been applied
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                filename TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await conn.commit()

        for mfile in migration_files:
            async with conn.execute(
                "SELECT 1 FROM _migrations WHERE filename = ?", (mfile.name,)
            ) as cur:
                already_applied = await cur.fetchone()

            if not already_applied:
                sql = mfile.read_text()
                await conn.executescript(sql)
                await conn.execute(
                    "INSERT INTO _migrations (filename) VALUES (?)", (mfile.name,)
                )
                await conn.commit()

        await _encrypt_legacy_api_keys(conn)


async def _encrypt_legacy_api_keys(conn: aiosqlite.Connection) -> None:
    """One-time data migration: user_api_keys.api_key used to be stored in
    plaintext. Encrypt any row that isn't already a valid Fernet token.
    Tracked via _migrations (same table the .sql migrations use) so this
    only does real work once."""
    marker = "999_encrypt_legacy_user_api_keys.py"
    async with conn.execute(
        "SELECT 1 FROM _migrations WHERE filename = ?", (marker,)
    ) as cur:
        if await cur.fetchone():
            return

    from app.crypto import decrypt_str, encrypt_str

    async with conn.execute(
        "SELECT id, api_key FROM user_api_keys WHERE api_key != ''"
    ) as cur:
        rows = await cur.fetchall()

    for row_id, api_key in rows:
        try:
            already_encrypted = bool(decrypt_str(api_key))
        except Exception:
            already_encrypted = False
        if already_encrypted:
            continue
        await conn.execute(
            "UPDATE user_api_keys SET api_key = ? WHERE id = ?",
            (encrypt_str(api_key), row_id),
        )

    await conn.execute("INSERT INTO _migrations (filename) VALUES (?)", (marker,))
    await conn.commit()
