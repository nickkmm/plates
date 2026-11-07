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

-- Fase 1b: the 7 granular plate TYPES (normalize.PLATE_TYPES), now a real
-- lookup table instead of a text convention -- see docs/DATABASE.md rarezas.
-- Defined here, before `plates`, so plates.plate_type can reference it
-- directly.
CREATE TABLE plate_types (
    plate_type TEXT PRIMARY KEY
);

INSERT INTO plate_types (plate_type) VALUES
    ('MOTO PARTICULAR'),
    ('CARRO PARTICULAR'),
    ('MOTO CARRO PARTICULAR'),
    ('MOTO CARRO PÚBLICO'),
    ('CARRO PÚBLICO'),
    ('REMOLQUE'),
    ('ORNAMENTAL');

CREATE TABLE plates (
    id SERIAL PRIMARY KEY,
    order_id INTEGER REFERENCES production_orders(id),
    plate_type TEXT NOT NULL REFERENCES plate_types(plate_type),
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

-- Seeded with the 3 fixed materials (normalize.INVENTORY_CATEGORIES) from
-- birth -- Fase 1b: blank_preparations.category below is now a real FK to
-- this table, so the row must already exist the first time ALISTAMIENTO is
-- ever recorded, before db.recalc_inventory() gets a chance to create it.
INSERT INTO inventory_categories (category) VALUES
    ('CARRO PARTICULAR'),
    ('MOTO PARTICULAR'),
    ('PÚBLICO');

-- Blank-preparation ("alistamiento") events: blanks cut/prepared in advance,
-- and new material intake. Recorded in the ledger as ALISTAMIENTO rows. These
-- are NOT plates; they feed inventory_categories.prepared.
CREATE TABLE blank_preparations (
    id SERIAL PRIMARY KEY,
    prep_date DATE,
    category TEXT NOT NULL REFERENCES inventory_categories(category),
    quantity INTEGER NOT NULL DEFAULT 0,
    prepared_by TEXT,
    created_at TIMESTAMP DEFAULT now()
);

-- POS (point-of-sale) order capture -- added alongside production tracking.
-- See docs/DATABASE.md for the full column reference and the "rarezas" this
-- introduces (requested_by stays a denormalized copy of customers.name).

-- requested_by (the intermediary -- PTO.V, MUVA, etc.) and the sale "customer"
-- are the same entity: the name already tracked in production_orders becomes
-- a real client row with new attributes. name is the ONLY UNIQUE constraint
-- in this schema besides primary keys -- see docs/DATABASE.md for why.
-- Table/columns named in English from birth -- Fase 1 originally shipped
-- this as `clientes`/`nombre`/`tipo`/etc.; Fase 1c renamed it in production
-- (see app/migrations/0003_rename_to_english.sql).
CREATE TABLE customers (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,     -- canonical form: normalize.normalize_requested_by(valor)
    type TEXT,                     -- one of normalize.CUSTOMER_TYPES (TRÁNSITO/EMPRESA/GESTOR/CLIENTE)
    document_type TEXT,            -- one of normalize.DOCUMENT_TYPES
    document_number TEXT,          -- TEXT always: NIT carries check digits/dashes
    document_relation TEXT,        -- only meaningful when type = CLIENTE
    phone TEXT,
    email TEXT,
    address TEXT,                  -- only meaningful when type = GESTOR or CLIENTE
    neighborhood TEXT,             -- only meaningful when type = GESTOR or CLIENTE
    municipality TEXT,             -- only meaningful when type = GESTOR or CLIENTE
    created_at TIMESTAMP DEFAULT now()
);

-- Real RBAC, replacing the AUTH_USERS/AUTH_ROLES env vars (see docs/WEB.md).
-- Table/columns named in English from birth -- Fase 1 originally shipped
-- this as `usuarios`/`rol`/`activo`; Fase 1c renamed it in production (see
-- app/migrations/0003_rename_to_english.sql).
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,   -- same werkzeug format as AUTH_USERS today
    role TEXT NOT NULL,            -- admin / production / point_of_sale
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT now()
);

