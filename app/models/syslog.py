"""
SyslogRecord — canonical in-memory representation of a parsed syslog event.
Matches the ClickHouse syslog_events table column-for-column (17 fields).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


# ── Facility / severity lookup tables ────────────────────────────────────────

FACILITY_NAMES: dict[int, str] = {
    0: "kern",   1: "user",   2: "mail",    3: "daemon",
    4: "auth",   5: "syslog", 6: "lpr",     7: "news",
    8: "uucp",   9: "cron",  10: "authpriv",11: "ftp",
    12: "ntp",  13: "audit", 14: "alert",  15: "clock",
    16: "local0",17: "local1",18: "local2", 19: "local3",
    20: "local4",21: "local5",22: "local6", 23: "local7",
}

SEVERITY_NAMES: dict[int, str] = {
    0: "emergency",
    1: "alert",
    2: "critical",
    3: "error",
    4: "warning",
    5: "notice",
    6: "info",
    7: "debug",
}


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class SyslogRecord:
    # Timing
    timestamp:      datetime                        # from syslog message header
    received_at:    datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Source device (parsed from syslog header / enriched from device registry)
    source_ip:      str = ""
    source_name:    str = ""

    # Syslog standard fields
    facility:       int = 0
    facility_name:  str = ""
    severity:       int = 6                         # default: info
    severity_name:  str = "info"

    # Process info
    program:        str = ""
    pid:            str = ""

    # Message content
    message:        str = ""
    raw:            str = ""

    # Ingest path (set at listener level, enriched from collector_registry)
    collector_ip:   str = ""
    collector_name: str = ""

    # Hierarchy (enriched from collector_registry + device_registry)
    org:            str = ""
    log_group:      str = ""
    site:           str = ""

    # ── Helpers ───────────────────────────────────────────────────────────────

    def enrich_names(self) -> None:
        """Always compute facility_name and severity_name from numeric codes."""
        self.facility_name = FACILITY_NAMES.get(self.facility, str(self.facility))
        self.severity_name = SEVERITY_NAMES.get(self.severity, str(self.severity))

    def to_clickhouse_row(self) -> tuple:
        """Return a tuple matching the INSERT column order in ClickHouseBackend."""
        return (
            self.timestamp,
            self.received_at,
            self.source_ip,
            self.source_name,
            self.facility,
            self.facility_name,
            self.severity,
            self.severity_name,
            self.program,
            self.pid,
            self.message,
            self.raw,
            self.collector_ip,
            self.collector_name,
            self.org,
            self.log_group,
            self.site,
        )
