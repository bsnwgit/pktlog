CREATE TABLE IF NOT EXISTS app_logs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    level    TEXT    NOT NULL,
    level_no INTEGER NOT NULL,
    logger   TEXT    NOT NULL,
    message  TEXT    NOT NULL,
    exc_info TEXT    NULL
);
CREATE INDEX IF NOT EXISTS idx_app_logs_ts       ON app_logs (ts DESC);
CREATE INDEX IF NOT EXISTS idx_app_logs_level_no ON app_logs (level_no);
CREATE INDEX IF NOT EXISTS idx_app_logs_logger   ON app_logs (logger);
