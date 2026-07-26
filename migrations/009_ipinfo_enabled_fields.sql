-- Per-user display preference: which ipinfo.io response sections (geolocation,
-- asn, company, privacy, abuse, domains) render in the IP Lookup modal. JSON
-- array of field keys; NULL means "not customized" — treat as all enabled.
ALTER TABLE user_api_keys ADD COLUMN enabled_fields TEXT DEFAULT NULL;
