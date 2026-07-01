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
        """Filtered syslog search. Returns {total, limit, offset, records}."""

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
