"""
ClickHouse storage backend for pktLog syslog data.
Uses clickhouse-driver (sync) wrapped in asyncio.to_thread for non-blocking operation.
A threading.Lock serializes all ClickHouse calls so concurrent asyncio.to_thread()
invocations never share the connection simultaneously.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from clickhouse_driver import Client

from app.config import get_settings
from app.storage.base import StorageBackend

log = logging.getLogger("pktlog.storage.clickhouse")
settings = get_settings()

# Column order must match SyslogRecord.to_clickhouse_row()
_INSERT_COLS = """
    timestamp, received_at,
    source_ip, source_name, dest_ip,
    facility, facility_name,
    severity, severity_name,
    program, pid,
    message, raw,
    collector_ip, collector_name,
    org, log_group, site
"""


def _text_filter(column: str, param: str, match_mode: str) -> str:
    """Case-insensitive text condition for a free-text field filter.

    `positionCaseInsensitive` returns the 1-based position of the needle, or 0
    when absent — so `> 0` is "contains anywhere" and `= 1` is "starts with".
    The Explorer's "Exact" checkbox selects the anchored form, which narrows
    progressively as the user types rather than only matching a whole value.
    """
    op = "= 1" if match_mode == "prefix" else "> 0"
    return f"positionCaseInsensitive({column}, %({param})s) {op}"


class ClickHouseBackend(StorageBackend):

    def __init__(self):
        self._client: Optional[Client] = None
        self._lock = threading.Lock()

    # ── Connection ────────────────────────────────────────────────────────────

    def _get_client(self) -> Client:
        if self._client is None:
            self._client = Client(
                host=settings.clickhouse_host,
                port=settings.clickhouse_port,
                database=settings.clickhouse_database,
                user=settings.clickhouse_user,
                password=settings.clickhouse_password,
                connect_timeout=10,
                # A stale/dropped TCP connection (idle timeout, network blip)
                # can otherwise hang a socket read forever with no exception,
                # which — since every call goes through _execute()'s single
                # threading.Lock — freezes ALL future ClickHouse operations
                # (including the batch writer) silently, with nothing to log.
                # Bound both so a hang fails fast, releases the lock, and
                # resets self._client for a clean reconnect on the next call.
                send_receive_timeout=20,
                sync_request_timeout=10,
                settings={"use_numpy": False},
            )
        return self._client

    def _execute(self, query: str, params=None, data=None) -> Any:
        with self._lock:
            client = self._get_client()
            try:
                if data is not None:
                    return client.execute(query, data, types_check=False)
                return client.execute(query, params or {})
            except Exception:
                self._client = None
                raise

    async def connect(self) -> None:
        """Verify connection on startup."""
        await asyncio.to_thread(self._execute, "SELECT 1")
        log.info("ClickHouse connected: %s:%s/%s",
                 settings.clickhouse_host, settings.clickhouse_port,
                 settings.clickhouse_database)

    async def close(self) -> None:
        if self._client:
            self._client.disconnect()
            self._client = None

    # ── Write ─────────────────────────────────────────────────────────────────

    async def insert_batch(self, records: list) -> int:
        """Insert a batch of SyslogRecord objects. Returns count inserted."""
        if not records:
            return 0
        rows = [r.to_clickhouse_row() for r in records]
        query = f"INSERT INTO syslog_events ({_INSERT_COLS}) VALUES"
        await asyncio.to_thread(self._execute, query, data=rows)
        return len(rows)

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
        match_mode: str = "contains",
        limit: int = 200,
        offset: int = 0,
    ) -> dict:
        conditions: list[str] = []
        params: dict = {}

        if start:
            conditions.append("timestamp >= %(start)s")
            params["start"] = start
        if end:
            conditions.append("timestamp <= %(end)s")
            params["end"] = end
        if source_ip:
            conditions.append(_text_filter("source_ip", "source_ip", match_mode))
            params["source_ip"] = source_ip
        if dest_ip:
            conditions.append(_text_filter("dest_ip", "dest_ip", match_mode))
            params["dest_ip"] = dest_ip
        if collector_ip:
            conditions.append(_text_filter("collector_ip", "collector_ip", match_mode))
            params["collector_ip"] = collector_ip
        if collector_name:
            conditions.append(_text_filter("toString(collector_name)", "collector_name", match_mode))
            params["collector_name"] = collector_name
        if org:
            conditions.append("org = %(org)s")
            params["org"] = org
        if log_group:
            conditions.append("log_group = %(log_group)s")
            params["log_group"] = log_group
        if site:
            conditions.append("site = %(site)s")
            params["site"] = site
        if severity_max is not None:
            conditions.append("severity <= %(severity_max)s")
            params["severity_max"] = severity_max
        if facility is not None:
            conditions.append("facility = %(facility)s")
            params["facility"] = facility
        if program:
            conditions.append(_text_filter("program", "program", match_mode))
            params["program"] = program
        if search:
            conditions.append("positionCaseInsensitive(message, %(search)s) > 0")
            params["search"] = search

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        count_q = f"SELECT count() FROM syslog_events {where}"
        total = (await asyncio.to_thread(self._execute, count_q, params))[0][0]

        rows_q = f"""
            SELECT timestamp, received_at, source_ip, source_name, dest_ip,
                   facility, facility_name, severity, severity_name,
                   program, pid, message, raw,
                   collector_ip, collector_name, org, log_group, site
            FROM syslog_events
            {where}
            ORDER BY timestamp DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """
        params["limit"] = limit
        params["offset"] = offset
        rows = await asyncio.to_thread(self._execute, rows_q, params)

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "records": [_row_to_dict(r) for r in rows],
        }

    # ── Aggregations ──────────────────────────────────────────────────────────

    async def count_by_severity(self, hours: int = 24) -> list[dict]:
        q = """
            SELECT severity, severity_name, count() AS cnt
            FROM syslog_events
            WHERE timestamp >= now() - INTERVAL %(hours)s HOUR AND timestamp <= now()
            GROUP BY severity, severity_name
            ORDER BY severity ASC
        """
        rows = await asyncio.to_thread(self._execute, q, {"hours": hours})
        return [{"severity": r[0], "severity_name": r[1], "count": r[2]} for r in rows]

    async def count_by_host(self, hours: int = 24, limit: int = 20) -> list[dict]:
        q = """
            SELECT source_ip, source_name, log_group, count() AS cnt
            FROM syslog_events
            WHERE timestamp >= now() - INTERVAL %(hours)s HOUR AND timestamp <= now()
            GROUP BY source_ip, source_name, log_group
            ORDER BY cnt DESC
            LIMIT %(limit)s
        """
        rows = await asyncio.to_thread(self._execute, q, {"hours": hours, "limit": limit})
        return [{"source_ip": r[0], "source_name": r[1], "log_group": r[2], "count": r[3]} for r in rows]

    async def top_programs(self, hours: int = 24, limit: int = 20) -> list[dict]:
        q = """
            SELECT program, count() AS cnt
            FROM syslog_events
            WHERE timestamp >= now() - INTERVAL %(hours)s HOUR AND timestamp <= now()
              AND program != ''
            GROUP BY program
            ORDER BY cnt DESC
            LIMIT %(limit)s
        """
        rows = await asyncio.to_thread(self._execute, q, {"hours": hours, "limit": limit})
        return [{"program": r[0], "count": r[1]} for r in rows]

    async def timeseries(
        self,
        hours: int = 24,
        bucket_minutes: int = 5,
        log_group: Optional[str] = None,
    ) -> list[dict]:
        extra = "AND log_group = %(log_group)s" if log_group else ""
        q = f"""
            SELECT
                toStartOfInterval(timestamp, INTERVAL %(bucket)s MINUTE) AS bucket,
                count() AS cnt
            FROM syslog_events
            WHERE timestamp >= now() - INTERVAL %(hours)s HOUR AND timestamp <= now()
            {extra}
            GROUP BY bucket
            ORDER BY bucket ASC
        """
        params: dict = {"hours": hours, "bucket": bucket_minutes}
        if log_group:
            params["log_group"] = log_group
        rows = await asyncio.to_thread(self._execute, q, params)
        return [{"bucket": r[0].isoformat() + "Z", "count": r[1]} for r in rows]

    async def collector_last_seen(self) -> list[dict]:
        """Last timestamp per collector — used for data-gap alerts."""
        q = """
            SELECT collector_ip, collector_name, max(timestamp) AS last_seen
            FROM syslog_events
            GROUP BY collector_ip, collector_name
            ORDER BY last_seen DESC
        """
        rows = await asyncio.to_thread(self._execute, q)
        return [{"collector_ip": r[0], "collector_name": r[1], "last_seen": r[2].isoformat() + "Z"} for r in rows]

    # ── Alert-engine metrics ─────────────────────────────────────────────────

    def _metric_where(
        self,
        window_min: int,
        collector_ip: Optional[str],
        severity_max: Optional[int],
        program: Optional[str],
    ) -> tuple[list[str], dict]:
        where = ["timestamp >= now() - INTERVAL %(window)s MINUTE"]
        params: dict = {"window": window_min}
        if collector_ip:
            where.append("collector_ip = %(collector_ip)s")
            params["collector_ip"] = collector_ip
        if severity_max is not None:
            where.append("severity <= %(severity_max)s")
            params["severity_max"] = severity_max
        if program:
            where.append("program = %(program)s")
            params["program"] = program
        return where, params

    async def count_events_in_window(
        self,
        window_min: int,
        collector_ip: Optional[str] = None,
        severity_max: Optional[int] = None,
        program: Optional[str] = None,
    ) -> int:
        where, params = self._metric_where(window_min, collector_ip, severity_max, program)
        q = f"SELECT count() FROM syslog_events WHERE {' AND '.join(where)}"
        rows = await asyncio.to_thread(self._execute, q, params)
        return int(rows[0][0]) if rows else 0

    async def count_events_baseline(
        self,
        baseline_days: int,
        window_min: int,
        collector_ip: Optional[str] = None,
        severity_max: Optional[int] = None,
        program: Optional[str] = None,
    ) -> float:
        """Average event count per window_min-sized bucket over the past
        baseline_days days (excluding the current window itself)."""
        where = [
            "timestamp >= now() - INTERVAL %(baseline_days)s DAY",
            "timestamp < now() - INTERVAL %(window)s MINUTE",
        ]
        params: dict = {"baseline_days": baseline_days, "window": window_min}
        if collector_ip:
            where.append("collector_ip = %(collector_ip)s")
            params["collector_ip"] = collector_ip
        if severity_max is not None:
            where.append("severity <= %(severity_max)s")
            params["severity_max"] = severity_max
        if program:
            where.append("program = %(program)s")
            params["program"] = program

        q = f"""
            SELECT avg(bucket_count) FROM (
                SELECT toStartOfInterval(timestamp, INTERVAL %(window)s MINUTE) AS bucket,
                       count() AS bucket_count
                FROM syslog_events
                WHERE {' AND '.join(where)}
                GROUP BY bucket
            )
        """
        rows = await asyncio.to_thread(self._execute, q, params)
        val = rows[0][0] if rows and rows[0][0] is not None else 0.0
        return float(val)

    async def top_sources_in_window(
        self,
        window_min: int,
        collector_ip: Optional[str] = None,
        severity_max: Optional[int] = None,
        program: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict]:
        where, params = self._metric_where(window_min, collector_ip, severity_max, program)
        params["limit"] = limit
        q = f"""
            SELECT source_ip, count() AS cnt
            FROM syslog_events
            WHERE {' AND '.join(where)}
            GROUP BY source_ip
            ORDER BY cnt DESC
            LIMIT %(limit)s
        """
        rows = await asyncio.to_thread(self._execute, q, params)
        return [{"source_ip": r[0], "count": r[1]} for r in rows]

    async def top_talker_in_window(
        self,
        window_min: int,
        collector_ip: Optional[str] = None,
        severity_max: Optional[int] = None,
    ) -> tuple[Optional[str], int]:
        top = await self.top_sources_in_window(
            window_min, collector_ip=collector_ip, severity_max=severity_max, limit=1
        )
        if not top:
            return None, 0
        return top[0]["source_ip"], top[0]["count"]

    async def table_size_gb(self, table: str) -> float:
        q = """
            SELECT sum(bytes_on_disk) FROM system.parts
            WHERE database = %(db)s AND table = %(table)s AND active
        """
        rows = await asyncio.to_thread(
            self._execute, q, {"db": settings.clickhouse_database, "table": table}
        )
        val = rows[0][0] if rows and rows[0][0] is not None else 0
        return float(val) / (1024 ** 3)


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
