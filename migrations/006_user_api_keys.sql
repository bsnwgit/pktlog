-- Per-user external API keys (e.g. AbuseIPDB for the IP reputation lookup).
-- Keyed by username rather than user id: suite-proxy (pktHub) requests share
-- a single pseudo user id of 0 across every hub-authenticated identity, so
-- id would not actually be per-user for that auth path — username is unique
-- and populated consistently by both auth paths.
CREATE TABLE IF NOT EXISTS user_api_keys (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL,
    provider    TEXT NOT NULL,
    api_key     TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (username, provider)
);
