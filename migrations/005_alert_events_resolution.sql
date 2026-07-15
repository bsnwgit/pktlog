-- pktLog migration 005 — alert event resolution tracking
-- The original alert_events schema (001_initial.sql) only tracked
-- acked_at/acked_by. app/alerts/engine.py's auto-resolve logic
-- (_auto_resolve / _auto_resolve_data_gap) already reads and writes
-- resolved_at/auto_resolved, which never existed as columns — every
-- auto-resolve check failed with "no such column: resolved_at".

ALTER TABLE alert_events ADD COLUMN resolved_at TEXT;
ALTER TABLE alert_events ADD COLUMN auto_resolved INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_alert_events_resolved ON alert_events(resolved_at);
