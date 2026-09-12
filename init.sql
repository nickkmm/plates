-- Schema for INGSE plate production tracking.
-- Runs automatically only on first initialization of an empty data volume.

CREATE TABLE production_orders (
    id SERIAL PRIMARY KEY,
    -- TEXT, not INTEGER: real order numbers are sometimes non-numeric (e.g. "NT 895").
    order_number TEXT NOT NULL,
    order_date DATE NOT NULL,
    requested_by TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE plates (
    id SERIAL PRIMARY KEY,
    order_id INTEGER REFERENCES production_orders(id),
    plate_type TEXT NOT NULL,
    plate_number TEXT,              -- single plate, or the START of a range
    plate_to TEXT,                  -- END of a range (NULL for single plates / reposición)
    is_batch_replacement BOOLEAN DEFAULT false,
    is_voided BOOLEAN DEFAULT false,           -- order annulled: NOT consumed
    is_plate_annulled BOOLEAN DEFAULT false,   -- plate annulled but blank lost: IS consumed, just flagged
    quantity INTEGER NOT NULL DEFAULT 1,
    material TEXT,
    prepared_by TEXT,
    is_possible_duplicate BOOLEAN DEFAULT false,
    -- City is a property of the PLATE, not the order: one order can ship plates
    -- to several cities. Types other than CARRO PARTICULAR/PÚBLICO are implicitly
    -- COLOMBIA (see core.normalize._CITY_SPECIFIC_TYPES).
    city TEXT,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE inventory_categories (
    category TEXT PRIMARY KEY,
    initial_stock INTEGER DEFAULT 0,  -- "alistado" baseline when the DB started (fixed)
    prepared INTEGER DEFAULT 0,       -- sum of ongoing blank_preparations
    consumed INTEGER DEFAULT 0        -- sum of plates made (excl. order-annulled)
    -- available (initial_stock + prepared - consumed) is computed in the view.
);

-- Blank-preparation ("alistamiento") events: blanks cut/prepared in advance,
-- and new material intake. Recorded in the ledger as ALISTAMIENTO rows. These
-- are NOT plates; they feed inventory_categories.prepared.
CREATE TABLE blank_preparations (
    id SERIAL PRIMARY KEY,
    prep_date DATE,
    category TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    prepared_by TEXT,
    created_at TIMESTAMP DEFAULT now()
);

-- POS (point-of-sale) order capture -- added alongside production tracking.
-- See docs/DATABASE.md for the full column reference and the "rarezas" this
-- introduces (requested_by stays a denormalized copy of clientes.nombre).

-- requested_by (the intermediary -- PTO.V, MUVA, etc.) and the sale "cliente"
-- are the same entity: the name already tracked in production_orders becomes
-- a real client row with new attributes. nombre is the ONLY UNIQUE constraint
-- in this schema besides primary keys -- see docs/DATABASE.md for why.
CREATE TABLE clientes (
    id SERIAL PRIMARY KEY,
    nombre TEXT NOT NULL UNIQUE,   -- canonical form: normalize.normalize_requested_by(valor)
    tipo TEXT,                     -- one of normalize.CLIENTE_TIPOS
    documento_tipo TEXT,           -- one of normalize.DOCUMENTO_TIPOS
    documento_numero TEXT,         -- TEXT always: NIT carries check digits/dashes
    documento_relacion TEXT,       -- only meaningful when documento_tipo = CÉDULA
    telefono TEXT,
    correo_electronico TEXT,
    licencia_transito TEXT,        -- only meaningful when tipo = PERSONA NATURAL
    created_at TIMESTAMP DEFAULT now()
);

-- Real RBAC, replacing the AUTH_USERS/AUTH_ROLES env vars (see docs/WEB.md).
CREATE TABLE usuarios (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,   -- same werkzeug format as AUTH_USERS today
    rol TEXT NOT NULL,             -- admin / produccion / consulta / punto_venta
    activo BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT now()
);

-- Suggested/default unit price per plate TYPE (the 7 normalize.PLATE_TYPES
-- values, NEVER the 3 inventory_categories materials -- see docs/DATABASE.md).
-- Starts empty on purpose: /precios shows one row per PLATE_TYPES entry
-- (read from the Python constant), marking the ones with no row yet as
-- "sin tarifa" instead of depending on seeded data.
CREATE TABLE plate_type_prices (
    plate_type TEXT PRIMARY KEY,
    unit_price NUMERIC(12,2) NOT NULL,
    updated_at TIMESTAMP DEFAULT now(),
    updated_by TEXT
);

-- Bookkeeping for app/schema_migrate.py (python -m app.schema_migrate), which
-- applies app/migrations/*.sql to an already-initialized database. Not used
-- by init.sql itself -- included here only so a fresh database ends up with
-- the exact same schema as an old one that had the migrations applied.
CREATE TABLE schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT now()
);

-- New production_orders columns for the full sale order (POS), all nullable
-- and additive -- no CHECK constraints, consistent with the rest of this
-- schema. cliente_id is nullable: not every historical requested_by value
-- backfills to a clean client (see app/schema_migrate.py).
ALTER TABLE production_orders ADD COLUMN comprobante_tipo TEXT;
ALTER TABLE production_orders ADD COLUMN comprobante_numero TEXT;
ALTER TABLE production_orders ADD COLUMN cliente_id INTEGER REFERENCES clientes(id);
ALTER TABLE production_orders ADD COLUMN forma_pago TEXT;
ALTER TABLE production_orders ADD COLUMN guia_numero TEXT;
ALTER TABLE production_orders ADD COLUMN pedido_numero TEXT;
ALTER TABLE production_orders ADD COLUMN fecha_entrega DATE;
ALTER TABLE production_orders ADD COLUMN fecha_transferencia_pago DATE;
ALTER TABLE production_orders ADD COLUMN responsable_despacho TEXT;
ALTER TABLE production_orders ADD COLUMN nombre_recibe TEXT;
ALTER TABLE production_orders ADD COLUMN observaciones TEXT;
ALTER TABLE production_orders ADD COLUMN creado_por INTEGER REFERENCES usuarios(id);

-- New plates columns: per-vehicle/per-line data (motivo/marca_vehiculo) and
-- the price snapshot taken at insert/edit time (see docs/DATABASE.md).
ALTER TABLE plates ADD COLUMN unit_price NUMERIC(12,2);
ALTER TABLE plates ADD COLUMN motivo TEXT;         -- DETERIORO / PÉRDIDA / OTROS
ALTER TABLE plates ADD COLUMN marca_vehiculo TEXT;

-- First index in this schema besides primary keys / the clientes.nombre
-- UNIQUE: /cuadre introduces the first real aggregate JOIN over all of
-- plates, joined on order_id.
CREATE INDEX plates_order_id_idx ON plates(order_id);
