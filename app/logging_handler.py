"""
SQLite logging handler for pktLog in-app log viewer.

Intercepts Python log records from the 'pktlog' logger hierarchy and
writes them to the app_logs SQLite table so they can be queried via
the /api/logs endpoint.

Design notes:
- Uses a background daemon thread + queue to decouple log I/O from the
  hot path (emit() never blocks callers).
- A ring-buffer cap (default 10 000 rows) is enforced after every batch
  flush to keep the table small.
- Level is configurable at runtime (default WARNING) — set via
  SQLiteLogHandler.set_level(logging.DEBUG) when deep troubleshooting.
"""
from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import traceback
from pathlib import Path
from typing import Optional

_SENTINEL = object()   # signals the worker to stop


class SQLiteLogHandler(logging.Handler):
    """
    Async, queue-backed logging.Handler that persists records to SQLite.

    Usage (called once at app startup):
        handler = SQLiteLogHandler(db_path="/path/to/pktlog.db")
        handler.attach_to_root_logger("pktlog")
    """

    def __init__(
        self,
        db_path: str | Path,
        max_records: int = 10_000,
        batch_size: int = 50,
        flush_interval: float = 2.0,
    ) -> None:
        super().__init__(level=logging.WARNING)
        self.db_path = str(db_path)
        self.max_records = max_records
        self.batch_size = batch_size
        self.flush_interval = flush_interval

        self._queue: queue.Queue = queue.Queue(maxsize=5_000)
        self._worker = threading.Thread(
            target=self._run,
            name="pktlog-log-writer",
            daemon=True,
        )
        self._worker.start()

    # ── Public API ─────────────────────────────────────────────────────────────

    def attach_to_root_logger(self, logger_name: str = "pktlog") -> None:
        """Attach this handler to the named logger (and all children)."""
        root = logging.getLogger(logger_name)
        root.addHandler(self)
        # Ensure the root logger's effective level is at least as permissive
        # as ours so records actually reach this handler.
        if root.level == logging.NOTSET or root.level > self.level:
            root.setLevel(self.level)

    def set_capture_level(self, level: int) -> None:
        """Change the minimum captured level at runtime (e.g. DEBUG for troubleshooting)."""
        self.setLevel(level)
        # Also relax the parent logger if needed
        root = logging.getLogger("pktlog")
        if root.level > level:
            root.setLevel(level)

    def stop(self) -> None:
        """Flush remaining records and stop the background worker."""
        self._queue.put(_SENTINEL)
        self._worker.join(timeout=5)

    # ── logging.Handler interface ──────────────────────────────────────────────

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Format exc_info while we still have it on the record
            exc_text: Optional[str] = None
            if record.exc_info:
                exc_text = "".join(traceback.format_exception(*record.exc_info))

            payload = (
                record.levelname,
                record.levelno,
                record.name,
                self.format(record),
                exc_text,
            )
            self._queue.put_nowait(payload)
        except queue.Full:
            pass  # drop silently rather than block the caller
        except Exception:
            self.handleError(record)

    # ── Background worker ──────────────────────────────────────────────────────

    def _run(self) -> None:
        """Worker thread: drain the queue in batches, write to SQLite."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        insert_count = 0

        try:
            while True:
                batch: list = []
                try:
                    # Block until at least one item arrives, then collect more
                    first = self._queue.get(timeout=self.flush_interval)
                    if first is _SENTINEL:
                        break
                    batch.append(first)
                    # Drain up to batch_size - 1 more without blocking
                    for _ in range(self.batch_size - 1):
                        try:
                            item = self._queue.get_nowait()
                            if item is _SENTINEL:
                                break
                            batch.append(item)
                        except queue.Empty:
                            break
                except queue.Empty:
                    pass  # timeout — nothing to flush

                if not batch:
                    continue

                try:
                    conn.executemany(
                        """
                        INSERT INTO app_logs (level, level_no, logger, message, exc_info)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        batch,
                    )
                    insert_count += len(batch)

                    # Ring-buffer cleanup every ~500 inserts
                    if insert_count >= 500:
                        conn.execute(
                            """
                            DELETE FROM app_logs
                            WHERE id NOT IN (
                                SELECT id FROM app_logs
                                ORDER BY id DESC
                                LIMIT ?
                            )
                            """,
                            (self.max_records,),
                        )
                        insert_count = 0

                    conn.commit()
                except Exception:
                    # Never raise out of the worker thread
                    try:
                        conn.rollback()
                    except Exception:
                        pass
        finally:
            try:
                conn.commit()
                conn.close()
            except Exception:
                pass
