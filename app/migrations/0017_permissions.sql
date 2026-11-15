-- The permission catalog moves into the database: `permissions` (code is the
-- English identifier stored in role_permissions.permission; label is the
-- Spanish text staff will see; sort_order is the display order). It mirrors
-- app/core/permissions.py's PERMISSIONS tuple, same codes, labels and order
-- (a test keeps them identical). role_permissions.permission becomes a FK to
-- permissions(code), so Postgres itself rejects an unknown code. See
-- docs/DATABASE.md.
--
-- Before the FK is added, every role_permissions row whose code isn't in the
-- catalog is deleted. Such rows grant nothing today
-- (effective_permissions intersects the stored codes with the catalog), so
-- nobody's access changes.
--
-- Safe to re-run: CREATE TABLE IF NOT EXISTS, INSERT ... ON CONFLICT DO
-- NOTHING, a DELETE that finds nothing the second time, and the FK added
-- through a guarded DO block (Postgres has no ADD CONSTRAINT IF NOT EXISTS).
-- The guard filters by conrelid, not just conname, for the reason given in
-- 0002_reference_integrity.sql. Same end schema as init.sql.

CREATE TABLE IF NOT EXISTS permissions (
    code TEXT PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL
);

INSERT INTO permissions (code, label, sort_order) VALUES
    ('orders.capture', 'Registrar y editar órdenes', 1),
    ('cash_summary.view', 'Ver el cuadre de caja', 2),
    ('declarations.review', 'Revisar declaraciones', 3),
    ('production.view', 'Ver el tablero de producción', 4),
    ('production.manage', 'Cambiar el estado de producción', 5),
    ('production.deliver', 'Marcar órdenes como entregadas', 6),
    ('records.view', 'Consultar registros', 7),
    ('records.delete', 'Eliminar órdenes', 8),
    ('duplicates.review', 'Revisar posibles duplicados', 9),
    ('inventory.manage', 'Gestionar inventario', 10),
    ('pricing.manage', 'Editar precios', 11),
    ('customers.manage', 'Gestionar clientes', 12),
    ('users.manage', 'Gestionar usuarios y roles', 13)
ON CONFLICT DO NOTHING;

DELETE FROM role_permissions WHERE permission NOT IN (SELECT code FROM permissions);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'role_permissions_permission_fkey'
          AND conrelid = to_regclass('role_permissions')
    ) THEN
        ALTER TABLE role_permissions
            ADD CONSTRAINT role_permissions_permission_fkey
            FOREIGN KEY (permission) REFERENCES permissions(code)
            ON DELETE CASCADE ON UPDATE CASCADE;
    END IF;
END $$;
