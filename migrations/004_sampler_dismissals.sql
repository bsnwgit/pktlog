-- Sampler IPs excluded from "Collector data gap" alert evaluation.
-- Add a row to permanently silence gap alerts for a decommissioned or
-- known-silent sampler without disabling the rule globally.

CREATE TABLE IF NOT EXISTS sampler_dismissals (
    sampler_ip   TEXT PRIMARY KEY,
    dismissed_at TEXT NOT NULL DEFAULT (datetime('now')),
    dismissed_by TEXT NOT NULL DEFAULT 'system',
    reason       TEXT
);
