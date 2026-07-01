-- pktLog migration 003 — collector registry
-- Maps collector IPs to their org/group hierarchy for log enrichment at ingest time.

CREATE TABLE IF NOT EXISTS collector_registry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    collector_ip    TEXT NOT NULL UNIQUE,   -- IP pktLog sees on the inbound TCP/UDP socket
    collector_name  TEXT NOT NULL,          -- human label, e.g. "medical", "dental"
    org             TEXT NOT NULL DEFAULT 'Vyne',
    log_group       TEXT NOT NULL DEFAULT '',  -- e.g. "Medical", "Dental"
    site            TEXT NOT NULL DEFAULT '',  -- optional finer-grained site label
    notes           TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Seed known collectors
INSERT OR IGNORE INTO collector_registry (collector_ip, collector_name, org, log_group, site) VALUES
    ('172.23.80.11', 'medical',  'Vyne', 'Medical', ''),
    ('10.56.57.181', 'dental',   'Vyne', 'Dental',  '');

-- Direct sources (O2 loopback) treated as local
INSERT OR IGNORE INTO collector_registry (collector_ip, collector_name, org, log_group, site) VALUES
    ('127.0.0.1',    'local',    'Vyne', 'Infrastructure', 'o2'),
    ('172.23.80.5',  'local',    'Vyne', 'Infrastructure', 'o2');
