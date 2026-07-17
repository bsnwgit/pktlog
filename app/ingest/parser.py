"""
Syslog parser — handles RFC 3164 and RFC 5424.

RFC 5424 example:
  <34>1 2026-06-29T12:00:00.000Z hostname app 1234 - - message text

RFC 3164 example:
  <34>Jun 29 12:00:00 hostname app[1234]: message text

Falls back to received_at when the parsed timestamp is implausible
(missing year, timezone issues, or >1 day in the future).

RFC 3164 timestamps carry no timezone marker; many devices (e.g. UniFi
APs/gateways) log them in local time rather than UTC, so they're interpreted
using the app's configured device timezone (Settings -> General -> Timezone)
before being converted to UTC for storage. See _device_timezone().
"""
from __future__ import annotations

import re
import logging
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from app.models.syslog import SyslogRecord

log = logging.getLogger("pktlog.ingest.parser")

# ── Device timezone (for RFC 3164 timestamps, which carry no tz marker) ───────
# Many devices (e.g. UniFi APs/gateways) log RFC 3164 timestamps in their own
# local clock, not UTC — treating "17:17:24" as UTC when the device really
# meant 17:17:24 America/New_York silently shifts every such timestamp by the
# zone offset. Reuse the app's configured display timezone (Settings ->
# General) as the assumed device timezone, cached to avoid a SQLite read on
# every single ingested message.
_TZ_CACHE_TTL = 300  # seconds
_tz_cache: dict = {"name": "UTC", "last_refresh": 0.0}


def _device_timezone() -> ZoneInfo:
    now = time.monotonic()
    if now - _tz_cache["last_refresh"] > _TZ_CACHE_TTL:
        try:
            import sqlite3
            import json
            from app.config import get_settings
            conn = sqlite3.connect(get_settings().db_path)
            row = conn.execute("SELECT value FROM settings WHERE key='timezone'").fetchone()
            conn.close()
            if row and row[0]:
                _tz_cache["name"] = json.loads(row[0])
        except Exception:
            pass
        _tz_cache["last_refresh"] = now
    try:
        return ZoneInfo(_tz_cache["name"])
    except Exception:
        return ZoneInfo("UTC")

# ── Regex patterns ────────────────────────────────────────────────────────────

# RFC 5424: <PRI>VERSION TIMESTAMP HOSTNAME APP-NAME PROCID MSGID STRUCTURED-DATA MSG
_RE_5424 = re.compile(
    r'^<(?P<pri>\d{1,3})>'
    r'(?P<ver>\d+)\s+'
    r'(?P<ts>\S+)\s+'
    r'(?P<host>\S+)\s+'
    r'(?P<app>\S+)\s+'
    r'(?P<pid>\S+)\s+'
    r'(?P<msgid>\S+)\s+'
    r'(?P<sd>\S+|\[.*?\])\s*'
    r'(?P<msg>.*)',
    re.DOTALL,
)

# RFC 3164: <PRI>TIMESTAMP HOSTNAME PROGRAM[PID]: MSG
# The PROGRAM[PID]: tag is optional here — some devices (e.g. UniFi/EdgeOS-
# style kernel netfilter LOG output) emit <PRI>TIMESTAMP HOSTNAME followed
# directly by a raw KV blob with no colon-terminated tag at all. Requiring
# the tag meant those lines never matched this regex, fell through to the
# "unparseable" branch below, and got PRI/timestamp/hostname silently
# dropped in favor of dumping the entire raw line as-is into message.
# The <PRI> header itself is also optional — some devices (e.g. UniFi CEF
# security events) emit TIMESTAMP HOSTNAME CEF:... with no PRI at all.
# The (?!CEF:) guard stops the tag group from swallowing "CEF" as if it
# were a PROGRAM[PID]: tag, which would otherwise chop the leading
# "CEF:0|..." off of message.
_RE_3164 = re.compile(
    r'^(?:<(?P<pri>\d{1,3})>)?'
    r'(?P<ts>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+'
    r'(?P<host>\S+)\s+'
    r'(?:(?!CEF:)(?P<prog_pid>\S+?)(?:\[(?P<pid>\d+)\])?:\s*)?'
    r'(?P<msg>.*)',
    re.DOTALL,
)

# Some kernel netfilter LOG lines (UniFi/EdgeOS/VyOS firewall logging, and
# plain iptables/nftables LOG target output) carry a human-readable summary
# in DESCR="..." and the actual traffic source in SRC=<ip> — buried inside
# an otherwise machine-oriented KV blob (IN=/OUT=/MAC=/PROTO=/...). Neither
# RFC 3164 nor 5424 has a concept of either field, so when present they're
# extracted directly out of the message text.
_RE_DESCR = re.compile(r'DESCR="([^"]*)"')
_RE_KV_SRC = re.compile(r'(?:^|\s)SRC=(\S+)')
_RE_KV_DST = re.compile(r'(?:^|\s)DST=(\S+)')

# Some UniFi AP kernel/wireless-driver debug lines have no syslog header at
# all (no PRI, no RFC 3164 timestamp, no hostname) — just a device-internal
# correlation tag and uptime, e.g. "{9ab2 a650} [7958296.686405] [DHCP-SM] ...".
# Neither RFC parser matches these, so they fall to the unparseable branch;
# strip this noise prefix so message isn't a byte-for-byte copy of raw.
_RE_KERNEL_DEBUG_PREFIX = re.compile(r'^\{[0-9a-fA-F]+\s+[0-9a-fA-F]+\}\s+\[\d+\.\d+\]\s*')

# RFC 3164 months
_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


