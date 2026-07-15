-- pktLog migration 003 — collector registry
-- Maps collector IPs to their org/group hierarchy for log enrichment at ingest time.

CREATE TABLE IF NOT EXISTS collector_registry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    collector_ip    TEXT NOT NULL UNIQUE,   -- IP pktLog sees on the inbound TCP/UDP socket
    collector_name  TEXT NOT NULL,          -- human label, e.g. "collector-a", "collector-b"
    org             TEXT NOT NULL DEFAULT '',
    log_group       TEXT NOT NULL DEFAULT '',  -- e.g. "Collector-A", "Collector-B"
    site            TEXT NOT NULL DEFAULT '',  -- optional finer-grained site label
    notes           TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- No seed rows here — install.sh seeds exactly one "local" collector for this
-- host's own detected IP (see step 8). Add real collectors via the UI/API.
