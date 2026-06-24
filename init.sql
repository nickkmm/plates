-- Schema for INGSE plate production tracking.
-- Runs automatically only on first initialization of an empty data volume.

CREATE TABLE production_orders (
    id SERIAL PRIMARY KEY,
    order_number INTEGER NOT NULL,
    order_date DATE NOT NULL,
    requested_by TEXT NOT NULL,
    city TEXT,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE plates (
    id SERIAL PRIMARY KEY,
    order_id INTEGER REFERENCES production_orders(id),
    plate_type TEXT NOT NULL,
    plate_number TEXT,
    is_batch_replacement BOOLEAN DEFAULT false,
    is_voided BOOLEAN DEFAULT false,
    quantity INTEGER NOT NULL DEFAULT 1,
    material TEXT,
    prepared_by TEXT,
    is_possible_duplicate BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE inventory_categories (
    category TEXT PRIMARY KEY,
    initial_stock INTEGER DEFAULT 0,
    prepared INTEGER DEFAULT 0,
    consumed INTEGER DEFAULT 0
);
