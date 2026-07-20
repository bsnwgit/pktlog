-- Marks exactly one user as the account auto-logged-in when all auth methods are disabled.
ALTER TABLE users ADD COLUMN is_default_admin INTEGER NOT NULL DEFAULT 0;
