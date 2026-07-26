-- Per-user preference: use ipapi.is's free tier (1,000 req/day, no key
-- required) instead of a stored personal key. When set, the IP Info lookup
-- calls ipapi.is with no key param at all.
ALTER TABLE user_api_keys ADD COLUMN free_tier INTEGER NOT NULL DEFAULT 0;
