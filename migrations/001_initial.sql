-- pktLog SQLite initial migration
-- Manages: users, settings, devices, alert rules, alert events, notification logs

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ─────────────────────────────────────────────────────────────────────────────
-- Settings  (key/value store for all app configuration)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,         -- JSON-encoded value
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Defaults inserted at first run by the app
-- (storage backend, ingest method, retention days, etc.)


-- ─────────────────────────────────────────────────────────────────────────────
-- Users
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL UNIQUE,
    email           TEXT NOT NULL UNIQUE,
    hashed_password TEXT,                   -- NULL for Okta-only users
    role            TEXT NOT NULL DEFAULT 'viewer'  -- admin | analyst | viewer
                        CHECK (role IN ('admin', 'analyst', 'viewer')),
    is_active       INTEGER NOT NULL DEFAULT 1,
    okta_sub        TEXT UNIQUE,            -- Okta subject claim for SSO users
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_login      TEXT,
    auth_provider   TEXT NOT NULL DEFAULT 'local'
);

-- Default admin created by install.sh (password changed on first login)
-- INSERT INTO users (username, email, hashed_password, role)
-- VALUES ('admin', 'admin@local', '<bcrypt_hash>', 'admin');


-- ─────────────────────────────────────────────────────────────────────────────
-- Device registry  (sampler IP → name/site)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS devices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ip          TEXT NOT NULL UNIQUE,     -- sampler IP address
    name        TEXT NOT NULL,            -- display name
    site        TEXT NOT NULL DEFAULT '', -- site/location label
    notes       TEXT NOT NULL DEFAULT '',
    allowed     INTEGER NOT NULL DEFAULT 1,  -- 0 = blocked (data rejected)
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Pre-populate with known samplers from audit
INSERT OR IGNORE INTO devices (ip, name, site) VALUES
    ('192.168.44.7',  'Site-B-fw1',  'Site-B'),
    ('192.168.44.8',  'Site-B-fw2',  'Site-B'),
    ('172.27.28.88',  'Site-A-sw1',      'Site-A'),
    ('172.27.28.89',  'Site-A-fw1',      'Site-A'),
    ('10.19.56.186',  'AWS-az2a',     'aws'),
    ('10.19.81.236',  'AWS-az2b',     'aws');


-- ─────────────────────────────────────────────────────────────────────────────
-- Alert rules
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alert_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    rule_type       TEXT NOT NULL,
    conditions      TEXT NOT NULL DEFAULT '{}',  -- JSON: type-specific params
    time_window_min INTEGER NOT NULL DEFAULT 5,
    severity        TEXT NOT NULL DEFAULT 'warning'
                        CHECK (severity IN ('info','warning','critical')),
    channels        TEXT NOT NULL DEFAULT '["inapp"]',  -- JSON array of channel names
    cooldown_min    INTEGER NOT NULL DEFAULT 30,
    created_by      INTEGER REFERENCES users(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_fired      TEXT
);

-- Built-in: alert when unknown sampler sends data
INSERT OR IGNORE INTO alert_rules (id, name, description, rule_type, conditions, severity, channels)
VALUES (1,
    'Unknown sampler detected',
    'Fires when a host not in the device registry sends data',
    'new_host',
    '{}',
    'warning',
    '["inapp"]'
);

-- Built-in: alert when a known sampler goes silent
INSERT OR IGNORE INTO alert_rules (id, name, description, rule_type, conditions, severity, channels)
VALUES (2,
    'Collector data gap',
    'Fires when no data are received from a known sampler for 10 minutes',
    'data_gap',
    '{"silence_minutes": 10}',
    'critical',
    '["inapp"]'
);


-- ─────────────────────────────────────────────────────────────────────────────
-- Alert events  (fired instances of alert rules)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alert_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id     INTEGER NOT NULL REFERENCES alert_rules(id),
    severity    TEXT NOT NULL,
    message     TEXT NOT NULL,
    details     TEXT NOT NULL DEFAULT '{}',  -- JSON: extra context
    fired_at    TEXT NOT NULL DEFAULT (datetime('now')),
    acked_at    TEXT,
    acked_by    INTEGER REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_alert_events_fired    ON alert_events(fired_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_events_acked    ON alert_events(acked_at);
CREATE INDEX IF NOT EXISTS idx_alert_events_rule     ON alert_events(rule_id);


-- ─────────────────────────────────────────────────────────────────────────────
-- Notification delivery log
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notification_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER NOT NULL REFERENCES alert_events(id),
    channel     TEXT NOT NULL,    -- email | slack | pagerduty | webhook | inapp
    status      TEXT NOT NULL,    -- sent | failed | skipped
    error       TEXT,
    sent_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_notif_log_event ON notification_log(event_id);
