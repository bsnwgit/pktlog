"""
DuckDB storage backend — enterprise concurrency design.

Architecture
------------
DuckDB requires each connection to be accessed by a single thread at a time.
To support concurrent reads without blocking on in-progress writes we use a
pooled multi-connection design:

    Write path
    ──────────
    _WRITE_EXECUTOR (max_workers=1)
        └── _wconn  ← one connection, one thread, zero contention on writes

    Read path
    ─────────
    _READ_EXECUTOR (max_workers=READ_POOL_SIZE)
        └── _ReadPool (READ_POOL_SIZE connections)
                ├── conn-0  ← thread 0 acquires, queries, releases
                ├── conn-1  ← thread 1 acquires, queries, releases
                └── …

    Each read thread borrows a connection from the pool (blocking queue with
    timeout), executes its query, and returns the connection.  READ_POOL_SIZE
    concurrent read queries can execute simultaneously without blocking each
    other or the write thread.

Timeout strategy
────────────────
asyncio.wait({fut}, timeout=N) is used for read timeouts instead of
asyncio.wait_for().  The key difference: asyncio.wait() does NOT attempt to
cancel the underlying future when the timeout expires — it simply returns
(done, pending) and the caller handles the pending case.  This avoids the
Python 3.9 bug where wait_for's _cancel_and_wait() stalls indefinitely
trying to cancel a non-cancellable concurrent.futures thread.  The thread
always runs to completion in the background; the HTTP handler returns an
empty/default result immediately after the timeout.

Scaling
───────
READ_POOL_SIZE can be increased for heavier read workloads.  Writes are
inherently single-writer in DuckDB (OLAP workloads do not need concurrent
writers); the single write thread is not a bottleneck at typical syslog
ingest rates.
"""
from __future__ import annotations

import asyncio
import contextlib
import concurrent.futures
import logging
import queue as _queue
from datetime import datetime, timezone, timedelta
from typing import Optional

import duckdb

from app.config import get_settings
from app.storage.base import StorageBackend

log = logging.getLogger("pktlog.storage.duckdb")
settings = get_settings()

# ── Pool / executor sizing ─────────────────────────────────────────────────────
# READ_POOL_SIZE concurrent read queries.  Each needs its own DuckDB connection
# and its own executor thread.  4 is a sensible default; tune up for heavier
# dashboard / API workloads.
READ_POOL_SIZE: int = 4
READ_QUERY_TIMEOUT: float = 30.0   # seconds before a read returns None

_WRITE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="duckdb-write"
)
_READ_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=READ_POOL_SIZE, thread_name_prefix="duckdb-read"
)

# ── Schema ────────────────────────────────────────────────────────────────────
# Column order must match SyslogRecord.to_clickhouse_row()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS syslog_events (
    timestamp        TIMESTAMPTZ NOT NULL,
    received_at      TIMESTAMPTZ NOT NULL,
    source_ip        VARCHAR     DEFAULT '',
    source_name      VARCHAR     DEFAULT '',
    dest_ip          VARCHAR     DEFAULT '',
    facility         INTEGER     DEFAULT 0,
    facility_name    VARCHAR     DEFAULT '',
    severity         INTEGER     DEFAULT 6,
    severity_name    VARCHAR     DEFAULT 'info',
    program          VARCHAR     DEFAULT '',
    pid              VARCHAR     DEFAULT '',
    message          VARCHAR     DEFAULT '',
    raw              VARCHAR     DEFAULT '',
    collector_ip     VARCHAR     DEFAULT '',
    collector_name   VARCHAR     DEFAULT '',
    org              VARCHAR     DEFAULT '',
    log_group        VARCHAR     DEFAULT '',
    site             VARCHAR     DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_syslog_ts            ON syslog_events (timestamp);
