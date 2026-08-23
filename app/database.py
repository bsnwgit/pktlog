"""
SQLite async database engine for the pktFlow app sidecar DB
(users, settings, devices, alert rules, alert events, notification log).
"""
from __future__ import annotations

import fcntl
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
    """Run migrations on startup. Safe to call multiple times (idempotent SQL).

    pktlog runs under uvicorn --workers 2 (start.sh) — unlike every other
    pkt* app, which is single-process. That means this function's FastAPI
    lifespan hook fires concurrently in two separate OS processes on every
    restart, and any one-time data migration below (e.g.
    _encrypt_legacy_api_keys) can run in both processes at once the first
    time it executes, racing to UPDATE the same rows — a real SQLite
    corruption vector, confirmed 2026-08-04. A plain asyncio.Lock doesn't
    help since these are separate processes, not threads, so this takes a
    real cross-process file lock for the whole migration body; the second
    worker blocks here until the first finishes, then runs through the same
    idempotent checks as a no-op."""
    lock_path = Path(DB_PATH).with_suffix(".migrate.lock")
    lock_file = open(lock_path, "w")
    fcntl.flock(lock_file, fcntl.LOCK_EX)
    try:
        await _run_migrations()
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


async def _run_migrations() -> None:
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
        await _encrypt_legacy_suite_tokens(conn)
        await _convert_resonance_roles_to_levels(conn)


async def _convert_resonance_roles_to_levels(conn: aiosqlite.Connection) -> None:
    """One-time data migration: resonance_roles (a flat list of roles allowed to
    open the assistant) becomes resonance_role_levels (a level per role).

    Every role that was on the old list becomes "read" and every role that was
    not becomes "none". Deliberately nobody is migrated to "write": there were
    no write operations when the old setting was ticked, so nothing on that list
    was ever consent to let the assistant change anything. Granting it silently
    on upgrade is exactly the sort of thing an admin should have to do on purpose.

    The old key is removed rather than left behind, so there is no second
    setting that looks live and is read by nothing.
    """
    marker = "998_resonance_role_levels.py"
    async with conn.execute(
        "SELECT 1 FROM _migrations WHERE filename = ?", (marker,)
    ) as cur:
        if await cur.fetchone():
            return

    import json as _json

    async with conn.execute(
        "SELECT value FROM settings WHERE key = 'resonance_roles'"
    ) as cur:
        row = await cur.fetchone()

    legacy: list = []
    if row and row[0]:
        try:
            parsed = _json.loads(row[0])
            if isinstance(parsed, list):
                legacy = [str(r) for r in parsed]
        except (ValueError, TypeError):
            legacy = []

    async with conn.execute(
        "SELECT 1 FROM settings WHERE key = 'resonance_role_levels'"
    ) as cur:
        already_set = await cur.fetchone() is not None

    if not already_set:
        levels = {role: ("read" if role in legacy else "none")
                  for role in ("admin", "analyst", "viewer")}
        await conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("resonance_role_levels", _json.dumps(levels)),
        )

    await conn.execute("DELETE FROM settings WHERE key = 'resonance_roles'")
    await conn.execute("INSERT INTO _migrations (filename) VALUES (?)", (marker,))
    await conn.commit()


async def _encrypt_legacy_suite_tokens(conn: aiosqlite.Connection) -> None:
    """One-time data migration: integrations.suite_token used to be stored
    in plaintext. Encrypt any row that isn't already a valid Fernet token.
    Tracked via _migrations (same table the .sql migrations use) so this
    only does real work once. Runs inside _run_migrations(), which init_db()
    already wraps in a cross-process lock — see that function's docstring."""
    marker = "999_encrypt_legacy_suite_tokens.py"
    async with conn.execute(
        "SELECT 1 FROM _migrations WHERE filename = ?", (marker,)
    ) as cur:
        if await cur.fetchone():
            return

    from app.crypto import decrypt_str, encrypt_str

    async with conn.execute(
        "SELECT id, suite_token FROM integrations WHERE suite_token != ''"
    ) as cur:
        rows = await cur.fetchall()

    for row_id, suite_token in rows:
        try:
            already_encrypted = bool(decrypt_str(suite_token))
        except Exception:
            already_encrypted = False
        if already_encrypted:
            continue
        await conn.execute(
            "UPDATE integrations SET suite_token = ? WHERE id = ?",
            (encrypt_str(suite_token), row_id),
        )

    await conn.execute("INSERT INTO _migrations (filename) VALUES (?)", (marker,))
    await conn.commit()


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
