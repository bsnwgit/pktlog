"""
Scheduled on-server backup job.

Runs at a configurable interval (default every 24 hours).
Each run creates a timestamped snapshot directory under backup_path:
  <backup_path>/backup_2026-06-24_22-00/
    pktlog.db
    config.yaml
    flows.csv.gz   (if backup_include_clickhouse is True)

Rotates snapshots — keeps the newest `backup_rotation_count` directories
and deletes older ones.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiosqlite

from app.config import get_settings

log = logging.getLogger("pktlog.backup")
_cfg = get_settings()


def _read_backup_settings_sync(db_path: str) -> dict:
    """Read backup settings synchronously (called from thread)."""
    import sqlite3
    defaults = {
        "backup_enabled": False,
        "backup_interval_hours": 24,
        "backup_rotation_count": 5,
        "backup_path": "/mnt/software/pktlog_backups",
        "backup_include_clickhouse": True,
    }
    try:
        conn = sqlite3.connect(db_path)
        for key in defaults:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if row:
                try:
                    defaults[key] = json.loads(row[0])
                except (ValueError, TypeError):
                    pass
        conn.close()
    except Exception as e:
        log.warning(f"Could not read backup settings: {e}")
    return defaults


def run_backup_sync(db_path: str, ch_database: str) -> dict:
    """
    Perform one backup run synchronously (called via asyncio.to_thread).
    Returns a dict describing what was done.
    """
    s = _read_backup_settings_sync(db_path)
    result: dict = {"status": "ok", "path": "", "files": []}

    backup_root = Path(s["backup_path"])
    backup_root.mkdir(parents=True, exist_ok=True)

    ts = datetime.utcnow().strftime("%Y-%m-%d_%H-%M")
    snap_dir = backup_root / f"backup_{ts}"
    snap_dir.mkdir(parents=True, exist_ok=True)
    result["path"] = str(snap_dir)

    # ── SQLite ────────────────────────────────────────────────────────────────
    db_src = Path(db_path)
    if db_src.exists():
        shutil.copy2(str(db_src), str(snap_dir / "pktlog.db"))
        result["files"].append("pktlog.db")

    # ── config.yaml ───────────────────────────────────────────────────────────
    for candidate in [Path("config.yaml"), Path("/mnt/software/pktlog/config.yaml")]:
        if candidate.exists():
            shutil.copy2(str(candidate), str(snap_dir / "config.yaml"))
            result["files"].append("config.yaml")
            break

    # ── ClickHouse flows ──────────────────────────────────────────────────────
    if s["backup_include_clickhouse"]:
        import gzip as _gzip
        flows_gz = snap_dir / "flows.csv.gz"
        try:
            with _gzip.open(str(flows_gz), "wb") as gz_f:
                proc = subprocess.Popen(
                    [
                        "clickhouse-client",
                        "--host", "localhost",
                        "--port", "9000",
                        "--database", ch_database,
                        "--query", "SELECT * FROM flows FORMAT CSVWithNames",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                shutil.copyfileobj(proc.stdout, gz_f)
                proc.wait(timeout=600)
                if proc.returncode != 0:
                    err = proc.stderr.read().decode("utf-8", errors="replace")
                    log.warning(f"ClickHouse backup error: {err}")
            if flows_gz.exists() and flows_gz.stat().st_size > 100:
                result["files"].append("flows.csv.gz")
            else:
                flows_gz.unlink(missing_ok=True)
        except Exception as e:
            log.warning(f"ClickHouse backup failed: {e}")
            if flows_gz.exists():
                flows_gz.unlink(missing_ok=True)

    # ── Rotation ──────────────────────────────────────────────────────────────
    keep = int(s["backup_rotation_count"])
    snapshots = sorted(
        [d for d in backup_root.iterdir() if d.is_dir() and d.name.startswith("backup_")],
        key=lambda d: d.stat().st_mtime,
    )
    removed = []
    while len(snapshots) > keep:
        old = snapshots.pop(0)
        shutil.rmtree(str(old), ignore_errors=True)
        removed.append(old.name)
    if removed:
        log.info(f"Backup rotation: removed {removed}")

    result["kept"] = len(snapshots)
    log.info(f"Backup complete: {snap_dir} — files: {result['files']}")
    return result


def list_backups_sync(db_path: str) -> list[dict]:
    """List existing backup snapshots, newest first."""
    s = _read_backup_settings_sync(db_path)
    backup_root = Path(s["backup_path"])
    if not backup_root.exists():
        return []
    snapshots = sorted(
        [d for d in backup_root.iterdir() if d.is_dir() and d.name.startswith("backup_")],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    result = []
    for snap in snapshots:
        try:
            size = sum(f.stat().st_size for f in snap.rglob("*") if f.is_file())
            files = [f.name for f in snap.iterdir() if f.is_file()]
            result.append({
                "name": snap.name,
                "path": str(snap),
                "size_bytes": size,
                "files": files,
            })
        except Exception:
            pass
    return result


class BackupScheduler:
    _instance: "Optional[BackupScheduler]" = None

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        BackupScheduler._instance = self
        self._task = asyncio.create_task(self._run_loop())
        log.info("Backup scheduler started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self) -> None:
        while True:
            try:
                s = await asyncio.to_thread(_read_backup_settings_sync, _cfg.db_path)
                if s["backup_enabled"]:
                    await asyncio.to_thread(run_backup_sync, _cfg.db_path, _cfg.clickhouse_database)
                else:
                    log.debug("Backup disabled — skipping run")
            except Exception as e:
                log.error(f"Backup scheduler error: {e}")
            # Re-read interval each cycle so changes take effect without restart
            try:
                s = await asyncio.to_thread(_read_backup_settings_sync, _cfg.db_path)
                interval_secs = max(1, int(s["backup_interval_hours"])) * 3600
            except Exception:
                interval_secs = 86400
            await asyncio.sleep(interval_secs)
