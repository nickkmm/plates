-- Multi-card declarations: a sworn declaration ("declaración juramentada") now
-- carries MANY ownership cards ("tarjetas de propiedad"), one row each in the
-- new `declaration_cards` table. Per-card data (reason, plate quantity, the
-- card's own fields, its owner, its review flags and the customer/card created
-- or linked on approval) moves out of `declarations`, which keeps only what is
-- declaration-wide: the declarant (the single person who signs, once, for every
-- card), the status/review columns and the union of the cards' flags. See
-- docs/DATABASE.md.
--
-- Applied to an ALREADY-INITIALIZED database by app/schema_migrate.py; every
-- statement is safe to re-run and the end schema must be identical to the
-- equivalent blocks in init.sql.
--
-- Data: each existing declaration (old one-card shape) becomes a declaration
-- with exactly one declaration_cards row, its signature moves to
-- declarant_signature_path (the gestor's if there was one, else the owner's)
-- and its submitted_data is rewritten to the canonical shape
--   {"acting_as", "declarant": {...}, "cards": [{..., "owner": {...}}], "raw"?}
-- The backfill only runs while the OLD columns still exist, so a re-run (or a
-- database built from the new init.sql) skips it entirely.

CREATE TABLE IF NOT EXISTS declaration_cards (
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
    UNIQUE (declaration_id, position)
);

-- UNIQUE (declaration_id, position) already indexes declaration_id lookups.
CREATE INDEX IF NOT EXISTS declaration_cards_ownership_card_id_idx ON declaration_cards (ownership_card_id);
CREATE INDEX IF NOT EXISTS declaration_cards_customer_id_idx ON declaration_cards (customer_id);

ALTER TABLE declarations ADD COLUMN IF NOT EXISTS declarant_signature_path TEXT;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'declarations'
          AND column_name = 'plate_quantity'
    ) THEN
        INSERT INTO declaration_cards (
            declaration_id, position, reason, plate_quantity, plate_number, transit_license,
            engine_number, chassis_number, vehicle_type, registered_in, vehicle_brand,
            owner_relation, owner_data, customer_id, ownership_card_id, review_flags
        )
        SELECT
            d.id, 0, d.reason, d.plate_quantity,
            COALESCE(d.submitted_data #>> '{vehicle,plate_number}', ''),
            COALESCE(d.submitted_data #>> '{vehicle,transit_license}', ''),
            NULLIF(d.submitted_data #>> '{vehicle,engine_number}', ''),
            NULLIF(d.submitted_data #>> '{vehicle,chassis_number}', ''),
            NULLIF(d.submitted_data #>> '{vehicle,vehicle_type}', ''),
            NULLIF(d.submitted_data #>> '{vehicle,registered_in}', ''),
            NULLIF(d.submitted_data #>> '{vehicle,vehicle_brand}', ''),
            CASE d.acting_as
                WHEN 'PROPIETARIO' THEN 'DUEÑO'
                WHEN 'POSEEDOR' THEN 'POSEEDOR'
                ELSE NULLIF(d.submitted_data ->> 'owner_relation', '')
            END,
            COALESCE(d.submitted_data -> 'owner', '{}'::jsonb),
            d.customer_id, d.ownership_card_id, d.review_flags
        FROM declarations d
        WHERE NOT EXISTS (SELECT 1 FROM declaration_cards c WHERE c.declaration_id = d.id);

        UPDATE declarations
        SET declarant_signature_path = COALESCE(gestor_signature_path, owner_signature_path)
        WHERE declarant_signature_path IS NULL;

        UPDATE declarations d
        SET submitted_data = jsonb_build_object(
                'acting_as', d.acting_as,
                'declarant', CASE
                    WHEN d.acting_as = 'GESTOR' AND jsonb_typeof(d.submitted_data -> 'gestor') = 'object'
                        THEN d.submitted_data -> 'gestor'
                    ELSE COALESCE(d.submitted_data -> 'owner', '{}'::jsonb)
                END,
                'cards', jsonb_build_array(
                    COALESCE(d.submitted_data -> 'vehicle', '{}'::jsonb)
                    || jsonb_build_object(
                        'reason', d.reason,
                        'plate_quantity', d.plate_quantity,
                        'owner', COALESCE(d.submitted_data -> 'owner', '{}'::jsonb),
                        'owner_relation', CASE d.acting_as
                            WHEN 'PROPIETARIO' THEN 'DUEÑO'
                            WHEN 'POSEEDOR' THEN 'POSEEDOR'
                            ELSE COALESCE(d.submitted_data ->> 'owner_relation', '')
                        END
                    )
                )
            )
            || CASE WHEN d.submitted_data ? 'raw'
                    THEN jsonb_build_object('raw', d.submitted_data -> 'raw')
                    ELSE '{}'::jsonb END;
    END IF;
END
$$;

DROP INDEX IF EXISTS declarations_customer_id_idx;
ALTER TABLE declarations DROP COLUMN IF EXISTS reason;
ALTER TABLE declarations DROP COLUMN IF EXISTS plate_quantity;
ALTER TABLE declarations DROP COLUMN IF EXISTS customer_id;
ALTER TABLE declarations DROP COLUMN IF EXISTS ownership_card_id;
ALTER TABLE declarations DROP COLUMN IF EXISTS owner_signature_path;
ALTER TABLE declarations DROP COLUMN IF EXISTS gestor_signature_path;
