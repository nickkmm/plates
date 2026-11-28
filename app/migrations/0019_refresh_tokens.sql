-- Refresh tokens for the JWT authentication (docs/MIGRATION_PLAN.md, "Phase
-- 2: JWT authentication"). One row per opaque refresh token, which a client
-- trades for a new short-lived access token. Only the SHA-256 hash of the
-- token is stored (token_hash), never the raw value. Each rotation revokes
-- the row it used and inserts a new one in the same family_id; presenting a
-- revoked token again (reuse) revokes the whole family. Deleting a user
-- deletes their tokens. See docs/DATABASE.md and app/auth/refresh_tokens.py.
--
-- Safe to re-run: CREATE TABLE/INDEX IF NOT EXISTS. Same end schema as
-- init.sql.

CREATE TABLE IF NOT EXISTS refresh_tokens (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    family_id UUID NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    revoked_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS refresh_tokens_user_id_idx ON refresh_tokens (user_id);
CREATE INDEX IF NOT EXISTS refresh_tokens_family_id_idx ON refresh_tokens (family_id);
