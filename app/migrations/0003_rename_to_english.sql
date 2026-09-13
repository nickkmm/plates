-- Fase 1c: rename `clientes`/`usuarios` (Fase 1's Spanish-named table and
-- columns, predating CLAUDE.md's English-identifiers convention) to
-- `customers`/`users` -- AND (corrected scope, verified against real
-- production with `\d production_orders`/`\d plates`: Fase 1, already
-- merged as PR #14, ALSO added a full set of Spanish-named POS columns on
-- `production_orders`/`plates`, live in production, not pending columns of
-- some unpushed Fase 2 as an earlier version of the plan mistakenly
-- assumed) the `production_orders`/`plates` columns those two tables
-- already have. See CLAUDE.md and docs/DATABASE.md for the full rename
-- map.
--
-- This is a rename over REAL, already-live data (190 clientes / 3 usuarios
-- rows, 1954 production_orders rows in production at the time this was
-- written) -- not purely additive like 0001/0002. A backup is taken before
-- this runs against production, the same care as the two previous schema
-- migrations this session.
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
-- born from the CURRENT init.sql, `customers`/`users` and the English
-- column names already exist under their final names and none of the old
-- Spanish identifiers ever existed at all -- every guard below is a no-op
-- in that case (see also the guard added to
-- app/migrations/0001_pos_fields.sql's CREATE TABLE clientes/usuarios
-- statements, needed so THAT file doesn't try to (re)create the old
-- Spanish-named tables from scratch on such a volume).
--
-- Only IDENTIFIERS are renamed here -- the stored *values* inside these
-- columns (customer names, PERSONA NATURAL/CÉDULA/DUEÑO, admin/producción/
-- consulta/punto_venta/EFECTIVO/etc.) are Spanish business data and are
-- never touched.
--
-- Besides the table and its columns, this also renames the objects Postgres
-- auto-named after the OLD table/column names when `clientes`/`usuarios`
-- were first created (the primary key, the `nombre`/`username` UNIQUE
-- constraint, and the `id` SERIAL sequence) -- renaming a table does NOT
-- rename these automatically, and leaving them as `clientes_pkey` etc. on a
-- table now called `customers` would make a migrated old database's
-- information_schema differ from a fresh install's (see
-- tests/test_schema_migrate.py::TestSchemaInvariant, which asserts the two
-- must be identical). Same reasoning applies to the two existing FK
-- constraints on production_orders (`production_orders_cliente_id_fkey` ->
-- `clientes(id)`, `production_orders_creado_por_fkey` -> `usuarios(id)`):
-- Postgres names a FK after the REFERENCING column, not the referenced
-- table, and tracks the referenced table by OID internally, so they would
-- keep working exactly as-is even if left unrenamed -- but their names
-- embed the OLD column names (`cliente_id`/`creado_por`), which would
-- likewise make a migrated database's information_schema differ from a
-- fresh install's (whose `customer_id`/`created_by` columns get freshly
-- auto-named FKs) -- so they're renamed below too, for that reason, not
-- because it's functionally required for the FK to keep working.
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

-- === production_orders: Spanish POS columns -> English =====================
-- production_orders always exists (core table, never conditionally
-- created) -- to_regclass('production_orders') is never NULL here.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'comprobante_tipo' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'receipt_type' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders RENAME COLUMN comprobante_tipo TO receipt_type;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'comprobante_numero' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'receipt_number' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders RENAME COLUMN comprobante_numero TO receipt_number;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'cliente_id' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'customer_id' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders RENAME COLUMN cliente_id TO customer_id;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'forma_pago' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'payment_method' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders RENAME COLUMN forma_pago TO payment_method;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'guia_numero' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'tracking_number' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders RENAME COLUMN guia_numero TO tracking_number;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'pedido_numero' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'client_order_number' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders RENAME COLUMN pedido_numero TO client_order_number;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'fecha_entrega' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'delivery_date' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders RENAME COLUMN fecha_entrega TO delivery_date;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'fecha_transferencia_pago' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'payment_date' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders RENAME COLUMN fecha_transferencia_pago TO payment_date;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'responsable_despacho' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'dispatched_by' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders RENAME COLUMN responsable_despacho TO dispatched_by;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'nombre_recibe' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'received_by' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders RENAME COLUMN nombre_recibe TO received_by;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'observaciones' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'notes' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders RENAME COLUMN observaciones TO notes;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'creado_por' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'created_by' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders RENAME COLUMN creado_por TO created_by;
    END IF;
END $$;

-- The two existing FKs, renamed for information_schema parity with a fresh
-- install -- see the file header for why this isn't functionally required
-- (Postgres tracks FKs by OID, not by name).

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'production_orders_cliente_id_fkey' AND conrelid = to_regclass('production_orders')
    ) THEN
        ALTER TABLE production_orders
            RENAME CONSTRAINT production_orders_cliente_id_fkey TO production_orders_customer_id_fkey;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'production_orders_creado_por_fkey' AND conrelid = to_regclass('production_orders')
    ) THEN
        ALTER TABLE production_orders
            RENAME CONSTRAINT production_orders_creado_por_fkey TO production_orders_created_by_fkey;
    END IF;
END $$;

-- === plates: Spanish per-vehicle columns -> English =========================
-- plates always exists (core table, never conditionally created) --
-- to_regclass('plates') is never NULL here.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('plates') AND attname = 'motivo' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('plates') AND attname = 'reason' AND NOT attisdropped
    ) THEN
        ALTER TABLE plates RENAME COLUMN motivo TO reason;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('plates') AND attname = 'marca_vehiculo' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('plates') AND attname = 'vehicle_brand' AND NOT attisdropped
    ) THEN
        ALTER TABLE plates RENAME COLUMN marca_vehiculo TO vehicle_brand;
    END IF;
END $$;
