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
