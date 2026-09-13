-- Idempotent version of the POS-fields schema change added to init.sql.
-- Applied to an ALREADY-INITIALIZED database (real data, existing volume) by
-- app/schema_migrate.py -- init.sql only ever runs once, on an empty Postgres
-- volume, so it cannot pick up new columns/tables on its own. Every statement
-- here must be safe to re-run: CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT
-- EXISTS everywhere, no data changes (the clientes/usuarios backfill is
-- separate Python logic in app/schema_migrate.py, not plain SQL, since it
-- needs app.core.normalize.normalize_requested_by).
--
-- Must produce the exact same end schema as the equivalent block in init.sql
-- -- see tests/test_schema_migrate.py, which asserts this against
-- information_schema.
--
-- Fase 1c NOTE: the two CREATE TABLE statements below still literally create
-- `clientes`/`usuarios` (this file's original, already-applied-in-production
-- content is left alone -- app/migrations/0003_rename_to_english.sql handles
-- the actual production rename) -- but each is now guarded to also treat
-- `customers`/`users` already existing as "nothing to do". Without that,
-- this file would try to (re)create a spurious, empty `clientes`/`usuarios`
-- table on a volume built from the CURRENT init.sql, which creates
-- `customers`/`users` directly and never has `clientes`/`usuarios` at all.
-- Resolved via to_regclass() (current search_path), NOT information_schema
-- filtered by a hardcoded 'public' -- same convention as
-- app/migrations/0003_rename_to_english.sql and 0002's `'plates'::regclass`-
-- style checks, so this behaves correctly whichever schema it runs against
-- (tests/test_schema_migrate.py exercises it inside throwaway, non-public
-- schemas).

DO $$
BEGIN
    IF to_regclass('clientes') IS NULL AND to_regclass('customers') IS NULL THEN
        CREATE TABLE clientes (
            id SERIAL PRIMARY KEY,
            nombre TEXT NOT NULL UNIQUE,
            tipo TEXT,
            documento_tipo TEXT,
            documento_numero TEXT,
            documento_relacion TEXT,
            telefono TEXT,
            correo_electronico TEXT,
            licencia_transito TEXT,
            created_at TIMESTAMP DEFAULT now()
        );
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('usuarios') IS NULL AND to_regclass('users') IS NULL THEN
        CREATE TABLE usuarios (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            rol TEXT NOT NULL,
            activo BOOLEAN DEFAULT true,
            created_at TIMESTAMP DEFAULT now()
        );
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS plate_type_prices (
    plate_type TEXT PRIMARY KEY,
    unit_price NUMERIC(12,2) NOT NULL,
    updated_at TIMESTAMP DEFAULT now(),
    updated_by TEXT
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT now()
);

-- Fase 1c NOTE: production_orders/plates below still gain columns under
-- their ORIGINAL Fase 1 Spanish names (this file's already-applied-in-
-- production content is left alone -- app/migrations/0003_rename_to_english.sql
-- handles the actual production rename) -- but each ADD COLUMN is now also
-- guarded against the FINAL English name already existing, same reasoning
-- as the CREATE TABLE guards above: a volume built from the CURRENT
-- init.sql already has these columns under their English names from birth,
-- and a plain `ADD COLUMN IF NOT EXISTS <old_spanish_name>` would otherwise
-- either error outright (the two FK columns below, since `clientes`/
-- `usuarios` don't exist on such a volume) or silently add a spurious
-- duplicate Spanish-named column alongside the real English one.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'comprobante_tipo' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'receipt_type' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders ADD COLUMN comprobante_tipo TEXT;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'comprobante_numero' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'receipt_number' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders ADD COLUMN comprobante_numero TEXT;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'cliente_id' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'customer_id' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders ADD COLUMN cliente_id INTEGER REFERENCES clientes(id);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'forma_pago' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'payment_method' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders ADD COLUMN forma_pago TEXT;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'guia_numero' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'tracking_number' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders ADD COLUMN guia_numero TEXT;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'pedido_numero' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'client_order_number' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders ADD COLUMN pedido_numero TEXT;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'fecha_entrega' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'delivery_date' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders ADD COLUMN fecha_entrega DATE;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'fecha_transferencia_pago' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'payment_date' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders ADD COLUMN fecha_transferencia_pago DATE;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'responsable_despacho' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'dispatched_by' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders ADD COLUMN responsable_despacho TEXT;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'nombre_recibe' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'received_by' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders ADD COLUMN nombre_recibe TEXT;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'observaciones' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'notes' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders ADD COLUMN observaciones TEXT;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'creado_por' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('production_orders') AND attname = 'created_by' AND NOT attisdropped
    ) THEN
        ALTER TABLE production_orders ADD COLUMN creado_por INTEGER REFERENCES usuarios(id);
    END IF;
END $$;

ALTER TABLE plates ADD COLUMN IF NOT EXISTS unit_price NUMERIC(12,2);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('plates') AND attname = 'motivo' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('plates') AND attname = 'reason' AND NOT attisdropped
    ) THEN
        ALTER TABLE plates ADD COLUMN motivo TEXT;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('plates') AND attname = 'marca_vehiculo' AND NOT attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('plates') AND attname = 'vehicle_brand' AND NOT attisdropped
    ) THEN
        ALTER TABLE plates ADD COLUMN marca_vehiculo TEXT;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS plates_order_id_idx ON plates(order_id);
