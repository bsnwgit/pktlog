"""
Abstract storage backend interface for pktLog syslog data.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional


class StorageBackend(ABC):

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection and verify schema."""

    @abstractmethod
    async def close(self) -> None:
        """Clean shutdown."""

    @abstractmethod
    async def insert_batch(self, records: list) -> int:
        """Bulk insert a batch of SyslogRecord objects. Returns count inserted."""

    @abstractmethod
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
        """Filtered syslog search. Returns {total, limit, offset, records}.

        `match_mode` governs the free-text field filters (source_ip, dest_ip,
        collector_ip, collector_name, program) — "contains" matches the term
        anywhere in the value, "prefix" anchors it at the first character.
        Both are case-insensitive. `search` (message body) is always
        "contains" — anchoring a full-text query to the start of a syslog
        message would never match anything useful.
        """

    @abstractmethod
    async def count_by_severity(self, hours: int = 24) -> list[dict]:
        """Event counts grouped by severity for the last N hours."""

    @abstractmethod
    async def count_by_host(self, hours: int = 24, limit: int = 20) -> list[dict]:
        """Top source hosts by event count for the last N hours."""

    @abstractmethod
    async def top_programs(self, hours: int = 24, limit: int = 20) -> list[dict]:
        """Top programs by event count for the last N hours."""

    @abstractmethod
    async def timeseries(
        self,
        hours: int = 24,
        bucket_minutes: int = 5,
        log_group: Optional[str] = None,
    ) -> list[dict]:
        """Event counts bucketed by time interval for charting."""

    @abstractmethod
    async def collector_last_seen(self) -> list[dict]:
        """Last received timestamp per collector — used for data-gap alerting."""

    # ── Alert-engine metrics ─────────────────────────────────────────────────
    # Concrete (non-abstract) with safe zero/empty defaults so a backend that
    # hasn't implemented them (e.g. DuckDB) degrades gracefully instead of
    # crashing the alert engine — callers already treat 0/empty as "not
    # applicable" (see app/alerts/engine.py).

    async def count_events_in_window(
        self,
        window_min: int,
        collector_ip: Optional[str] = None,
        severity_max: Optional[int] = None,
        program: Optional[str] = None,
    ) -> int:
        """Count of syslog events in the last window_min minutes, optionally filtered."""
        return 0

    async def count_events_baseline(
        self,
        baseline_days: int,
        window_min: int,
        collector_ip: Optional[str] = None,
        severity_max: Optional[int] = None,
        program: Optional[str] = None,
    ) -> float:
        """Average event count over window_min-sized buckets across the past
        baseline_days days — used as the comparison baseline for rate-spike alerts."""
        return 0.0

    async def top_sources_in_window(
        self,
        window_min: int,
        collector_ip: Optional[str] = None,
        severity_max: Optional[int] = None,
        program: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict]:
        """Top source_ip by event count in the window."""
        return []

    async def top_talker_in_window(
        self,
        window_min: int,
        collector_ip: Optional[str] = None,
        severity_max: Optional[int] = None,
    ) -> tuple[Optional[str], int]:
        """Single top talker (source_ip, count) in the window."""
        return None, 0

    async def table_size_gb(self, table: str) -> float:
        """On-disk size of a table in GB. 0 if not applicable or not found."""
        return 0.0
