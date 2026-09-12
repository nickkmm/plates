-- Fase 1b: real referential integrity for the two relationships that, since
-- Fase 1, existed only "by text convention" (matching values, no FK) --
-- plates.plate_type / plate_type_prices.plate_type against the constant
-- normalize.PLATE_TYPES, and blank_preparations.category against
-- inventory_categories.category. See docs/DATABASE.md for the full
-- before/after.
--
-- Confirmed against production before writing this: 0 rows of
-- plates.plate_type outside the 7 valid PLATE_TYPES, 0 rows of
-- blank_preparations.category outside the 3 valid inventory_categories --
-- this is a purely additive migration, no orphaned data to reconcile first.
--
-- Applied to an ALREADY-INITIALIZED database by app/schema_migrate.py, same
-- as app/migrations/0001_pos_fields.sql -- every statement here must be safe
-- to re-run:
--   * CREATE TABLE IF NOT EXISTS for the new table.
--   * INSERT ... ON CONFLICT DO NOTHING for seed data (never overwrites an
--     existing inventory_categories row's real prepared/consumed counters).
--   * Each FK is added through a guarded DO block, since Postgres has no
--     `ADD CONSTRAINT IF NOT EXISTS`.
--
-- Order matters: plate_types is created and seeded, and inventory_categories
-- is seeded, BEFORE the FKs that reference them are added below -- otherwise
-- adding a FK against an empty/incomplete reference table would either fail
-- outright (existing rows with no match) or -- for inventory_categories on a
-- brand-new volume -- leave the very first ALISTAMIENTO insert with no row
-- to reference yet (db.recalc_inventory() only creates it afterwards, too
-- late for a non-deferred FK check on the INSERT that comes first).
--
-- Must produce the exact same end schema as the equivalent block in init.sql
-- -- see tests/test_schema_migrate.py, which asserts this against
-- information_schema.

CREATE TABLE IF NOT EXISTS plate_types (
    plate_type TEXT PRIMARY KEY
);

-- Seeded from normalize.PLATE_TYPES (7 values, read directly from the
-- constant when this file was written -- see tests/test_schema_migrate.py
-- for a guard that the DB content stays in sync with it).
INSERT INTO plate_types (plate_type) VALUES
    ('MOTO PARTICULAR'),
    ('CARRO PARTICULAR'),
    ('MOTO CARRO PARTICULAR'),
    ('MOTO CARRO PÚBLICO'),
    ('CARRO PÚBLICO'),
    ('REMOLQUE'),
    ('ORNAMENTAL')
ON CONFLICT (plate_type) DO NOTHING;

-- inventory_categories already exists (Fase 1 baseline) and, in production,
-- already holds these 3 rows (populated over time by db.recalc_inventory()).
-- Seeded here too, idempotently, so a database that somehow reached this
-- migration without ever having recalculated inventory (e.g. a brand-new
-- volume) still has the 3 rows in place before the blank_preparations FK
-- below is added -- ON CONFLICT DO NOTHING so this never touches the real
-- prepared/consumed counters already accumulated elsewhere.
INSERT INTO inventory_categories (category) VALUES
    ('CARRO PARTICULAR'),
    ('MOTO PARTICULAR'),
    ('PÚBLICO')
ON CONFLICT (category) DO NOTHING;

-- Each guard below filters pg_constraint by conrelid = '<table>'::regclass,
-- NOT just conname -- conname is only unique per-table, not database-wide,
-- and ::regclass itself resolves against the current search_path. Checking
-- conname alone would false-positive "already exists" against a same-named
-- constraint on an unrelated table in a different schema (this bit the
-- schema-invariant test in tests/test_schema_migrate.py, which builds a
-- fresh-init.sql schema and an old-schema-plus-migrations schema side by
-- side in the same database).

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'plates_plate_type_fkey' AND conrelid = 'plates'::regclass
    ) THEN
        ALTER TABLE plates
            ADD CONSTRAINT plates_plate_type_fkey
            FOREIGN KEY (plate_type) REFERENCES plate_types(plate_type);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'plate_type_prices_plate_type_fkey' AND conrelid = 'plate_type_prices'::regclass
    ) THEN
        ALTER TABLE plate_type_prices
            ADD CONSTRAINT plate_type_prices_plate_type_fkey
            FOREIGN KEY (plate_type) REFERENCES plate_types(plate_type);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'blank_preparations_category_fkey' AND conrelid = 'blank_preparations'::regclass
    ) THEN
        ALTER TABLE blank_preparations
            ADD CONSTRAINT blank_preparations_category_fkey
            FOREIGN KEY (category) REFERENCES inventory_categories(category);
    END IF;
END $$;