-- Colombian "tarjeta de propiedad", identified by its "licencia de tránsito"
-- (see app/migrations/0006_ownership_cards.sql and docs/DATABASE.md). Three
-- independent constraints, no composite key: the FK (one customer may own
-- several cards), UNIQUE(transit_license) and UNIQUE(plate_number).
CREATE TABLE ownership_cards (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    transit_license TEXT NOT NULL UNIQUE,   -- identifier: stripped + uppercased
    plate_number TEXT NOT NULL UNIQUE,      -- normalize.clean_plate form
    engine_number TEXT,
    chassis_number TEXT,
    vehicle_type TEXT,                      -- one of normalize.PLATE_TYPES (validated in code)
    registered_in TEXT,                     -- "matriculado en"
    vehicle_brand TEXT,
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX ownership_cards_customer_id_idx ON ownership_cards (customer_id);

-- Sworn declarations submitted through the public form (see
-- app/migrations/0007_declarations.sql, 0009_declaration_cards.sql, 0010 and
-- docs/DATABASE.md). ONE declarant (who signs once) and many ownership cards
-- (declaration_cards below). While pending_review, gestor_id and the cards'
-- customer_id / ownership_card_id are NULL and submitted_data is the only copy.
CREATE TABLE declarations (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMP DEFAULT now(),
    acting_as TEXT NOT NULL,                -- normalize.ACTING_AS (PROPIETARIO / POSEEDOR / GESTOR)
    gestor_id INTEGER REFERENCES customers(id),              -- the declarant when acting_as is GESTOR; NULL otherwise or while pending
    status TEXT NOT NULL,                   -- pending_review / approved / rejected
    review_flags TEXT[] NOT NULL DEFAULT '{}',   -- union of the cards' flags
    reviewed_by INTEGER REFERENCES users(id),
    reviewed_at TIMESTAMP,
    review_note TEXT,
    submitted_data JSONB NOT NULL,          -- exact copy of what was declared (legal evidence)
    notarized_pdf_path TEXT,
    submitter_ip TEXT,
    user_agent TEXT,
    accepted_terms_at TIMESTAMP,
    declarant_signature_path TEXT,
    production_order_id INTEGER REFERENCES production_orders(id) ON DELETE SET NULL,  -- the order that attended it (0010); NULL = not attended yet
    notarized_received_at TIMESTAMP,        -- 0011: staff got the notarized copy back (PÉRDIDA declarations); NULL = not yet
    notarized_received_by INTEGER REFERENCES users(id),
    declarant_id_front_path TEXT,           -- 0012: cédula photos of the declarant (stored names); NULL on older declarations
    declarant_id_back_path TEXT,
    accepted_data_policy_at TIMESTAMP,      -- 0013: when the declarant accepted the personal-data authorization (Ley 1581); NULL on older declarations
    accepts_marketing BOOLEAN               -- 0013: TRUE = SÍ, FALSE = NO to commercial information; NULL on older declarations
);
CREATE INDEX declarations_production_order_id_idx ON declarations (production_order_id);
CREATE INDEX declarations_status_idx ON declarations (status);
CREATE INDEX declarations_submitter_ip_created_at_idx ON declarations (submitter_ip, created_at);

-- One row per ownership card of a declaration (see docs/DATABASE.md).
CREATE TABLE declaration_cards (
    id SERIAL PRIMARY KEY,
    declaration_id INTEGER NOT NULL REFERENCES declarations(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,              -- 0-based order inside the declaration
    reason TEXT NOT NULL,                   -- normalize.DECLARATION_REASONS (DETERIORO / PÉRDIDA)
    plate_quantity INTEGER NOT NULL,        -- 1..normalize.max_plates_per_card(vehicle_type)
    plate_number TEXT NOT NULL,
    transit_license TEXT NOT NULL,
    engine_number TEXT,
    chassis_number TEXT,
    vehicle_type TEXT,                      -- one of normalize.PLATE_TYPES
    registered_in TEXT,
    vehicle_brand TEXT,
    owner_relation TEXT,                    -- DUEÑO / POSEEDOR (normalize.DOCUMENT_RELATIONS)
    owner_data JSONB NOT NULL,              -- the card owner's person data (a copy of the declarant when not a gestor)
    customer_id INTEGER REFERENCES customers(id),            -- the card's owner (CLIENTE); NULL while pending
    ownership_card_id INTEGER REFERENCES ownership_cards(id),   -- NULL while pending
    review_flags TEXT[] NOT NULL DEFAULT '{}',
    card_front_path TEXT,                   -- 0012: photos of the ownership card (stored names); NULL on older declarations
    card_back_path TEXT,
    UNIQUE (declaration_id, position)
);
-- UNIQUE (declaration_id, position) already indexes declaration_id lookups.
CREATE INDEX declaration_cards_ownership_card_id_idx ON declaration_cards (ownership_card_id);
CREATE INDEX declaration_cards_customer_id_idx ON declaration_cards (customer_id);

-- Suggested/default unit price per plate TYPE (the 7 normalize.PLATE_TYPES
-- values, NEVER the 3 inventory_categories materials -- see docs/DATABASE.md).
-- Starts empty on purpose: /precios shows one row per PLATE_TYPES entry
-- (read from the Python constant), marking the ones with no row yet as
-- "sin tarifa" instead of depending on seeded data.
CREATE TABLE plate_type_prices (
    plate_type TEXT PRIMARY KEY REFERENCES plate_types(plate_type),
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
-- schema. customer_id is nullable: not every historical requested_by value
-- backfills to a clean client (see app/schema_migrate.py). Named in English
-- from birth -- Fase 1 originally shipped these as `comprobante_tipo`,
-- `comprobante_numero`, `cliente_id`, `forma_pago`, `guia_numero`,
-- `pedido_numero`, `fecha_entrega`, `fecha_transferencia_pago`,
-- `responsable_despacho`, `nombre_recibe`, `observaciones`, `creado_por`;
-- Fase 1c renamed them in production (see
-- app/migrations/0003_rename_to_english.sql).
ALTER TABLE production_orders ADD COLUMN receipt_type TEXT;
ALTER TABLE production_orders ADD COLUMN receipt_number TEXT;
ALTER TABLE production_orders ADD COLUMN customer_id INTEGER REFERENCES customers(id);
ALTER TABLE production_orders ADD COLUMN payment_method TEXT;
ALTER TABLE production_orders ADD COLUMN tracking_number TEXT;
ALTER TABLE production_orders ADD COLUMN client_order_number TEXT;
ALTER TABLE production_orders ADD COLUMN delivery_date DATE;
ALTER TABLE production_orders ADD COLUMN payment_date DATE;
ALTER TABLE production_orders ADD COLUMN dispatched_by TEXT;
ALTER TABLE production_orders ADD COLUMN received_by TEXT;
ALTER TABLE production_orders ADD COLUMN notes TEXT;
ALTER TABLE production_orders ADD COLUMN created_by INTEGER REFERENCES users(id);

-- New plates columns: per-vehicle/per-line data (reason/vehicle_brand) and
-- the price snapshot taken at insert/edit time (see docs/DATABASE.md).
-- reason/vehicle_brand named in English from birth -- Fase 1 originally
-- shipped these as `motivo`/`marca_vehiculo`; Fase 1c renamed them in
-- production (see app/migrations/0003_rename_to_english.sql).
ALTER TABLE plates ADD COLUMN unit_price NUMERIC(12,2);
ALTER TABLE plates ADD COLUMN reason TEXT;         -- DETERIORO / PÉRDIDA / OTROS
ALTER TABLE plates ADD COLUMN vehicle_brand TEXT;

-- Formal link fabricated plate -> its ownership card (see docs/DATABASE.md and
-- app/migrations/0008_plates_ownership_card_id.sql). Added here, after
-- ownership_cards exists, because `plates` is created before it. NULL for
-- ranges, voided/annulled/replacement plates, EMPRESA/TRÁNSITO orders and
-- every plate without a matching card.
ALTER TABLE plates ADD COLUMN ownership_card_id INTEGER REFERENCES ownership_cards(id);

-- First index in this schema besides primary keys / the customers.name
-- UNIQUE: /cuadre introduces the first real aggregate JOIN over all of
-- plates, joined on order_id.
CREATE INDEX plates_order_id_idx ON plates(order_id);
CREATE INDEX plates_ownership_card_id_idx ON plates(ownership_card_id);
