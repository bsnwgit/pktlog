"""
Batch writer — drains the ingest queue and flushes to ClickHouse.

Flush triggers (whichever comes first):
  - 500 records in queue
  - 2 seconds elapsed since last flush

On ClickHouse failure:
  - Retries up to 3 times with 2s backoff
  - Overflows to file journal at /mnt/software/pktlog/ingest_journal/
  - Journal replayed automatically once ClickHouse recovers
  - Journal drops oldest files when total size exceeds configured cap (default 5 GB)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.models.syslog import SyslogRecord

log = logging.getLogger("pktlog.ingest.writer")

_BATCH_SIZE    = 500
_FLUSH_INTERVAL = 2.0        # seconds
_MAX_RETRIES   = 3
_RETRY_DELAY   = 2.0         # seconds
_JOURNAL_DIR   = Path("/mnt/software/pktlog/ingest_journal")
_DEFAULT_MAX_JOURNAL_BYTES = 5 * 1024 ** 3   # 5 GB


def _get_max_journal_bytes() -> int:
    """Read journal size cap from settings DB (synchronous, best-effort)."""
    try:
        import sqlite3, json as _json
        from app.config import get_settings
        conn = sqlite3.connect(get_settings().db_path)
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'journal_max_gb'"
        ).fetchone()
        conn.close()
        if row:
            gb = float(_json.loads(row[0]))
            return int(gb * 1024 ** 3)
    except Exception:
        pass
    return _DEFAULT_MAX_JOURNAL_BYTES


class BatchWriter:

    def __init__(self):
        self._queue: asyncio.Queue[SyslogRecord] = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        _JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="ingest-writer")
        log.info("BatchWriter started (batch=%d flush=%.1fs)", _BATCH_SIZE, _FLUSH_INTERVAL)
        # Replay any leftover journal files from previous run
        asyncio.create_task(self._replay_journal(), name="journal-replay")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Flush whatever remains in the queue
        remaining = []
        while not self._queue.empty():
            try:
                remaining.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if remaining:
            await self._flush(remaining)
        log.info("BatchWriter stopped")

    # ── Enqueue ───────────────────────────────────────────────────────────────

    async def enqueue(self, record: SyslogRecord) -> None:
        await self._queue.put(record)

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        batch: list[SyslogRecord] = []
        last_flush = time.monotonic()

        while self._running:
            try:
                timeout = _FLUSH_INTERVAL - (time.monotonic() - last_flush)
                timeout = max(0.05, timeout)
                record = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                batch.append(record)

                if len(batch) >= _BATCH_SIZE:
                    await self._flush(batch)
                    batch = []
                    last_flush = time.monotonic()

            except asyncio.TimeoutError:
                if batch:
                    await self._flush(batch)
                    batch = []
                last_flush = time.monotonic()

            except asyncio.CancelledError:
                break

        if batch:
            await self._flush(batch)

    # ── Flush ─────────────────────────────────────────────────────────────────

    async def _flush(self, batch: list[SyslogRecord]) -> None:
        from app.storage.factory import get_storage
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                storage = get_storage()
                count = await storage.insert_batch(batch)
                log.debug("Flushed %d records to ClickHouse", count)
                return
            except Exception as e:
                log.warning("ClickHouse insert attempt %d/%d failed: %s", attempt, _MAX_RETRIES, e)
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_RETRY_DELAY)

        # All retries exhausted — write to journal
        log.error("ClickHouse unavailable after %d retries — writing %d records to journal", _MAX_RETRIES, len(batch))
        await self._write_journal(batch)

    # ── Journal ───────────────────────────────────────────────────────────────

    async def _write_journal(self, batch: list[SyslogRecord]) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        path = _JOURNAL_DIR / f"{ts}.jsonl"
        try:
            lines = []
            for r in batch:
                row = r.to_clickhouse_row()
                lines.append(json.dumps([
                    _ser(v) for v in row
                ]))
            path.write_text("\n".join(lines), encoding="utf-8")
            log.info("Journal written: %s (%d records)", path.name, len(batch))
            await self._enforce_journal_cap()
        except Exception as e:
            log.error("Failed to write journal file %s: %s", path, e)

    async def _enforce_journal_cap(self) -> None:
        """Drop oldest journal files if total size exceeds cap."""
        try:
            cap = _get_max_journal_bytes()
            files = sorted(_JOURNAL_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
            total = sum(p.stat().st_size for p in files)
            while total > cap and files:
                oldest = files.pop(0)
                size = oldest.stat().st_size
                oldest.unlink()
                total -= size
                log.warning("Journal cap exceeded — dropped oldest file: %s", oldest.name)
        except Exception as e:
            log.error("Journal cap enforcement failed: %s", e)

    async def _replay_journal(self) -> None:
        """On startup, replay any journal files left from a previous outage."""
        files = sorted(_JOURNAL_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not files:
            return
        log.info("Replaying %d journal file(s) from previous outage", len(files))
        for path in files:
            try:
                await self._replay_file(path)
            except Exception as e:
                log.error("Journal replay failed for %s: %s", path.name, e)

    async def _replay_file(self, path: Path) -> None:
        from app.storage.factory import get_storage
        from app.models.syslog import SyslogRecord
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        batch = []
        for line in lines:
            vals = json.loads(line)
            r = SyslogRecord(
                timestamp       = datetime.fromisoformat(vals[0]),
                received_at     = datetime.fromisoformat(vals[1]),
                source_ip       = vals[2],
                source_name     = vals[3],
                facility        = vals[4],
                facility_name   = vals[5],
                severity        = vals[6],
                severity_name   = vals[7],
                program         = vals[8],
                pid             = vals[9],
                message         = vals[10],
                raw             = vals[11],
                collector_ip    = vals[12],
                collector_name  = vals[13],
                org             = vals[14],
                log_group       = vals[15],
                site            = vals[16],
            )
            batch.append(r)

        storage = get_storage()
        await storage.insert_batch(batch)
        path.unlink()
        log.info("Journal replayed and removed: %s (%d records)", path.name, len(batch))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ser(v) -> str:
    if isinstance(v, datetime):
        return v.isoformat()
    return v


# ── Module-level singleton ────────────────────────────────────────────────────

_writer: Optional[BatchWriter] = None


def get_writer() -> BatchWriter:
    global _writer
    if _writer is None:
        _writer = BatchWriter()
    return _writer
