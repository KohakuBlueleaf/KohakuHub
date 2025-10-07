-- Database migration for PostgreSQL
-- Adds quota columns and SSHKey table

-- 1. Add quota columns to User table
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS private_quota_bytes BIGINT DEFAULT NULL;
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS public_quota_bytes BIGINT DEFAULT NULL;
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS private_used_bytes BIGINT DEFAULT 0;
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS public_used_bytes BIGINT DEFAULT 0;

-- 2. Add quota columns to Organization table
ALTER TABLE organization ADD COLUMN IF NOT EXISTS private_quota_bytes BIGINT DEFAULT NULL;
ALTER TABLE organization ADD COLUMN IF NOT EXISTS public_quota_bytes BIGINT DEFAULT NULL;
ALTER TABLE organization ADD COLUMN IF NOT EXISTS private_used_bytes BIGINT DEFAULT 0;
ALTER TABLE organization ADD COLUMN IF NOT EXISTS public_used_bytes BIGINT DEFAULT 0;

-- 3. Create SSHKey table
CREATE TABLE IF NOT EXISTS sshkey (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    key_type VARCHAR(255) NOT NULL,
    public_key TEXT NOT NULL,
    fingerprint VARCHAR(255) NOT NULL UNIQUE,
    title VARCHAR(255) NOT NULL,
    last_used TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 4. Create indexes
CREATE INDEX IF NOT EXISTS sshkey_user_id ON sshkey(user_id);
CREATE INDEX IF NOT EXISTS sshkey_fingerprint ON sshkey(fingerprint);
CREATE UNIQUE INDEX IF NOT EXISTS sshkey_user_fingerprint ON sshkey(user_id, fingerprint);

-- Done!
