-- Phase 2 (declarations plan): the `ownership_cards` table -- the Colombian
-- "tarjeta de propiedad", identified by its "licencia de tránsito". See
-- docs/DATABASE.md for the full column reference and the conflict statuses
-- app/core/db/ownership_cards.py::find_card_conflicts classifies.
--
-- Purely additive: a brand-new table and index, nothing existing is touched.
--
-- Applied to an ALREADY-INITIALIZED database by app/schema_migrate.py, same
-- as the earlier migrations -- every statement here must be safe to re-run
-- (CREATE ... IF NOT EXISTS). Must produce the exact same end schema as the
-- equivalent block in init.sql.
--
-- Three INDEPENDENT constraints, deliberately no composite key: the FK
-- (each card belongs to exactly one customer; one customer may own several
-- cards), UNIQUE(transit_license) and UNIQUE(plate_number). vehicle_type
-- holds a normalize.PLATE_TYPES value but is validated by callers, not by a
-- DB constraint.

CREATE TABLE IF NOT EXISTS ownership_cards (
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

CREATE INDEX IF NOT EXISTS ownership_cards_customer_id_idx ON ownership_cards (customer_id);
