-- Named connections to sibling pkt* apps pktlog could pull data from in
-- the future. No consumer feature uses this yet — this just lays down the
-- same outbound Suite Integration infrastructure pktIPAM/pktflow/pktWiFi
-- already have, so a connection can be wired up later without a schema
-- change first.
CREATE TABLE IF NOT EXISTS integrations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL UNIQUE,   -- user-given label, e.g. "Main pktIPAM"
    app_name          TEXT NOT NULL,
    base_url          TEXT NOT NULL DEFAULT '',
    suite_token       TEXT NOT NULL DEFAULT '',
    enabled           INTEGER NOT NULL DEFAULT 1,
    health_status     TEXT NOT NULL DEFAULT 'unknown',
    last_health_check TEXT,
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_integrations_app_name ON integrations(app_name);
