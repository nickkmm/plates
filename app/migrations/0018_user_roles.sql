-- A user can hold several roles: `user_roles` (one row per user and role)
-- becomes the only place role assignments live, replacing users.role. A
-- user's permissions are the union of the role_permissions of all their
-- roles; admin in any of them still grants every code. See
-- docs/DATABASE.md.
--
-- Every existing user gets one user_roles row equal to their users.role
-- (assigned_by NULL: nobody assigned it through a screen), then users.role
-- is dropped, which also drops users_role_fkey. So every user keeps exactly
-- the access they had. user_roles.role has no ON DELETE, the same rule as
-- the old users_role_fkey: a role that any user still holds can't be
-- deleted.
--
-- Safe to re-run: CREATE TABLE/INDEX IF NOT EXISTS, and the backfill and
-- column drop run in a DO block only while users.role still exists (checked
-- in the current schema). Same end schema as init.sql.

CREATE TABLE IF NOT EXISTS user_roles (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL REFERENCES roles(name) ON UPDATE CASCADE,
    assigned_at TIMESTAMP NOT NULL DEFAULT now(),
    assigned_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    PRIMARY KEY (user_id, role)
);
CREATE INDEX IF NOT EXISTS user_roles_role_idx ON user_roles (role);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'users'
          AND column_name = 'role'
    ) THEN
        INSERT INTO user_roles (user_id, role) SELECT id, role FROM users ON CONFLICT DO NOTHING;
        ALTER TABLE users DROP COLUMN role;
    END IF;
END $$;
