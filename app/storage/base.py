"""
Abstract storage backend interface.
Both ClickHouse and DuckDB backends implement this.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from app.models.flow import FlowRecord, TopTalker, TimeSeriesPoint, DeviceSummary, FlowSearchResult, TopologyNode, TopologyEdge, PortStat


class StorageBackend(ABC):

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection and ensure schema exists."""

    @abstractmethod
    async def close(self) -> None:
        """Clean shutdown."""

    @abstractmethod
    async def insert_flows(self, flows: list[FlowRecord]) -> None:
        """Bulk insert a batch of flow records."""

    @abstractmethod
    async def get_device_summaries(self) -> list[DeviceSummary]:
        """Current status for all known samplers (Dashboard cards)."""

    @abstractmethod
    async def get_top_talkers(
        self,
        sampler_ip: Optional[str],
        start: datetime,
        end: datetime,
        limit: int = 50,
    ) -> list[TopTalker]:
        """Top src/dst pairs by byte volume in the given window."""

    @abstractmethod
    async def get_time_series(
        self,
        sampler_ip: Optional[str],
        start: datetime,
        end: datetime,
        bucket_seconds: int = 60,
        dst_port: Optional[int] = None,
        protocol: Optional[int] = None,
        site: Optional[str] = None,
    ) -> list[TimeSeriesPoint]:
        """Traffic volume bucketed by time for charting."""

    @abstractmethod
    async def search_flows(
        self,
        src_ip: Optional[str] = None,
        dst_ip: Optional[str] = None,
        src_port: Optional[int] = None,
        dst_port: Optional[int] = None,
        protocol: Optional[int] = None,
        sampler_ip: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[FlowSearchResult]:
        """Filtered flow search for the Flow Explorer."""

    @abstractmethod
    async def get_flows_per_sec(self) -> float:
        """Current sustained ingest rate (last 60 seconds)."""

    @abstractmethod
    async def get_sampler_last_seen(self) -> dict[str, datetime]:
        """Dict of sampler_ip → latest timestamp (for data-gap alerting)."""

    @abstractmethod
    async def update_retention_ttl(self, days: int) -> None:
        """Adjust the TTL on the raw flows table."""

    @abstractmethod
    async def get_topology(
        self,
        start: datetime,
        end: datetime,
        sampler_ip: Optional[str] = None,
        min_bytes: int = 0,
        limit: int = 200,
    ) -> tuple[list[TopologyNode], list[TopologyEdge]]:
        """Return (nodes, edges) for the network topology graph."""

    @abstractmethod
    async def purge_sampler(self, sampler_ip: str) -> None:
        """Delete all flow records for a given sampler IP (used to clean stale dashboard cards)."""

    @abstractmethod
    async def get_top_ports(
        self,
        start: datetime,
        end: datetime,
        sampler_ip: Optional[str] = None,
        site: Optional[str] = None,
        limit: int = 50,
    ) -> list[PortStat]:
        """Top destination ports ranked by byte volume."""

    @abstractmethod
    async def get_metric_in_window(
        self,
        metric: str,
        window_min: int,
        sampler_ip: Optional[str] = None,
    ) -> float:
        """Sum of metric ('bytes', 'packets', or 'flows') over the last window_min minutes."""

    @abstractmethod
    async def get_metric_baseline(
        self,
        metric: str,
        baseline_days: int,
        window_min: int,
        sampler_ip: Optional[str] = None,
    ) -> float:
        """Average per-window-min value of metric over the last baseline_days days."""

    @abstractmethod
    async def get_port_flow_count(
        self,
        port: int,
        protocol: Optional[int],
        direction: str,
        window_min: int,
        sampler_ip: Optional[str] = None,
    ) -> int:
        """Count of flows matching port/protocol/direction in the last window_min minutes.
        direction: 'src', 'dst', or 'any'."""

    async def get_daily_timeseries(
        self,
        days: int = 30,
        sampler_ip: Optional[str] = None,
    ) -> list[TimeSeriesPoint]:
        """Daily rollup: bytes/packets/flows per day from flows_daily.
        Default implementation returns empty list (ClickHouse-only feature)."""
        return []

    async def get_hourly_timeseries(
        self,
        start: datetime,
        end: datetime,
        sampler_ip: Optional[str] = None,
    ) -> list[TimeSeriesPoint]:
        """Hourly rollup: bytes/packets/flows per hour from flows_hourly.
        Default implementation returns empty list (ClickHouse-only feature)."""
        return []

    @abstractmethod
    async def get_top_talker_in_window(
        self,
        metric: str,
        window_min: int,
        sampler_ip: Optional[str] = None,
    ) -> tuple[str, float]:
        """Return (src_ip, value) for the single highest-traffic IP in the window.
        metric: 'bytes', 'packets', or 'flows'. Returns ('', 0.0) when no data."""

    @abstractmethod
    async def get_elephant_flow_stats(
        self,
        threshold_bytes: float,
        window_min: int,
        sampler_ip: Optional[str] = None,
    ) -> tuple[int, float]:
        """Return (count, max_bytes) for flows exceeding threshold_bytes in the window.
        Returns (0, 0.0) when none found."""

    @abstractmethod
    async def get_inter_site_metric(
        self,
        metric: str,
        window_min: int,
        site_a: Optional[str] = None,
        site_b: Optional[str] = None,
    ) -> float:
        """Sum of metric for flows involving site_a and/or site_b in window_min minutes.
        If both sites specified, matches flows where site is either. If neither, matches all."""

    @abstractmethod
    async def get_top_connection_count(
        self,
        window_min: int,
        sampler_ip: Optional[str] = None,
    ) -> tuple[str, int]:
        """Return (src_ip, flow_count) for the src_ip with most connections in the window.
        Returns ('', 0) when no data."""

    @abstractmethod
    async def get_top_unique_dst_ports(
        self,
        window_min: int,
        sampler_ip: Optional[str] = None,
    ) -> tuple[str, int]:
        """Return (src_ip, distinct_port_count) for the src_ip hitting the most distinct
        destination ports in the window. Returns ('', 0) when no data."""

    @abstractmethod
    async def get_top_unique_dst_ips(
        self,
        window_min: int,
        sampler_ip: Optional[str] = None,
        src_subnet: Optional[str] = None,
    ) -> tuple[str, int]:
        """Return (src_ip, distinct_dst_count) for the src_ip reaching the most distinct
        destination IPs in the window. Optionally filter src_ip by CIDR subnet.
        Returns ('', 0) when no data."""

    @abstractmethod
    async def get_unexpected_proto_count(
        self,
        port: int,
        expected_proto: int,
        direction: str,
        window_min: int,
        sampler_ip: Optional[str] = None,
    ) -> int:
        """Count of flows on the given port using a protocol other than expected_proto.
        direction: 'src', 'dst', or 'any'. expected_proto is an integer (6=TCP, 17=UDP, 1=ICMP)."""

    async def get_clickhouse_table_size_gb(self, table: str = "flows") -> float:
        """Return compressed size of the named ClickHouse table in GB.
        Default implementation returns 0.0 (ClickHouse-only feature)."""
        return 0.0

    @abstractmethod
    async def get_inter_site_top_contributors(
        self,
        metric: str,
        window_min: int,
        site_a: Optional[str] = None,
        site_b: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict]:
        """Return top (src_ip, dst_ip, site, value) contributor dicts driving inter-site traffic.
        Sorted by value descending. Returns [] when no data."""

    @abstractmethod
    async def get_elephant_flow_top(
        self,
        threshold_bytes: float,
        window_min: int,
        sampler_ip: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict]:
        """Return top oversized flow dicts: {src_ip, dst_ip, bytes, protocol}.
        Only flows with bytes >= threshold_bytes, sorted descending. Returns [] when none."""

    @abstractmethod
    async def get_threshold_top_ips(
        self,
        metric: str,
        window_min: int,
        sampler_ip: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict]:
        """Return top {src_ip, value} dicts contributing to a threshold metric.
        Sorted by value descending. Returns [] when no data."""

    @abstractmethod
    async def get_port_flow_top_ips(
        self,
        port: int,
        protocol: Optional[int],
        direction: str,
        window_min: int,
        sampler_ip: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict]:
        """Return top {src_ip, dst_ip, flow_count, sampler_ip} dicts for flows on the given port.
        direction: 'src', 'dst', or 'any'. protocol=None means any protocol.
        Sorted by flow_count descending. Returns [] when no data."""

    @abstractmethod
    async def get_unexpected_proto_top_ips(
        self,
        port: int,
        expected_proto: int,
        direction: str,
        window_min: int,
        sampler_ip: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict]:
        """Return top {src_ip, dst_ip, protocol_name, flow_count, sampler_ip} dicts for flows
        on the given port using a protocol other than expected_proto.
        Sorted by flow_count descending. Returns [] when no data."""

    @abstractmethod
    async def get_top_dsts_for_ip(
        self,
        src_ip: str,
        window_min: int,
        sampler_ip: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict]:
        """Return top {dst_ip, flow_count, bytes} dicts for destinations contacted by src_ip.
        Sorted by flow_count descending. Returns [] when no data."""

    @abstractmethod
    async def get_top_ports_for_ip(
        self,
        src_ip: str,
        window_min: int,
        sampler_ip: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict]:
        """Return top {dst_port, protocol_name, flow_count} dicts for dst ports scanned by src_ip.
        Sorted by dst_port ascending (scan enumeration order). Returns [] when no data."""
