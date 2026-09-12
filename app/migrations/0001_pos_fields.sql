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

CREATE TABLE IF NOT EXISTS clientes (
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

CREATE TABLE IF NOT EXISTS usuarios (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    rol TEXT NOT NULL,
    activo BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT now()
);

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

ALTER TABLE production_orders ADD COLUMN IF NOT EXISTS comprobante_tipo TEXT;
ALTER TABLE production_orders ADD COLUMN IF NOT EXISTS comprobante_numero TEXT;
ALTER TABLE production_orders ADD COLUMN IF NOT EXISTS cliente_id INTEGER REFERENCES clientes(id);
ALTER TABLE production_orders ADD COLUMN IF NOT EXISTS forma_pago TEXT;
ALTER TABLE production_orders ADD COLUMN IF NOT EXISTS guia_numero TEXT;
ALTER TABLE production_orders ADD COLUMN IF NOT EXISTS pedido_numero TEXT;
ALTER TABLE production_orders ADD COLUMN IF NOT EXISTS fecha_entrega DATE;
ALTER TABLE production_orders ADD COLUMN IF NOT EXISTS fecha_transferencia_pago DATE;
ALTER TABLE production_orders ADD COLUMN IF NOT EXISTS responsable_despacho TEXT;
ALTER TABLE production_orders ADD COLUMN IF NOT EXISTS nombre_recibe TEXT;
ALTER TABLE production_orders ADD COLUMN IF NOT EXISTS observaciones TEXT;
ALTER TABLE production_orders ADD COLUMN IF NOT EXISTS creado_por INTEGER REFERENCES usuarios(id);

ALTER TABLE plates ADD COLUMN IF NOT EXISTS unit_price NUMERIC(12,2);
ALTER TABLE plates ADD COLUMN IF NOT EXISTS motivo TEXT;
ALTER TABLE plates ADD COLUMN IF NOT EXISTS marca_vehiculo TEXT;

CREATE INDEX IF NOT EXISTS plates_order_id_idx ON plates(order_id);
