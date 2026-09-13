-- Fase 1c: rename `clientes`/`usuarios` (Fase 1's Spanish-named table and
-- columns, predating CLAUDE.md's English-identifiers convention) to
-- `customers`/`users`. See CLAUDE.md and docs/DATABASE.md.
--
-- This is a rename over REAL, already-live data (190 clientes / 3 usuarios
-- rows in production at the time this was written) -- not purely additive
-- like 0001/0002. A backup is taken before this runs against production,
-- the same care as the two previous schema migrations this session.
--
-- Applied to an ALREADY-INITIALIZED database by app/schema_migrate.py, same
-- as 0001/0002 -- every statement here must be safe to re-run. Postgres has
-- no `RENAME ... IF EXISTS` for tables/columns/constraints/sequences, and
-- errors outright if the source doesn't exist or the target name is already
-- taken -- so every rename below is guarded by checking the catalog first,
-- the same defensive pattern app/migrations/0002_reference_integrity.sql
-- uses for its ADD CONSTRAINT guards. Guards resolve names via
-- `to_regclass()`/`pg_attribute` (current search_path), NOT
-- information_schema filtered by a hardcoded 'public' -- same convention as
-- 0002's `'plates'::regclass`-style checks -- so this file behaves
-- correctly whichever schema it runs against (tests/test_schema_migrate.py
-- exercises it inside throwaway, non-public schemas). On a brand-new volume
-- born from the CURRENT init.sql, `customers`/`users` already exist under
-- their final names and `clientes`/`usuarios` never existed at all -- every
-- guard below is a no-op in that case (see also the guard added to
-- app/migrations/0001_pos_fields.sql's CREATE TABLE clientes/usuarios
-- statements, needed so THAT file doesn't try to (re)create the old
-- Spanish-named tables from scratch on such a volume).
--
-- Only IDENTIFIERS are renamed here -- the stored *values* inside these
-- columns (customer names, PERSONA NATURAL/CÉDULA/DUEÑO, admin/producción/
-- consulta/punto_venta) are Spanish business data and are never touched.
--
-- Besides the table and its columns, this also renames the objects Postgres
-- auto-named after the OLD table/column names when `clientes`/`usuarios`
-- were first created (the primary key, the `nombre`/`username` UNIQUE
-- constraint, and the `id` SERIAL sequence) -- renaming a table does NOT
-- rename these automatically, and leaving them as `clientes_pkey` etc. on a
-- table now called `customers` would make a migrated old database's
-- information_schema differ from a fresh install's (see
-- tests/test_schema_migrate.py::TestSchemaInvariant, which asserts the two
-- must be identical). The FK constraints pointing at `clientes(id)`/
-- `usuarios(id)` (production_orders.cliente_id/creado_por) need no action
-- here: Postgres names a FK after the REFERENCING column, not the
-- referenced table (e.g. production_orders_cliente_id_fkey), and tracks the
-- referenced table by OID internally -- it keeps working, and reporting the
-- new name, straight through the rename below.
--
-- Must produce the exact same end schema as the equivalent block in
-- init.sql -- see tests/test_schema_migrate.py, which asserts this against
-- information_schema.

-- === clientes -> customers ==================================================

DO $$
BEGIN
    IF to_regclass('clientes') IS NOT NULL AND to_regclass('customers') IS NULL THEN
        ALTER TABLE clientes RENAME TO customers;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('customers') AND attname = 'nombre' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('customers') AND attname = 'name' AND NOT attisdropped
    ) THEN
        ALTER TABLE customers RENAME COLUMN nombre TO name;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('customers') AND attname = 'tipo' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('customers') AND attname = 'type' AND NOT attisdropped
    ) THEN
        ALTER TABLE customers RENAME COLUMN tipo TO type;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('customers') AND attname = 'documento_tipo' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('customers') AND attname = 'document_type' AND NOT attisdropped
    ) THEN
        ALTER TABLE customers RENAME COLUMN documento_tipo TO document_type;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('customers') AND attname = 'documento_numero' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('customers') AND attname = 'document_number' AND NOT attisdropped
    ) THEN
        ALTER TABLE customers RENAME COLUMN documento_numero TO document_number;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('customers') AND attname = 'documento_relacion' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('customers') AND attname = 'document_relation' AND NOT attisdropped
    ) THEN
        ALTER TABLE customers RENAME COLUMN documento_relacion TO document_relation;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('customers') AND attname = 'telefono' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('customers') AND attname = 'phone' AND NOT attisdropped
    ) THEN
        ALTER TABLE customers RENAME COLUMN telefono TO phone;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('customers') AND attname = 'correo_electronico' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('customers') AND attname = 'email' AND NOT attisdropped
    ) THEN
        ALTER TABLE customers RENAME COLUMN correo_electronico TO email;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('customers') AND attname = 'licencia_transito' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('customers') AND attname = 'transit_license' AND NOT attisdropped
    ) THEN
        ALTER TABLE customers RENAME COLUMN licencia_transito TO transit_license;
    END IF;
END $$;

-- Objects Postgres auto-named after `clientes`/`nombre` when the table was
-- first created -- see the file header for why these need explicit renames.
-- Sequences are relations too, so to_regclass() resolves them the same way.

DO $$
BEGIN
    IF to_regclass('clientes_id_seq') IS NOT NULL AND to_regclass('customers_id_seq') IS NULL THEN
        ALTER SEQUENCE clientes_id_seq RENAME TO customers_id_seq;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'clientes_pkey' AND conrelid = to_regclass('customers')
    ) THEN
        ALTER TABLE customers RENAME CONSTRAINT clientes_pkey TO customers_pkey;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'clientes_nombre_key' AND conrelid = to_regclass('customers')
    ) THEN
        ALTER TABLE customers RENAME CONSTRAINT clientes_nombre_key TO customers_name_key;
    END IF;
END $$;

-- === usuarios -> users =======================================================

DO $$
BEGIN
    IF to_regclass('usuarios') IS NOT NULL AND to_regclass('users') IS NULL THEN
        ALTER TABLE usuarios RENAME TO users;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('users') AND attname = 'rol' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('users') AND attname = 'role' AND NOT attisdropped
    ) THEN
        ALTER TABLE users RENAME COLUMN rol TO role;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('users') AND attname = 'activo' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('users') AND attname = 'active' AND NOT attisdropped
    ) THEN
        ALTER TABLE users RENAME COLUMN activo TO active;
    END IF;
END $$;

-- Objects Postgres auto-named after `usuarios`/`username` when the table was
-- first created.

DO $$
BEGIN
    IF to_regclass('usuarios_id_seq') IS NOT NULL AND to_regclass('users_id_seq') IS NULL THEN
        ALTER SEQUENCE usuarios_id_seq RENAME TO users_id_seq;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'usuarios_pkey' AND conrelid = to_regclass('users')
    ) THEN
        ALTER TABLE users RENAME CONSTRAINT usuarios_pkey TO users_pkey;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'usuarios_username_key' AND conrelid = to_regclass('users')
    ) THEN
        ALTER TABLE users RENAME CONSTRAINT usuarios_username_key TO users_username_key;
    END IF;
END $$;
