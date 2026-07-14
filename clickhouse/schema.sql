-- pktLog ClickHouse schema.
--
-- NOTE: this schema was not previously checked into the repo — it is
-- reconstructed here from the 17-column INSERT list in
-- app/storage/clickhouse.py and app/models/syslog.py (SyslogRecord) so that
-- a fresh bare-metal install has something to apply. If your existing
-- deployment's syslog_events table differs (extra columns, different
-- ORDER BY/partitioning/TTL), prefer that over this file and update this
-- file to match so future installs stay in sync.

CREATE DATABASE IF NOT EXISTS pktlog;

CREATE TABLE IF NOT EXISTS pktlog.syslog_events
(
    timestamp       DateTime64(3),
    received_at     DateTime64(3),

    source_ip       String,
    source_name     String,

    facility        UInt8,
    facility_name   LowCardinality(String),
    severity        UInt8,
    severity_name   LowCardinality(String),

    program         String,
    pid             String,

    message         String,
    raw             String,

    collector_ip    String,
    collector_name  LowCardinality(String),

    org             LowCardinality(String),
    log_group       LowCardinality(String),
    site            LowCardinality(String)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(timestamp)
ORDER BY (org, log_group, source_ip, timestamp)
TTL toDateTime(timestamp) + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;
