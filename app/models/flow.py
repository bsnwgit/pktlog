"""
Internal normalized flow record model.
All ingest paths (HTTP POST / UDP) normalize to FlowRecord before storage.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class FlowRecord(BaseModel):
    """Single normalized NetFlow record."""

    # Time
    timestamp: datetime = Field(description="Flow end time")

    # Sampler identity (enriched from device registry)
    sampler_ip: str = Field(description="IP address of the NetFlow exporter")
    sampler_name: str = Field(default="", description="Human name (from device registry)")
    site: str = Field(default="", description="Site label (from device registry)")

    # Layer 3 / Layer 4
    src_ip: str = Field(default="0.0.0.0")
    dst_ip: str = Field(default="0.0.0.0")
    src_port: int = Field(default=0, ge=0, le=65535)
    dst_port: int = Field(default=0, ge=0, le=65535)
    protocol: int = Field(default=0, ge=0, le=255, description="IP protocol number")

    # Counters
    bytes: int = Field(default=0, ge=0)
    packets: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)

    # TCP / QoS
    tcp_flags: int = Field(default=0, ge=0, le=255)
    tos: int = Field(default=0, ge=0, le=255)

    # Routing
    input_if: int = Field(default=0, ge=0)
    output_if: int = Field(default=0, ge=0)
    next_hop: str = Field(default="0.0.0.0")
    src_as: int = Field(default=0, ge=0)
    dst_as: int = Field(default=0, ge=0)

    # Direction (0=ingress, 1=egress, 2=unknown)
    flow_dir: int = Field(default=2, ge=0, le=2)

    @field_validator("src_ip", "dst_ip", "next_hop", mode="before")
    @classmethod
    def coerce_ip(cls, v):
        if v is None:
            return "0.0.0.0"
        return str(v)

    def to_clickhouse_row(self) -> tuple:
        """Returns a tuple in ClickHouse insert column order."""
        return (
            self.timestamp,
            self.sampler_ip,
            self.sampler_name,
            self.site,
            self.src_ip,
            self.dst_ip,
            self.src_port,
            self.dst_port,
            self.protocol,
            self.bytes,
            self.packets,
            self.duration_ms,
            self.tcp_flags,
            self.tos,
            self.input_if,
            self.output_if,
            self.next_hop,
            self.src_as,
            self.dst_as,
            self.flow_dir,
        )


# ── Response models (API output shapes) ──────────────────────────────────────

class TopTalker(BaseModel):
    src_ip: str
    dst_ip: str
    dst_port: int
    protocol: int
    bytes: int
    packets: int
    flow_count: int


class TimeSeriesPoint(BaseModel):
    timestamp: datetime
    bytes: int
    packets: int
    flow_count: int


class DeviceSummary(BaseModel):
    sampler_ip: str
    sampler_name: str
    site: str
    bytes_last_hour: int
    packets_last_hour: int
    flows_last_hour: int
    flows_per_sec: float
    last_seen: Optional[datetime]


class FlowSearchResult(BaseModel):
    """Full flow record returned by the Flow Explorer — all NetFlow fields."""
    timestamp: datetime
    sampler_ip: str
    sampler_name: str
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    protocol: int
    bytes: int
    packets: int
    duration_ms: int
    # Extended fields (shown in detail panel)
    tcp_flags: int = 0
    tos: int = 0
    input_if: int = 0
    output_if: int = 0
    next_hop: str = "0.0.0.0"
    src_as: int = 0
    dst_as: int = 0
    flow_dir: int = 2


class ProtocolStat(BaseModel):
    protocol: int
    name: str
    bytes: int
    packets: int
    flow_count: int
    pct_bytes: float


class TopologyNode(BaseModel):
    id: str                        # IP address
    sampler_name: str = ""
    site: str = ""
    bytes: int = 0
    flows: int = 0
    is_sampler: bool = False       # True if this IP is a known NetFlow exporter


class TopologyEdge(BaseModel):
    source: str                    # src_ip
    target: str                    # dst_ip
    bytes: int = 0
    packets: int = 0
    flows: int = 0
    protocol: int = 0
    dst_port: int = 0


class PortStat(BaseModel):
    port: int
    protocol: int
    proto_name: str
    service_name: str              # well-known service name or ""
    bytes: int
    packets: int
    flow_count: int
    pct_bytes: float
