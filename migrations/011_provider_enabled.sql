-- Per-provider "show this provider's section in the IP Lookup modal at all"
-- preference — independent of enabled_fields (which controls which fields
-- WITHIN an already-shown section render) and independent of api_key/free_tier
-- (whether the provider is configured). Default 1 preserves existing behavior:
-- every provider's section shows unless the user explicitly disables it.
ALTER TABLE user_api_keys ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1;

