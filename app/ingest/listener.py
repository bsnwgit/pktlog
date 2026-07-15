"""
Syslog listener — async UDP + TCP on the configured syslog_port (default 5514).

TCP framing: non-transparent (newline-delimited, RFC 6587 §3.4.2).
Max message size: 64 KB (handles oversized messages gracefully).

Each received message is:
  1. Parsed  (RFC 3164 / RFC 5424)
  2. Enriched (normalizer → org/group/site/names)
  3. Enqueued (batch writer → ClickHouse)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from app.ingest.parser import parse
from app.ingest.normalizer import get_normalizer
from app.ingest.writer import get_writer
from app.config import get_settings

log = logging.getLogger("pktlog.ingest.listener")

_MAX_SIZE = 65536   # 64 KB UDP datagram cap


async def _get_port() -> int:
    """Syslog port — SQLite settings value wins if present (Settings UI →
    Ingest tab), otherwise falls back to config.yaml/env (app/config.py)."""
    port = get_settings().syslog_port
    try:
        import aiosqlite, json
        async with aiosqlite.connect(get_settings().db_path) as db:
            async with db.execute(
                "SELECT value FROM settings WHERE key = 'syslog_port'"
            ) as cur:
                row = await cur.fetchone()
                if row:
                    port = int(json.loads(row[0]))
    except Exception:
        pass
    return port


# ── UDP protocol ──────────────────────────────────────────────────────────────

class _UDPProtocol(asyncio.DatagramProtocol):

    def __init__(self):
        self._transport = None

    def connection_made(self, transport):
        self._transport = transport

    def datagram_received(self, data: bytes, addr: tuple):
        raw = data.decode("utf-8", errors="replace").strip()
        if not raw:
            return
        collector_ip = addr[0]
        received_at = datetime.now(timezone.utc)
        asyncio.ensure_future(_process(raw, collector_ip, received_at))

    def error_received(self, exc):
        log.warning("UDP error: %s", exc)

    def connection_lost(self, exc):
        if exc:
            log.warning("UDP connection lost: %s", exc)


# ── TCP handler ───────────────────────────────────────────────────────────────

async def _handle_tcp(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    collector_ip = peer[0] if peer else ""
    log.debug("TCP connection from %s", collector_ip)
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            if len(line) > _MAX_SIZE:
                log.warning("Oversized message from %s (%d bytes) — truncating", collector_ip, len(line))
                line = line[:_MAX_SIZE]
            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            received_at = datetime.now(timezone.utc)
            await _process(raw, collector_ip, received_at)
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    except Exception as e:
        log.warning("TCP handler error from %s: %s", collector_ip, e)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


# ── Process pipeline ──────────────────────────────────────────────────────────

async def _process(raw: str, collector_ip: str, received_at: datetime) -> None:
    try:
        record = parse(raw, received_at, collector_ip)
        normalizer = get_normalizer()
        await normalizer.enrich(record)
        writer = get_writer()
        await writer.enqueue(record)
    except Exception as e:
        log.error("Ingest pipeline error from %s: %s", collector_ip, e)


# ── Listener lifecycle ────────────────────────────────────────────────────────

class SyslogListener:

    def __init__(self):
        self._udp_transport: Optional[asyncio.BaseTransport] = None
        self._tcp_server: Optional[asyncio.Server] = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        port = await _get_port()

        # With multiple uvicorn workers, only one process can own the port.
        # The others skip binding gracefully — the writer still starts so
        # any records enqueued locally will be flushed by this worker too.
        try:
            # UDP
            self._udp_transport, _ = await loop.create_datagram_endpoint(
                _UDPProtocol,
                local_addr=("0.0.0.0", port),
            )
            log.info("Syslog UDP listener started on port %d", port)

            # TCP
            self._tcp_server = await asyncio.start_server(
                _handle_tcp,
                host="0.0.0.0",
                port=port,
            )
            log.info("Syslog TCP listener started on port %d", port)

        except OSError as e:
            log.info(
                "Syslog port %d already bound by another worker — this worker will not listen (%s)",
                port, e,
            )

        # Always start the writer (handles ClickHouse flush for this worker)
        writer = get_writer()
        await writer.start()

    async def stop(self) -> None:
        if self._tcp_server:
            self._tcp_server.close()
            await self._tcp_server.wait_closed()
        if self._udp_transport:
            self._udp_transport.close()

        writer = get_writer()
        await writer.stop()
        log.info("Syslog listener stopped")


# ── Module-level singleton ────────────────────────────────────────────────────

_listener: Optional[SyslogListener] = None


def get_listener() -> SyslogListener:
    global _listener
    if _listener is None:
        _listener = SyslogListener()
    return _listener