# ── Public entry point ────────────────────────────────────────────────────────

def parse(raw: str, received_at: datetime, collector_ip: str = "") -> SyslogRecord:
    """
    Parse a raw syslog line into a SyslogRecord.
    collector_ip is set here for reference; enrichment happens in normalizer.
    """
    raw = raw.strip()
    record = SyslogRecord(
        timestamp=received_at,
        received_at=received_at,
        raw=raw,
        collector_ip=collector_ip,
    )

    # Try RFC 5424 first (has explicit version number after PRI)
    m = _RE_5424.match(raw)
    if m and m.group("ver") == "1":
        _parse_5424(m, record, received_at)
    else:
        # Try RFC 3164
        m = _RE_3164.match(raw)
        if m:
            _parse_3164(m, record, received_at)
        else:
            # Neither matched — store raw with received_at and log a warning
            log.debug("Unparseable syslog line from %s: %.120s", collector_ip, raw)
            record.message = raw

    _extract_netfilter_fields(record)
    _strip_kernel_debug_prefix(record)
    record.enrich_names()
    return record


# ── Format-specific parsers ───────────────────────────────────────────────────

def _parse_5424(m: re.Match, record: SyslogRecord, received_at: datetime) -> None:
    pri = int(m.group("pri"))
    record.facility = pri >> 3
    record.severity = pri & 0x07

    ts_str = m.group("ts")
    record.timestamp = _parse_ts_5424(ts_str, received_at)

    host = m.group("host")
    record.source_ip = host if host != "-" else ""

    app = m.group("app")
    record.program = app if app != "-" else ""

    pid = m.group("pid")
    record.pid = pid if pid != "-" else ""

    record.message = m.group("msg").strip()


def _extract_netfilter_fields(record: SyslogRecord) -> None:
    """
    When message looks like kernel netfilter LOG output (DESCR="..." and/or
    SRC=<ip>/DST=<ip> present among the IN=/OUT=/MAC=/PROTO=/... KV pairs),
    replace the technical KV blob with DESCR's human-readable text as
    message, prefer the embedded SRC= as source_ip over the syslog HOSTNAME
    field — for this log type the actual traffic source is far more useful
    than the reporting device's own name (which is already captured
    separately as collector_name/collector_ip) — and lift DST= into its own
    dest_ip field the same way. raw is left untouched either way. Not every
    matching line carries a DST= (e.g. some IN=/OUT=-only variants); dest_ip
    stays "" when absent rather than guessing.
    """
    text = record.message
    if "DESCR=" not in text and "SRC=" not in text:
        return

    descr = _RE_DESCR.search(text)
    if descr:
        record.message = descr.group(1).strip()

    src = _RE_KV_SRC.search(text)
    if src:
        record.source_ip = src.group(1)

    dst = _RE_KV_DST.search(text)
    if dst:
        record.dest_ip = dst.group(1)


def _strip_kernel_debug_prefix(record: SyslogRecord) -> None:
    """Strip the {tag} [uptime] noise prefix from headerless AP debug lines."""
    record.message = _RE_KERNEL_DEBUG_PREFIX.sub("", record.message, count=1)


def _parse_3164(m: re.Match, record: SyslogRecord, received_at: datetime) -> None:
    pri = m.group("pri")
    if pri is not None:
        pri = int(pri)
        record.facility = pri >> 3
        record.severity = pri & 0x07
    # else: no PRI on the wire — leave facility/severity at SyslogRecord's
    # defaults (kern/info) rather than guessing.

    record.timestamp = _parse_ts_3164(m.group("ts"), received_at)
    record.source_ip = m.group("host")

    prog_pid = m.group("prog_pid")  # None when the line has no TAG[PID]: prefix at all
    record.program = prog_pid.rstrip(":") if prog_pid else ""
    record.pid = m.group("pid") or ""
    record.message = m.group("msg").strip()


# ── Timestamp parsers ─────────────────────────────────────────────────────────

def _parse_ts_5424(ts_str: str, received_at: datetime) -> datetime:
    """Parse RFC 5424 timestamp (ISO 8601). Falls back to received_at."""
    if ts_str == "-":
        return received_at
    try:
        # Python 3.11+ handles Z suffix; earlier versions need replacement
        ts_str = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return _sanity_check(dt, received_at)
    except Exception:
        return received_at


def _parse_ts_3164(ts_str: str, received_at: datetime) -> datetime:
    """
    Parse RFC 3164 timestamp (e.g. 'Jun 29 12:00:00').
    No year, no timezone marker — assume current year and the app's
    configured device timezone (not UTC; see _device_timezone()).
    """
    try:
        parts = ts_str.split()
        if len(parts) != 3:
            return received_at
        month = _MONTHS.get(parts[0])
        if month is None:
            return received_at
        day = int(parts[1])
        h, mi, s = (int(x) for x in parts[2].split(":"))
        year = received_at.year
        tz = _device_timezone()
        naive = datetime(year, month, day, h, mi, s)
        dt = naive.replace(tzinfo=tz).astimezone(timezone.utc)
        # If the parsed date is >1 day in the future, it's last year's log
        if dt > received_at + timedelta(days=1):
            naive = naive.replace(year=year - 1)
            dt = naive.replace(tzinfo=tz).astimezone(timezone.utc)
        return _sanity_check(dt, received_at)
    except Exception:
        return received_at


def _sanity_check(dt: datetime, received_at: datetime) -> datetime:
    """Return received_at if dt is more than 7 days in the future (clock skew guard)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if dt > received_at + timedelta(days=7):
        return received_at
    return dt
