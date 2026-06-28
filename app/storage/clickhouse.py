"""
ClickHouse storage backend.
Uses clickhouse-driver (sync) wrapped in asyncio.to_thread for non-blocking operation.
Thread-safe: a threading.Lock serializes all ClickHouse calls so concurrent
asyncio.to_thread() invocations never share the connection simultaneously.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, date, timezone
from typing import Optional

from clickhouse_driver import Client

from app.config import get_settings
from app.models.flow import FlowRecord, TopTalker, TimeSeriesPoint, DeviceSummary, FlowSearchResult, TopologyNode, TopologyEdge, PortStat, ProtocolStat
from app.storage.base import StorageBackend

log = logging.getLogger("pktlog.storage.clickhouse")
settings = get_settings()

# Column order must match FlowRecord.to_clickhouse_row() and schema.sql
_INSERT_COLS = """
    timestamp, sampler_ip, sampler_name, site,
    src_ip, dst_ip, src_port, dst_port, protocol,
    bytes, packets, duration_ms, tcp_flags, tos,
    input_if, output_if, next_hop, src_as, dst_as, flow_dir
"""


class ClickHouseBackend(StorageBackend):

    def __init__(self):
        self._client: Optional[Client] = None
        self._lock = threading.Lock()  # serializes concurrent asyncio.to_thread calls

    def _get_client(self) -> Client:
        if self._client is None:
            self._client = Client(
                host=settings.clickhouse_host,
                port=settings.clickhouse_port,
                database=settings.clickhouse_database,
                user=settings.clickhouse_user,
                password=settings.clickhouse_password,
                connect_timeout=10,
                settings={"use_numpy": False},
            )
        return self._client

    def _execute(self, query: str, params=None, data=None):
        """Sync ClickHouse execute — call via asyncio.to_thread.
        Thread-safe: held under self._lock so concurrent threads never share the
        connection.  Reconnects on any driver error (EOFError, PartiallyConsumedQueryError,
        AttributeError, OSError, etc.).
        """
        with self._lock:
            for attempt in range(2):
                try:
                    client = self._get_client()
                    if data is not None:
                        return client.execute(query, data)
                    return client.execute(query, params or {})
                except Exception as e:
                    log.warning(
                        f"ClickHouse query failed ({type(e).__name__}: {e}), "
                        f"reconnecting… (attempt {attempt + 1}/2)"
                    )
                    try:
                        if self._client is not None:
                            self._client.disconnect()
                    except Exception:
                        pass
                    self._client = None
                    if attempt == 1:
                        raise

    async def connect(self) -> None:
        await asyncio.to_thread(self._ensure_schema)
        log.info(f"ClickHouse connected: {settings.clickhouse_host}:{settings.clickhouse_port}/{settings.clickhouse_database}")

    def _ensure_schema(self):
        from pathlib import Path
        schema_path = Path(__file__).parent.parent.parent / "clickhouse" / "schema.sql"
        if not schema_path.exists():
            log.warning("schema.sql not found — skipping schema init")
            return
        client = self._get_client()
        # Create database if needed
        client.execute(f"CREATE DATABASE IF NOT EXISTS {settings.clickhouse_database}")
        # Execute schema statements one at a time (skip comments and empty lines)
        sql = schema_path.read_text()
        statements = [s.strip() for s in sql.split(";") if s.strip() and not s.strip().startswith("--")]
        for stmt in statements:
            try:
                client.execute(stmt)
            except Exception as e:
                # Ignore "already exists" errors
                if "already exists" not in str(e).lower():
                    log.warning(f"Schema statement warning: {e}")

    async def close(self) -> None:
        if self._client:
            self._client.disconnect()
            self._client = None

    async def insert_flows(self, flows: list[FlowRecord]) -> None:
        if not flows:
            return
        rows = [f.to_clickhouse_row() for f in flows]
        query = f"INSERT INTO {settings.clickhouse_database}.flows ({_INSERT_COLS}) VALUES"
        await asyncio.to_thread(self._execute, query, data=rows)

    async def get_device_summaries(self) -> list[DeviceSummary]:
        query = f"""
            SELECT
                sampler_ip,
                any(sampler_name)                          AS sampler_name,
                any(site)                                  AS site,
                sumIf(bytes,   timestamp >= now() - INTERVAL 1 HOUR) AS bytes_last_hour,
                sumIf(packets, timestamp >= now() - INTERVAL 1 HOUR) AS packets_last_hour,
                countIf(       timestamp >= now() - INTERVAL 1 HOUR) AS flows_last_hour,
                max(timestamp)                             AS last_seen
            FROM {settings.clickhouse_database}.flows
            WHERE timestamp >= now() - INTERVAL 24 HOUR
            GROUP BY sampler_ip
            ORDER BY sampler_name
        """
        rows = await asyncio.to_thread(self._execute, query)
        result = []
        for row in rows:
            result.append(DeviceSummary(
                sampler_ip=str(row[0]),
                sampler_name=row[1],
                site=row[2],
                bytes_last_hour=row[3],
                packets_last_hour=row[4],
                flows_last_hour=row[5],
                flows_per_sec=round(row[5] / 3600, 2),
                last_seen=row[6],
            ))
        return result

    async def get_top_talkers(
        self,
        sampler_ip: Optional[str],
        start: datetime,
        end: datetime,
        limit: int = 50,
    ) -> list[TopTalker]:
        where = "timestamp BETWEEN %(start)s AND %(end)s"
        params: dict = {"start": start, "end": end, "limit": limit}
        if sampler_ip:
            where += " AND sampler_ip = %(sampler_ip)s"
            params["sampler_ip"] = sampler_ip

        query = f"""
            SELECT src_ip, dst_ip, dst_port, protocol,
                   sum(bytes) AS bytes, sum(packets) AS packets, count() AS flow_count
            FROM {settings.clickhouse_database}.flows
            WHERE {where}
            GROUP BY src_ip, dst_ip, dst_port, protocol
            ORDER BY bytes DESC
            LIMIT %(limit)s
        """
        rows = await asyncio.to_thread(self._execute, query, params)
        return [
            TopTalker(
                src_ip=str(r[0]), dst_ip=str(r[1]),
                dst_port=r[2], protocol=r[3],
                bytes=r[4], packets=r[5], flow_count=r[6],
            )
            for r in rows
        ]

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
        conditions = ["timestamp BETWEEN %(start)s AND %(end)s"]
        params: dict = {"start": start, "end": end, "bucket": bucket_seconds}
        if sampler_ip:
            conditions.append("sampler_ip = %(sampler_ip)s")
            params["sampler_ip"] = sampler_ip
        if dst_port is not None:
            conditions.append("dst_port = %(dst_port)s")
            params["dst_port"] = dst_port
        if protocol is not None:
            conditions.append("protocol = %(protocol)s")
            params["protocol"] = protocol
        if site:
            conditions.append("site = %(site)s")
            params["site"] = site
        where = " AND ".join(conditions)

        query = f"""
            SELECT
                toStartOfInterval(timestamp, INTERVAL %(bucket)s SECOND) AS ts,
                sum(bytes)   AS bytes,
                sum(packets) AS packets,
                count()      AS flow_count
            FROM {settings.clickhouse_database}.flows
            WHERE {where}
            GROUP BY ts
            ORDER BY ts
        """
        rows = await asyncio.to_thread(self._execute, query, params)
        return [TimeSeriesPoint(timestamp=r[0], bytes=r[1], packets=r[2], flow_count=r[3]) for r in rows]

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
        conditions = []
        params: dict = {"limit": limit, "offset": offset}

        if start:
            conditions.append("timestamp >= %(start)s"); params["start"] = start
        if end:
            conditions.append("timestamp <= %(end)s"); params["end"] = end
        if src_ip:
            conditions.append("src_ip = %(src_ip)s"); params["src_ip"] = src_ip
        if dst_ip:
            conditions.append("dst_ip = %(dst_ip)s"); params["dst_ip"] = dst_ip
        if src_port is not None:
            conditions.append("src_port = %(src_port)s"); params["src_port"] = src_port
        if dst_port is not None:
            conditions.append("dst_port = %(dst_port)s"); params["dst_port"] = dst_port
        if protocol is not None:
            conditions.append("protocol = %(protocol)s"); params["protocol"] = protocol
        if sampler_ip:
            conditions.append("sampler_ip = %(sampler_ip)s"); params["sampler_ip"] = sampler_ip

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        query = f"""
            SELECT timestamp, sampler_ip, sampler_name, src_ip, dst_ip,
                   src_port, dst_port, protocol, bytes, packets, duration_ms,
                   tcp_flags, tos, input_if, output_if, next_hop, src_as, dst_as, flow_dir
            FROM {settings.clickhouse_database}.flows
            {where}
            ORDER BY timestamp DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """
        rows = await asyncio.to_thread(self._execute, query, params)
        return [
            FlowSearchResult(
                timestamp=r[0], sampler_ip=str(r[1]), sampler_name=r[2],
                src_ip=str(r[3]), dst_ip=str(r[4]),
                src_port=r[5], dst_port=r[6], protocol=r[7],
                bytes=r[8], packets=r[9], duration_ms=r[10],
                tcp_flags=r[11], tos=r[12],
                input_if=r[13], output_if=r[14],
                next_hop=str(r[15]), src_as=r[16], dst_as=r[17], flow_dir=r[18],
            )
            for r in rows
        ]

    async def get_flows_per_sec(self) -> float:
        query = f"""
            SELECT count() / 60.0
            FROM {settings.clickhouse_database}.flows
            WHERE timestamp >= now() - INTERVAL 60 SECOND
        """
        rows = await asyncio.to_thread(self._execute, query)
        return float(rows[0][0]) if rows else 0.0

    async def get_sampler_last_seen(self) -> dict[str, datetime]:
        query = f"""
            SELECT sampler_ip, max(timestamp)
            FROM {settings.clickhouse_database}.flows
            WHERE timestamp >= now() - INTERVAL 1 DAY
            GROUP BY sampler_ip
        """
        rows = await asyncio.to_thread(self._execute, query)
        return {str(r[0]): r[1] for r in rows}

    async def get_topology(
        self,
        start: datetime,
        end: datetime,
        sampler_ip: Optional[str] = None,
        min_bytes: int = 0,
        limit: int = 200,
    ) -> tuple[list[TopologyNode], list[TopologyEdge]]:
        where_parts = ["timestamp BETWEEN %(start)s AND %(end)s"]
        params: dict = {"start": start, "end": end, "limit": limit, "min_bytes": min_bytes}
        if sampler_ip:
            where_parts.append("sampler_ip = %(sampler_ip)s")
            params["sampler_ip"] = sampler_ip
        where = " AND ".join(where_parts)

        # Edges: top IP pairs by bytes (include site so non-sampler nodes get enriched)
        edge_query = f"""
            SELECT src_ip, dst_ip, sum(bytes) AS bytes, sum(packets) AS packets,
                   count() AS flows, any(protocol) AS protocol, any(dst_port) AS dst_port,
                   any(site) AS site
            FROM {settings.clickhouse_database}.flows
            WHERE {where}
            GROUP BY src_ip, dst_ip
            HAVING bytes >= %(min_bytes)s
            ORDER BY bytes DESC
            LIMIT %(limit)s
        """
        edge_rows = await asyncio.to_thread(self._execute, edge_query, params)

        # Sampler info for node enrichment
        sampler_query = f"""
            SELECT sampler_ip, any(sampler_name) AS name, any(site) AS site
            FROM {settings.clickhouse_database}.flows
            WHERE {where}
            GROUP BY sampler_ip
        """
        sampler_rows = await asyncio.to_thread(self._execute, sampler_query, params)
        sampler_map = {str(r[0]): {"name": r[1], "site": r[2]} for r in sampler_rows}

        # Build edges; track dominant site per IP from flow data
        edges: list[TopologyEdge] = []
        node_bytes: dict[str, int] = {}
        node_flows: dict[str, int] = {}
        node_site: dict[str, str] = {}   # site from actual flows (first non-empty wins)
        for r in edge_rows:
            src, dst = str(r[0]), str(r[1])
            edge_site = str(r[7]) if len(r) > 7 and r[7] else ""
            edges.append(TopologyEdge(
                source=src, target=dst,
                bytes=r[2], packets=r[3], flows=r[4],
                protocol=r[5], dst_port=r[6],
            ))
            node_bytes[src] = node_bytes.get(src, 0) + r[2]
            node_bytes[dst] = node_bytes.get(dst, 0) + r[2]
            node_flows[src] = node_flows.get(src, 0) + r[4]
            node_flows[dst] = node_flows.get(dst, 0) + r[4]
            if edge_site:
                node_site.setdefault(src, edge_site)
                node_site.setdefault(dst, edge_site)

        # Build nodes from IPs seen in edges
        all_ips = set(node_bytes.keys())
        nodes: list[TopologyNode] = []
        for ip in all_ips:
            info = sampler_map.get(ip, {})
            # Prefer sampler_map site; fall back to site inferred from edge flow data
            site = info.get("site", "") or node_site.get(ip, "")
            nodes.append(TopologyNode(
                id=ip,
                sampler_name=info.get("name", ""),
                site=site,
                bytes=node_bytes.get(ip, 0),
                flows=node_flows.get(ip, 0),
                is_sampler=ip in sampler_map,
            ))

        return nodes, edges

    async def update_retention_ttl(self, days: int) -> None:
        query = f"""
            ALTER TABLE {settings.clickhouse_database}.flows
            MODIFY TTL toDateTime(timestamp) + INTERVAL {days} DAY
        """
        await asyncio.to_thread(self._execute, query)
        log.info(f"Updated flow retention TTL to {days} days")

    async def purge_sampler(self, sampler_ip: str) -> None:
        query = f"ALTER TABLE {settings.clickhouse_database}.flows DELETE WHERE sampler_ip = %(sampler_ip)s"
        await asyncio.to_thread(self._execute, query, {"sampler_ip": sampler_ip})
        log.info(f"Purged all flows for sampler_ip={sampler_ip}")

    # Well-known port → service name (dst_port context)
    _WELL_KNOWN: dict[tuple[int, int], str] = {
        (80, 6): "HTTP", (443, 6): "HTTPS", (22, 6): "SSH", (23, 6): "Telnet",
        (25, 6): "SMTP", (53, 17): "DNS", (53, 6): "DNS/TCP", (67, 17): "DHCP",
        (68, 17): "DHCP", (110, 6): "POP3", (143, 6): "IMAP", (161, 17): "SNMP",
        (162, 17): "SNMP Trap", (179, 6): "BGP", (389, 6): "LDAP",
        (443, 17): "QUIC", (445, 6): "SMB", (514, 17): "Syslog",
        (587, 6): "SMTP TLS", (636, 6): "LDAPS", (993, 6): "IMAPS",
        (995, 6): "POP3S", (1433, 6): "MSSQL", (1521, 6): "Oracle",
        (3306, 6): "MySQL", (3389, 6): "RDP", (5432, 6): "PostgreSQL",
        (5601, 6): "Kibana", (5672, 6): "AMQP", (6379, 6): "Redis",
        (8080, 6): "HTTP-Alt", (8443, 6): "HTTPS-Alt", (9000, 6): "ClickHouse",
        (9200, 6): "Elasticsearch", (9300, 6): "Elasticsearch", (27017, 6): "MongoDB",
        (2055, 17): "NetFlow", (4739, 17): "IPFIX",
    }

    _PROTO_NAMES: dict[int, str] = {
        0:   "HOPOPT",
        1:   "ICMP",
        2:   "IGMP",
        3:   "GGP",
        4:   "IP-in-IP",
        5:   "ST",
        6:   "TCP",
        7:   "CBT",
        8:   "EGP",
        9:   "IGP",
        17:  "UDP",
        20:  "HMP",
        22:  "XNS-IDP",
        27:  "RDP",
        33:  "DCCP",
        36:  "XTP",
        37:  "DDP",
        38:  "IDPR-CMTP",
        41:  "IPv6",
        43:  "IPv6-Route",
        44:  "IPv6-Frag",
        45:  "IDRP",
        46:  "RSVP",
        47:  "GRE",
        48:  "DSR",
        50:  "ESP",
        51:  "AH",
        58:  "ICMPv6",
        59:  "IPv6-NoNxt",
        60:  "IPv6-Opts",
        88:  "EIGRP",
        89:  "OSPF",
        94:  "IPIP",
        103: "PIM",
        108: "IPComp",
        112: "VRRP",
        115: "L2TP",
        132: "SCTP",
        133: "FC",
        136: "UDPLite",
        137: "MPLS-in-IP",
        138: "manet",
        139: "HIP",
        140: "Shim6",
        141: "WESP",
        142: "ROHC",
    }

    async def get_top_ports(
        self,
        start: datetime,
        end: datetime,
        sampler_ip: Optional[str] = None,
        site: Optional[str] = None,
        limit: int = 50,
    ) -> list[PortStat]:
        conditions = ["timestamp BETWEEN %(start)s AND %(end)s"]
        params: dict = {"start": start, "end": end, "limit": limit}
        if sampler_ip:
            conditions.append("sampler_ip = %(sampler_ip)s")
            params["sampler_ip"] = sampler_ip
        if site:
            conditions.append("site = %(site)s")
            params["site"] = site
        where = " AND ".join(conditions)

        query = f"""
            SELECT
                dst_port,
                protocol,
                sum(bytes)   AS bytes,
                sum(packets) AS packets,
                count()      AS flow_count
            FROM {settings.clickhouse_database}.flows
            WHERE {where}
            GROUP BY dst_port, protocol
            ORDER BY bytes DESC
            LIMIT %(limit)s
        """
        rows = await asyncio.to_thread(self._execute, query, params)

        total_bytes = sum(r[2] for r in rows) or 1
        result = []
        for r in rows:
            port, proto, b, pkt, fc = int(r[0]), int(r[1]), int(r[2]), int(r[3]), int(r[4])
            result.append(PortStat(
                port=port,
                protocol=proto,
                proto_name=self._PROTO_NAMES.get(proto, str(proto)),
                service_name=self._WELL_KNOWN.get((port, proto), ""),
                bytes=b,
                packets=pkt,
                flow_count=fc,
                pct_bytes=round(b / total_bytes * 100, 2),
            ))
        return result

    async def get_protocol_distribution(
        self,
        start: datetime,
        end: datetime,
        sampler_ip: Optional[str] = None,
    ) -> list[ProtocolStat]:
        conditions = ["timestamp BETWEEN %(start)s AND %(end)s"]
        params: dict = {"start": start, "end": end}
        if sampler_ip:
            conditions.append("sampler_ip = %(sampler_ip)s")
            params["sampler_ip"] = sampler_ip
        where = " AND ".join(conditions)

        query = f"""
            SELECT
                protocol,
                sum(bytes)   AS bytes,
                sum(packets) AS packets,
                count()      AS flow_count
            FROM {settings.clickhouse_database}.flows
            WHERE {where}
            GROUP BY protocol
            ORDER BY bytes DESC
            LIMIT 20
        """
        rows = await asyncio.to_thread(self._execute, query, params)
        total_bytes = sum(int(r[1]) for r in rows) or 1
        result = []
        for r in rows:
            proto, b, pkt, fc = int(r[0]), int(r[1]), int(r[2]), int(r[3])
            result.append(ProtocolStat(
                protocol=proto,
                name=self._PROTO_NAMES.get(proto, f"Proto {proto}"),
                bytes=b,
                packets=pkt,
                flow_count=fc,
                pct_bytes=round(b / total_bytes * 100, 1),
            ))
        return result

    async def get_metric_in_window(
        self,
        metric: str,
        window_min: int,
        sampler_ip: Optional[str] = None,
    ) -> float:
        """Sum of metric over the last window_min minutes."""
        col = {"bytes": "sum(bytes)", "packets": "sum(packets)", "flows": "count()"}.get(metric, "sum(bytes)")
        where = "timestamp >= now() - INTERVAL %(window_min)s MINUTE"
        params: dict = {"window_min": window_min}
        if sampler_ip:
            where += " AND sampler_ip = %(sampler_ip)s"
            params["sampler_ip"] = sampler_ip
        query = f"SELECT {col} FROM {settings.clickhouse_database}.flows WHERE {where}"
        rows = await asyncio.to_thread(self._execute, query, params)
        return float(rows[0][0]) if rows and rows[0][0] is not None else 0.0

    async def get_metric_baseline(
        self,
        metric: str,
        baseline_days: int,
        window_min: int,
        sampler_ip: Optional[str] = None,
    ) -> float:
        """Average per-window value of metric over the last baseline_days days."""
        col = {"bytes": "sum(bytes)", "packets": "sum(packets)", "flows": "count()"}.get(metric, "sum(bytes)")
        where = "timestamp >= now() - INTERVAL %(baseline_days)s DAY"
        params: dict = {"baseline_days": baseline_days}
        if sampler_ip:
            where += " AND sampler_ip = %(sampler_ip)s"
            params["sampler_ip"] = sampler_ip
        num_windows = max((baseline_days * 24 * 60) / window_min, 1)
        query = f"SELECT {col} FROM {settings.clickhouse_database}.flows WHERE {where}"
        rows = await asyncio.to_thread(self._execute, query, params)
        total = float(rows[0][0]) if rows and rows[0][0] is not None else 0.0
        return total / num_windows

    async def get_port_flow_count(
        self,
        port: int,
        protocol: Optional[int],
        direction: str,
        window_min: int,
        sampler_ip: Optional[str] = None,
    ) -> int:
        """Count of flows matching port/protocol/direction in the last window_min minutes."""
        params: dict = {"port": port, "window_min": window_min}
        if direction == "dst":
            port_clause = "dst_port = %(port)s"
        elif direction == "src":
            port_clause = "src_port = %(port)s"
        else:
            port_clause = "(src_port = %(port)s OR dst_port = %(port)s)"

        conditions = [f"timestamp >= now() - INTERVAL %(window_min)s MINUTE", port_clause]
        if protocol is not None:
            conditions.append("protocol = %(protocol)s")
            params["protocol"] = protocol
        if sampler_ip:
            conditions.append("sampler_ip = %(sampler_ip)s")
            params["sampler_ip"] = sampler_ip

        where = " AND ".join(conditions)
        query = f"SELECT count() FROM {settings.clickhouse_database}.flows WHERE {where}"
        rows = await asyncio.to_thread(self._execute, query, params)
        return int(rows[0][0]) if rows and rows[0][0] is not None else 0

    async def get_daily_timeseries(
        self,
        days: int = 30,
        sampler_ip: Optional[str] = None,
    ) -> list[TimeSeriesPoint]:
        """Daily rollup from flows_daily. Filters out bad epoch-0 entries."""
        conditions = [
            "day >= today() - %(days)s",
            "day >= toDate('2020-01-01')",
        ]
        params: dict = {"days": days}
        if sampler_ip:
            conditions.append("sampler_ip = %(sampler_ip)s")
            params["sampler_ip"] = sampler_ip
        where = " AND ".join(conditions)
        query = f"""
            SELECT day, sum(bytes) AS bytes, sum(packets) AS packets, sum(flow_count) AS flow_count
            FROM {settings.clickhouse_database}.flows_daily
            WHERE {where}
            GROUP BY day
            ORDER BY day
        """
        rows = await asyncio.to_thread(self._execute, query, params)
        result = []
        for r in rows:
            d = r[0]
            # ClickHouse Date → Python date; convert to midnight UTC datetime
            if isinstance(d, date) and not isinstance(d, datetime):
                ts = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
            else:
                ts = d if getattr(d, 'tzinfo', None) else d.replace(tzinfo=timezone.utc)
            result.append(TimeSeriesPoint(timestamp=ts, bytes=int(r[1]), packets=int(r[2]), flow_count=int(r[3])))
        return result

    async def get_hourly_timeseries(
        self,
        start: datetime,
        end: datetime,
        sampler_ip: Optional[str] = None,
    ) -> list[TimeSeriesPoint]:
        """Hourly rollup from flows_hourly."""
        conditions = ["hour BETWEEN %(start)s AND %(end)s"]
        params: dict = {"start": start, "end": end}
        if sampler_ip:
            conditions.append("sampler_ip = %(sampler_ip)s")
            params["sampler_ip"] = sampler_ip
        where = " AND ".join(conditions)
        query = f"""
            SELECT hour, sum(bytes) AS bytes, sum(packets) AS packets, sum(flow_count) AS flow_count
            FROM {settings.clickhouse_database}.flows_hourly
            WHERE {where}
            GROUP BY hour
            ORDER BY hour
        """
        rows = await asyncio.to_thread(self._execute, query, params)
        return [
            TimeSeriesPoint(
                timestamp=r[0] if getattr(r[0], 'tzinfo', None) else r[0].replace(tzinfo=timezone.utc),
                bytes=int(r[1]), packets=int(r[2]), flow_count=int(r[3]),
            )
            for r in rows
        ]

    # ── Alert engine helpers ──────────────────────────────────────────────────

    async def get_top_talker_in_window(
        self,
        metric: str,
        window_min: int,
        sampler_ip: Optional[str] = None,
    ) -> tuple[str, float]:
        col = {"bytes": "sum(bytes)", "packets": "sum(packets)", "flows": "count()"}.get(metric, "sum(bytes)")
        where = "timestamp >= now() - INTERVAL %(window_min)s MINUTE"
        params: dict = {"window_min": window_min}
        if sampler_ip:
            where += " AND sampler_ip = %(sampler_ip)s"
            params["sampler_ip"] = sampler_ip
        query = (
            f"SELECT src_ip, {col} AS val FROM {settings.clickhouse_database}.flows "
            f"WHERE {where} GROUP BY src_ip ORDER BY val DESC LIMIT 1"
        )
        rows = await asyncio.to_thread(self._execute, query, params)
        if rows:
            return rows[0][0], float(rows[0][1])
        return "", 0.0

    async def get_elephant_flow_stats(
        self,
        threshold_bytes: float,
        window_min: int,
        sampler_ip: Optional[str] = None,
    ) -> tuple[int, float]:
        where = "timestamp >= now() - INTERVAL %(window_min)s MINUTE AND bytes >= %(threshold)s"
        params: dict = {"window_min": window_min, "threshold": int(threshold_bytes)}
        if sampler_ip:
            where += " AND sampler_ip = %(sampler_ip)s"
            params["sampler_ip"] = sampler_ip
        query = f"SELECT count(), max(bytes) FROM {settings.clickhouse_database}.flows WHERE {where}"
        rows = await asyncio.to_thread(self._execute, query, params)
        if rows and rows[0][0]:
            return int(rows[0][0]), float(rows[0][1])
        return 0, 0.0

    async def get_inter_site_metric(
        self,
        metric: str,
        window_min: int,
        site_a: Optional[str] = None,
        site_b: Optional[str] = None,
    ) -> float:
        col = {"bytes": "sum(bytes)", "packets": "sum(packets)", "flows": "count()"}.get(metric, "sum(bytes)")
        conditions = ["timestamp >= now() - INTERVAL %(window_min)s MINUTE"]
        params: dict = {"window_min": window_min}
        if site_a and site_b:
            conditions.append("(site = %(site_a)s OR site = %(site_b)s)")
            params["site_a"] = site_a
            params["site_b"] = site_b
        elif site_a:
            conditions.append("site = %(site_a)s")
            params["site_a"] = site_a
        elif site_b:
            conditions.append("site = %(site_b)s")
            params["site_b"] = site_b
        where = " AND ".join(conditions)
        query = f"SELECT {col} FROM {settings.clickhouse_database}.flows WHERE {where}"
        rows = await asyncio.to_thread(self._execute, query, params)
        return float(rows[0][0]) if rows and rows[0][0] is not None else 0.0

    async def get_top_connection_count(
        self,
        window_min: int,
        sampler_ip: Optional[str] = None,
    ) -> tuple[str, int]:
        where = "timestamp >= now() - INTERVAL %(window_min)s MINUTE"
        params: dict = {"window_min": window_min}
        if sampler_ip:
            where += " AND sampler_ip = %(sampler_ip)s"
            params["sampler_ip"] = sampler_ip
        query = (
            f"SELECT src_ip, count() AS c FROM {settings.clickhouse_database}.flows "
            f"WHERE {where} GROUP BY src_ip ORDER BY c DESC LIMIT 1"
        )
        rows = await asyncio.to_thread(self._execute, query, params)
        if rows:
            return rows[0][0], int(rows[0][1])
        return "", 0

    async def get_top_unique_dst_ports(
        self,
        window_min: int,
        sampler_ip: Optional[str] = None,
    ) -> tuple[str, int]:
        where = "timestamp >= now() - INTERVAL %(window_min)s MINUTE"
        params: dict = {"window_min": window_min}
        if sampler_ip:
            where += " AND sampler_ip = %(sampler_ip)s"
            params["sampler_ip"] = sampler_ip
        query = (
            f"SELECT src_ip, uniq(dst_port) AS u FROM {settings.clickhouse_database}.flows "
            f"WHERE {where} GROUP BY src_ip ORDER BY u DESC LIMIT 1"
        )
        rows = await asyncio.to_thread(self._execute, query, params)
        if rows:
            return rows[0][0], int(rows[0][1])
        return "", 0

    async def get_top_unique_dst_ips(
        self,
        window_min: int,
        sampler_ip: Optional[str] = None,
        src_subnet: Optional[str] = None,
    ) -> tuple[str, int]:
        where = "timestamp >= now() - INTERVAL %(window_min)s MINUTE"
        params: dict = {"window_min": window_min}
        if sampler_ip:
            where += " AND sampler_ip = %(sampler_ip)s"
            params["sampler_ip"] = sampler_ip
        if src_subnet:
            where += " AND isIPAddressInRange(src_ip, %(subnet)s)"
            params["subnet"] = src_subnet
        query = (
            f"SELECT src_ip, uniq(dst_ip) AS u FROM {settings.clickhouse_database}.flows "
            f"WHERE {where} GROUP BY src_ip ORDER BY u DESC LIMIT 1"
        )
        rows = await asyncio.to_thread(self._execute, query, params)
        if rows:
            return rows[0][0], int(rows[0][1])
        return "", 0

    async def get_unexpected_proto_count(
        self,
        port: int,
        expected_proto: int,
        direction: str,
        window_min: int,
        sampler_ip: Optional[str] = None,
    ) -> int:
        if direction == "dst":
            port_clause = "dst_port = %(port)s"
        elif direction == "src":
            port_clause = "src_port = %(port)s"
        else:
            port_clause = "(src_port = %(port)s OR dst_port = %(port)s)"
        conditions = [
            "timestamp >= now() - INTERVAL %(window_min)s MINUTE",
            port_clause,
            "protocol != %(expected_proto)s",
        ]
        params: dict = {"window_min": window_min, "port": port, "expected_proto": expected_proto}
        if sampler_ip:
            conditions.append("sampler_ip = %(sampler_ip)s")
            params["sampler_ip"] = sampler_ip
        where = " AND ".join(conditions)
        query = f"SELECT count() FROM {settings.clickhouse_database}.flows WHERE {where}"
        rows = await asyncio.to_thread(self._execute, query, params)
        return int(rows[0][0]) if rows and rows[0][0] is not None else 0

    async def get_inter_site_top_contributors(
        self,
        metric: str,
        window_min: int,
        site_a: Optional[str] = None,
        site_b: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict]:
        col = {"bytes": "sum(bytes)", "packets": "sum(packets)", "flows": "count()"}.get(metric, "sum(bytes)")
        conditions = ["timestamp >= now() - INTERVAL %(window_min)s MINUTE"]
        params: dict = {"window_min": window_min, "limit": limit}
        if site_a and site_b:
            conditions.append("(site = %(site_a)s OR site = %(site_b)s)")
            params["site_a"] = site_a
            params["site_b"] = site_b
        elif site_a:
            conditions.append("site = %(site_a)s")
            params["site_a"] = site_a
        elif site_b:
            conditions.append("site = %(site_b)s")
            params["site_b"] = site_b
        where = " AND ".join(conditions)
        query = (
            f"SELECT src_ip, dst_ip, site, sampler_ip, {col} AS val "
            f"FROM {settings.clickhouse_database}.flows "
            f"WHERE {where} GROUP BY src_ip, dst_ip, site, sampler_ip ORDER BY val DESC LIMIT %(limit)s"
        )
        rows = await asyncio.to_thread(self._execute, query, params)
        return [{"src_ip": r[0], "dst_ip": r[1], "site": r[2], "sampler_ip": r[3], "value": float(r[4])} for r in rows]

    async def get_elephant_flow_top(
        self,
        threshold_bytes: float,
        window_min: int,
        sampler_ip: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict]:
        _PROTO_NAMES = {1: "ICMP", 6: "TCP", 17: "UDP", 47: "GRE", 50: "ESP"}
        conditions = [
            "timestamp >= now() - INTERVAL %(window_min)s MINUTE",
            "bytes >= %(threshold)s",
        ]
        params: dict = {"window_min": window_min, "threshold": int(threshold_bytes), "limit": limit}
        if sampler_ip:
            conditions.append("sampler_ip = %(sampler_ip)s")
            params["sampler_ip"] = sampler_ip
        where = " AND ".join(conditions)
        query = (
            f"SELECT src_ip, dst_ip, bytes, protocol "
            f"FROM {settings.clickhouse_database}.flows "
            f"WHERE {where} ORDER BY bytes DESC LIMIT %(limit)s"
        )
        rows = await asyncio.to_thread(self._execute, query, params)
        return [
            {
                "src_ip": r[0], "dst_ip": r[1],
                "bytes": int(r[2]),
                "protocol": _PROTO_NAMES.get(r[3], str(r[3])),
            }
            for r in rows
        ]

    async def get_threshold_top_ips(
        self,
        metric: str,
        window_min: int,
        sampler_ip: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict]:
        col = {"bytes": "sum(bytes)", "packets": "sum(packets)", "flows": "count()"}.get(metric, "sum(bytes)")
        where = "timestamp >= now() - INTERVAL %(window_min)s MINUTE"
        params: dict = {"window_min": window_min, "limit": limit}
        if sampler_ip:
            where += " AND sampler_ip = %(sampler_ip)s"
            params["sampler_ip"] = sampler_ip
        query = (
            f"SELECT src_ip, {col} AS val FROM {settings.clickhouse_database}.flows "
            f"WHERE {where} GROUP BY src_ip ORDER BY val DESC LIMIT %(limit)s"
        )
        rows = await asyncio.to_thread(self._execute, query, params)
        return [{"src_ip": r[0], "value": float(r[1])} for r in rows]

    async def get_port_flow_top_ips(
        self,
        port: int,
        protocol: Optional[int],
        direction: str,
        window_min: int,
        sampler_ip: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict]:
        _PROTO_NAMES = {1: "ICMP", 6: "TCP", 17: "UDP", 47: "GRE", 50: "ESP"}
        conditions = ["timestamp >= now() - INTERVAL %(window_min)s MINUTE"]
        params: dict = {"window_min": window_min, "port": port, "limit": limit}
        if direction == "src":
            conditions.append("src_port = %(port)s")
        elif direction == "dst":
            conditions.append("dst_port = %(port)s")
        else:
            conditions.append("(src_port = %(port)s OR dst_port = %(port)s)")
        if protocol is not None:
            conditions.append("protocol = %(protocol)s")
            params["protocol"] = protocol
        if sampler_ip:
            conditions.append("sampler_ip = %(sampler_ip)s")
            params["sampler_ip"] = sampler_ip
        where = " AND ".join(conditions)
        query = (
            f"SELECT src_ip, dst_ip, protocol, sampler_ip, count() AS c "
            f"FROM {settings.clickhouse_database}.flows "
            f"WHERE {where} GROUP BY src_ip, dst_ip, protocol, sampler_ip "
            f"ORDER BY c DESC LIMIT %(limit)s"
        )
        rows = await asyncio.to_thread(self._execute, query, params)
        return [
            {
                "src_ip": r[0], "dst_ip": r[1],
                "protocol": _PROTO_NAMES.get(r[2], str(r[2])),
                "sampler_ip": r[3], "flow_count": int(r[4]),
            }
            for r in rows
        ]

    async def get_unexpected_proto_top_ips(
        self,
        port: int,
        expected_proto: int,
        direction: str,
        window_min: int,
        sampler_ip: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict]:
        _PROTO_NAMES = {1: "ICMP", 6: "TCP", 17: "UDP", 47: "GRE", 50: "ESP"}
        conditions = [
            "timestamp >= now() - INTERVAL %(window_min)s MINUTE",
            "protocol != %(expected_proto)s",
        ]
        params: dict = {"window_min": window_min, "port": port,
                        "expected_proto": expected_proto, "limit": limit}
        if direction == "src":
            conditions.append("src_port = %(port)s")
        elif direction == "dst":
            conditions.append("dst_port = %(port)s")
        else:
            conditions.append("(src_port = %(port)s OR dst_port = %(port)s)")
        if sampler_ip:
            conditions.append("sampler_ip = %(sampler_ip)s")
            params["sampler_ip"] = sampler_ip
        where = " AND ".join(conditions)
        query = (
            f"SELECT src_ip, dst_ip, protocol, sampler_ip, count() AS c "
            f"FROM {settings.clickhouse_database}.flows "
            f"WHERE {where} GROUP BY src_ip, dst_ip, protocol, sampler_ip "
            f"ORDER BY c DESC LIMIT %(limit)s"
        )
        rows = await asyncio.to_thread(self._execute, query, params)
        return [
            {
                "src_ip": r[0], "dst_ip": r[1],
                "protocol": _PROTO_NAMES.get(r[2], str(r[2])),
                "sampler_ip": r[3], "flow_count": int(r[4]),
            }
            for r in rows
        ]

    async def get_top_dsts_for_ip(
        self,
        src_ip: str,
        window_min: int,
        sampler_ip: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict]:
        where = "timestamp >= now() - INTERVAL %(window_min)s MINUTE AND src_ip = %(src_ip)s"
        params: dict = {"window_min": window_min, "src_ip": src_ip, "limit": limit}
        if sampler_ip:
            where += " AND sampler_ip = %(sampler_ip)s"
            params["sampler_ip"] = sampler_ip
        query = (
            f"SELECT dst_ip, count() AS c, sum(bytes) AS b "
            f"FROM {settings.clickhouse_database}.flows "
            f"WHERE {where} GROUP BY dst_ip ORDER BY c DESC LIMIT %(limit)s"
        )
        rows = await asyncio.to_thread(self._execute, query, params)
        return [{"dst_ip": r[0], "flow_count": int(r[1]), "bytes": int(r[2])} for r in rows]

    async def get_top_ports_for_ip(
        self,
        src_ip: str,
        window_min: int,
        sampler_ip: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict]:
        _PROTO_NAMES = {1: "ICMP", 6: "TCP", 17: "UDP", 47: "GRE", 50: "ESP"}
        where = "timestamp >= now() - INTERVAL %(window_min)s MINUTE AND src_ip = %(src_ip)s"
        params: dict = {"window_min": window_min, "src_ip": src_ip, "limit": limit}
        if sampler_ip:
            where += " AND sampler_ip = %(sampler_ip)s"
            params["sampler_ip"] = sampler_ip
        query = (
            f"SELECT dst_port, protocol, count() AS c "
            f"FROM {settings.clickhouse_database}.flows "
            f"WHERE {where} GROUP BY dst_port, protocol ORDER BY dst_port ASC LIMIT %(limit)s"
        )
        rows = await asyncio.to_thread(self._execute, query, params)
        return [
            {
                "dst_port": int(r[0]),
                "protocol": _PROTO_NAMES.get(r[1], str(r[1])),
                "flow_count": int(r[2]),
            }
            for r in rows
        ]

    async def get_clickhouse_table_size_gb(self, table: str = "flows") -> float:
        query = (
            "SELECT total_bytes FROM system.tables "
            "WHERE database = %(db)s AND name = %(table)s"
        )
        params = {"db": settings.clickhouse_database, "table": table}
        try:
            rows = await asyncio.to_thread(self._execute, query, params)
            if rows and rows[0][0] is not None:
                return float(rows[0][0]) / (1024 ** 3)
        except Exception as e:
            log.warning(f"get_clickhouse_table_size_gb error: {e}")
        return 0.0
