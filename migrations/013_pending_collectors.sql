-- pktLog migration 013 — pending collector approvals
-- Senders that aren't in collector_registry are dropped at ingest. This table
-- records them so an admin can see and approve them from the Approval page,
-- rather than the device being invisible unless the "new_host" alert rule
-- happens to be enabled.
--
-- A row here grants nothing: data still only persists once approval creates
-- the collector_registry entry. Rows are counters, written by a periodic
-- flush from the ingest path, never per-message.

CREATE TABLE IF NOT EXISTS pending_collectors (
    collector_ip   TEXT PRIMARY KEY,              -- IP seen on the inbound socket
    first_seen     TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen      TEXT NOT NULL DEFAULT (datetime('now')),
    message_count  INTEGER NOT NULL DEFAULT 0,    -- messages dropped since first_seen
    sample_message TEXT NOT NULL DEFAULT '',      -- most recent raw line, to identify the device
    ignored        INTEGER NOT NULL DEFAULT 0     -- hidden from the queue; still counted
);

CREATE INDEX IF NOT EXISTS idx_pending_collectors_last_seen
    ON pending_collectors(last_seen DESC);