CREATE INDEX IF NOT EXISTS idx_syslog_source_ip     ON syslog_events (source_ip);
CREATE INDEX IF NOT EXISTS idx_syslog_dest_ip       ON syslog_events (dest_ip);
CREATE INDEX IF NOT EXISTS idx_syslog_collector_ip  ON syslog_events (collector_ip);
CREATE INDEX IF NOT EXISTS idx_syslog_org           ON syslog_events (org);
CREATE INDEX IF NOT EXISTS idx_syslog_log_group     ON syslog_events (log_group);
CREATE INDEX IF NOT EXISTS idx_syslog_site          ON syslog_events (site);
"""


# ── Connection pool ───────────────────────────────────────────────────────────

class _ReadPool:
    """
    Bounded pool of DuckDB connections for concurrent read queries.

    Each connection is owned by at most one thread at a time (enforced by the
    blocking queue).  Threads acquire a connection, run their query, then
    return it to the pool.  If all connections are checked out the acquiring
    thread blocks up to `acquire_timeout` seconds before raising.
    """

    def __init__(self, db_path: str, size: int = READ_POOL_SIZE,
                 acquire_timeout: float = READ_QUERY_TIMEOUT):
        self._db_path = db_path
        self._size = size
        self._acquire_timeout = acquire_timeout
        self._pool: _queue.Queue[duckdb.DuckDBPyConnection] = _queue.Queue(maxsize=size)

    def open(self) -> None:
        """Open all pool connections.  Safe to call from any single thread."""
        for i in range(self._size):
            conn = duckdb.connect(self._db_path)
            self._pool.put(conn)
        log.info("DuckDB read pool open: %d connections at %s", self._size, self._db_path)

    @contextlib.contextmanager
    def acquire(self):
        """
        Context manager: check out a connection, yield it, then return it.
        Raises RuntimeError if no connection becomes available within the
        configured timeout.
        """
        try:
            conn = self._pool.get(timeout=self._acquire_timeout)
        except _queue.Empty:
            raise RuntimeError(
                f"DuckDB read pool exhausted — all {self._size} connections busy "
                f"after {self._acquire_timeout:.0f}s"
            )
        try:
            yield conn
        except Exception:
            # If the connection threw an error it may be invalid; replace it.
            try:
                conn.close()
            except Exception:
                pass
            try:
                conn = duckdb.connect(self._db_path)
            except Exception as e:
                log.error("DuckDB: failed to replace broken connection: %s", e)
                conn = None
            finally:
                if conn is not None:
                    self._pool.put(conn)
            raise
        else:
            self._pool.put(conn)

    @property
    def size(self) -> int:
        return self._size

    @property
    def available(self) -> int:
        return self._pool.qsize()

    def close_all(self) -> None:
        """Close every connection.  Call on shutdown."""
        closed = 0
        while True:
            try:
                conn = self._pool.get_nowait()
                try:
                    conn.close()
                except Exception:
                    pass
                closed += 1
            except _queue.Empty:
                break
        log.info("DuckDB read pool: %d connections closed", closed)


# ── Backend ───────────────────────────────────────────────────────────────────

class DuckDBBackend(StorageBackend):

    def __init__(self):
        self._wconn: Optional[duckdb.DuckDBPyConnection] = None
        self._read_pool: Optional[_ReadPool] = None
        self._db_path: str = settings.duckdb_path

    # ── Async dispatch helpers ─────────────────────────────────────────────────

    async def _write(self, fn):
        """Dispatch a write operation to the single write executor thread."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_WRITE_EXECUTOR, fn)

    async def _read(self, fn, timeout: float = READ_QUERY_TIMEOUT):
        """
        Dispatch a read operation to the read executor thread pool.

        asyncio.wait() is intentionally used instead of wait_for() so that
        expiry of the timeout does NOT attempt to cancel the running thread
        (which is not reliably possible for concurrent.futures tasks in
        Python 3.9).  The thread completes in the background; callers receive
        None and can return an empty result set gracefully.
        """
        loop = asyncio.get_event_loop()
        fut = loop.run_in_executor(_READ_EXECUTOR, fn)
        done, _ = await asyncio.wait({fut}, timeout=timeout)
        if fut in done:
            exc = fut.exception()
            if exc is not None:
                log.error("DuckDB read error: %s", exc, exc_info=exc)
                return None
            return fut.result()
        log.warning(
            "DuckDB read timed out after %.1fs — thread running to completion in background",
            timeout,
        )
        return None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """
        Open the write connection (+ apply schema) then warm up the read pool.
        Both happen on the write executor so DuckDB sees a consistent first-
        opener before the read pool connections are created.
        """
        def _open():
            # Write connection
            self._wconn = duckdb.connect(self._db_path)
            for stmt in _SCHEMA.strip().split(";"):
                s = stmt.strip()
                if s:
                    self._wconn.execute(s)

            # Read pool — open here so schema DDL is visible to pool conns
            self._read_pool = _ReadPool(
                self._db_path,
                size=READ_POOL_SIZE,
                acquire_timeout=READ_QUERY_TIMEOUT,
            )
            self._read_pool.open()

        await self._write(_open)
        log.info(
            "DuckDB ready — 1 write thread + %d-connection read pool at %s",
            READ_POOL_SIZE, self._db_path,
        )

    async def close(self) -> None:
        def _close():
            if self._read_pool:
                self._read_pool.close_all()
                self._read_pool = None
            if self._wconn:
                self._wconn.close()
                self._wconn = None

        await self._write(_close)
        # Shut down executors immediately — do NOT wait for threads.
        # Without this, Python's atexit waits forever for blocked DuckDB threads.
        _READ_EXECUTOR.shutdown(wait=False, cancel_futures=True)
        _WRITE_EXECUTOR.shutdown(wait=False, cancel_futures=True)
        log.info("DuckDB connections and executors closed")

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def pool_stats(self) -> dict:
        if self._read_pool is None:
            return {"status": "not_connected"}
        return {
            "pool_size": self._read_pool.size,
            "available": self._read_pool.available,
            "in_use": self._read_pool.size - self._read_pool.available,
        }

    # ── Write ─────────────────────────────────────────────────────────────────

    async def insert_batch(self, records: list) -> int:
        """Insert a batch of SyslogRecord objects. Returns count inserted."""
        if not records:
            return 0

        def _insert():
            rows = [r.to_clickhouse_row() for r in records]
            self._wconn.executemany(
                "INSERT INTO syslog_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            return len(rows)

        return await self._write(_insert)

    # ── Search ────────────────────────────────────────────────────────────────

    async def search(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        source_ip: Optional[str] = None,
        dest_ip: Optional[str] = None,
        collector_ip: Optional[str] = None,
        collector_name: Optional[str] = None,
        org: Optional[str] = None,
        log_group: Optional[str] = None,
        site: Optional[str] = None,
        severity_max: Optional[int] = None,
        facility: Optional[int] = None,
        program: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> dict:
        def _query():
            conditions: list[str] = []
            params: list = []
            if start:
                conditions.append("timestamp >= ?"); params.append(start)
            if end:
                conditions.append("timestamp <= ?"); params.append(end)
            if source_ip:
                conditions.append("source_ip = ?"); params.append(source_ip)
            if dest_ip:
                conditions.append("dest_ip = ?"); params.append(dest_ip)
            if collector_ip:
                conditions.append("collector_ip = ?"); params.append(collector_ip)
            if collector_name:
                conditions.append("collector_name = ?"); params.append(collector_name)
            if org:
                conditions.append("org = ?"); params.append(org)
            if log_group:
                conditions.append("log_group = ?"); params.append(log_group)
            if site:
                conditions.append("site = ?"); params.append(site)
            if severity_max is not None:
                conditions.append("severity <= ?"); params.append(severity_max)
            if facility is not None:
                conditions.append("facility = ?"); params.append(facility)
            if program:
                conditions.append("program = ?"); params.append(program)
            if search:
                conditions.append("message ILIKE ?"); params.append(f"%{search}%")

            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

            with self._read_pool.acquire() as conn:
                total = conn.execute(
                    f"SELECT COUNT(*) FROM syslog_events {where}", params
                ).fetchone()[0]

                rows = conn.execute(f"""
                    SELECT timestamp, received_at, source_ip, source_name, dest_ip,
                           facility, facility_name, severity, severity_name,
                           program, pid, message, raw,
                           collector_ip, collector_name, org, log_group, site
                    FROM syslog_events
                    {where}
                    ORDER BY timestamp DESC
                    LIMIT ? OFFSET ?
                """, params + [limit, offset]).fetchall()

            return total, rows

        result = await self._read(_query)
        total, rows = result if result else (0, [])
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "records": [_row_to_dict(r) for r in rows],
        }

    # ── Aggregations ──────────────────────────────────────────────────────────

    async def count_by_severity(self, hours: int = 24) -> list[dict]:
        def _query():
            cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
            with self._read_pool.acquire() as conn:
                return conn.execute("""
                    SELECT severity, severity_name, COUNT(*) AS cnt
                    FROM syslog_events
                    WHERE timestamp >= ?
                    GROUP BY severity, severity_name
                    ORDER BY severity ASC
                """, [cutoff]).fetchall()

        rows = await self._read(_query) or []
        return [{"severity": r[0], "severity_name": r[1], "count": r[2]} for r in rows]

    async def count_by_host(self, hours: int = 24, limit: int = 20) -> list[dict]:
        def _query():
            cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
            with self._read_pool.acquire() as conn:
                return conn.execute("""
                    SELECT source_ip, source_name, log_group, COUNT(*) AS cnt
                    FROM syslog_events
                    WHERE timestamp >= ?
                    GROUP BY source_ip, source_name, log_group
                    ORDER BY cnt DESC
                    LIMIT ?
                """, [cutoff, limit]).fetchall()

        rows = await self._read(_query) or []
        return [{"source_ip": r[0], "source_name": r[1], "log_group": r[2], "count": r[3]} for r in rows]

    async def top_programs(self, hours: int = 24, limit: int = 20) -> list[dict]:
        def _query():
            cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
            with self._read_pool.acquire() as conn:
                return conn.execute("""
                    SELECT program, COUNT(*) AS cnt
                    FROM syslog_events
                    WHERE timestamp >= ? AND program != ''
                    GROUP BY program
                    ORDER BY cnt DESC
                    LIMIT ?
                """, [cutoff, limit]).fetchall()

        rows = await self._read(_query) or []
        return [{"program": r[0], "count": r[1]} for r in rows]

    async def timeseries(
        self,
        hours: int = 24,
        bucket_minutes: int = 5,
        log_group: Optional[str] = None,
    ) -> list[dict]:
        def _query():
            cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
            bucket_ms = bucket_minutes * 60 * 1000
            extra = "AND log_group = ?" if log_group else ""
            params: list = [bucket_ms, bucket_ms, cutoff]
            if log_group:
                params.append(log_group)
            with self._read_pool.acquire() as conn:
                return conn.execute(f"""
                    SELECT
                        epoch_ms((epoch_ms(timestamp) // ?) * ?)::TIMESTAMPTZ AS bucket,
                        COUNT(*) AS cnt
                    FROM syslog_events
                    WHERE timestamp >= ? {extra}
                    GROUP BY bucket
                    ORDER BY bucket ASC
                """, params).fetchall()

        rows = await self._read(_query) or []
        return [{"bucket": r[0].isoformat() + "Z", "count": r[1]} for r in rows]

    async def collector_last_seen(self) -> list[dict]:
        """Last timestamp per collector — used for data-gap alerts."""
        def _query():
            with self._read_pool.acquire() as conn:
                return conn.execute("""
                    SELECT collector_ip, collector_name, MAX(timestamp) AS last_seen
                    FROM syslog_events
                    GROUP BY collector_ip, collector_name
                    ORDER BY last_seen DESC
                """).fetchall()

        rows = await self._read(_query) or []
        return [{"collector_ip": r[0], "collector_name": r[1], "last_seen": r[2].isoformat() + "Z"} for r in rows]

    # ── Retention ─────────────────────────────────────────────────────────────

    async def update_retention_ttl(self, days: int) -> None:
        """Delete rows older than `days` days (DuckDB has no built-in TTL)."""
        def _delete():
            cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
            # COUNT(*) of deleted rows via a subquery — DuckDB does not support
            # RETURNING in DELETE; fetch the count before deleting instead.
            count = self._wconn.execute(
                "SELECT COUNT(*) FROM syslog_events WHERE timestamp < ?", [cutoff]
            ).fetchone()[0]
            self._wconn.execute("DELETE FROM syslog_events WHERE timestamp < ?", [cutoff])
            return count

        n = await self._write(_delete)
        log.info("DuckDB retention purge: removed %d rows older than %d days", n, days)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_dict(r: tuple) -> dict:
    return {
        "timestamp":      (r[0].isoformat() + "Z") if r[0] else None,
        "received_at":    (r[1].isoformat() + "Z") if r[1] else None,
        "source_ip":      r[2],
        "source_name":    r[3],
        "dest_ip":        r[4],
        "facility":       r[5],
        "facility_name":  r[6],
        "severity":       r[7],
        "severity_name":  r[8],
        "program":        r[9],
        "pid":            r[10],
        "message":        r[11],
        "raw":            r[12],
        "collector_ip":   r[13],
        "collector_name": r[14],
        "org":            r[15],
        "log_group":      r[16],
        "site":           r[17],
    }
