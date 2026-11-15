-- Roles and their permissions move into the database: `roles` (name is the
-- English identifier stored in users.role and never shown; label is the
-- Spanish display name) and `role_permissions` (one row per granted code from
-- app/core/permissions.py's catalog, validated in Python, no CHECK -- same
-- pattern as normalize.PLATE_TYPES). users.role becomes a FK to roles(name).
-- See docs/DATABASE.md. Nothing reads these tables yet: access is still
-- decided by app/web/auth.py's hard-coded role sets.
--
-- The seed reproduces today's access exactly, plus the production codes. The
-- admin rows are informational: app/core/permissions.py::effective_permissions
-- always grants admin every code.
--
-- Before the FK is added, every users.role value missing from `roles` is
-- inserted as a non-system role with label = name and no permissions, so this
-- migration can never fail on unexpected production data.
--
-- Safe to re-run: CREATE TABLE IF NOT EXISTS, INSERT ... ON CONFLICT DO
-- NOTHING, and the FK added through a guarded DO block (Postgres has no ADD
-- CONSTRAINT IF NOT EXISTS). The guard filters by conrelid, not just conname,
-- for the reason given in 0002_reference_integrity.sql. Same end schema as
-- init.sql.

CREATE TABLE IF NOT EXISTS roles (
    name TEXT PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    is_system BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role TEXT NOT NULL REFERENCES roles(name) ON DELETE CASCADE ON UPDATE CASCADE,
    permission TEXT NOT NULL,
    PRIMARY KEY (role, permission)
);

INSERT INTO roles (name, label, is_system) VALUES
    ('admin', 'Administrador', true),
    ('point_of_sale', 'Punto de venta', true),
    ('production', 'Producción', true)
ON CONFLICT DO NOTHING;

INSERT INTO role_permissions (role, permission) VALUES
    ('admin', 'orders.capture'),
    ('admin', 'cash_summary.view'),
    ('admin', 'declarations.review'),
    ('admin', 'production.view'),
    ('admin', 'production.manage'),
    ('admin', 'production.deliver'),
    ('admin', 'records.view'),
    ('admin', 'records.delete'),
    ('admin', 'duplicates.review'),
    ('admin', 'inventory.manage'),
    ('admin', 'pricing.manage'),
    ('admin', 'customers.manage'),
    ('admin', 'users.manage'),
    ('point_of_sale', 'orders.capture'),
    ('point_of_sale', 'cash_summary.view'),
    ('point_of_sale', 'declarations.review'),
    ('point_of_sale', 'records.view'),
    ('point_of_sale', 'production.view'),
    ('point_of_sale', 'production.deliver'),
    ('production', 'records.view'),
    ('production', 'production.view'),
    ('production', 'production.manage'),
    ('production', 'production.deliver')
ON CONFLICT DO NOTHING;

INSERT INTO roles (name, label, is_system)
SELECT DISTINCT role, role, false FROM users
WHERE role NOT IN (SELECT name FROM roles)
ON CONFLICT DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'users_role_fkey' AND conrelid = to_regclass('users')
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT users_role_fkey
            FOREIGN KEY (role) REFERENCES roles(name) ON UPDATE CASCADE;
    END IF;
END $$;
