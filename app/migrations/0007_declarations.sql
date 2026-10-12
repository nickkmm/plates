-- Phase 3 (declarations plan): the `declarations` table -- one row per sworn
-- declaration ("declaración juramentada") submitted through the public form.
-- See docs/DATABASE.md for the column reference, the statuses and the review
-- flags app/core/declarations.py computes.
--
-- Purely additive: a brand-new table and indexes, nothing existing is touched.
-- Applied to an ALREADY-INITIALIZED database by app/schema_migrate.py; every
-- statement is safe to re-run (IF NOT EXISTS) and must produce the exact same
-- schema as the equivalent block in init.sql.
--
-- While a declaration is `pending_review`, customer_id / ownership_card_id /
-- gestor_id are NULL and `submitted_data` is the ONLY place the declared data
-- lives (nothing was created in customers / ownership_cards).

CREATE TABLE IF NOT EXISTS declarations (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMP DEFAULT now(),
    reason TEXT NOT NULL,                   -- normalize.DECLARATION_REASONS (DETERIORO / PÉRDIDA)
    plate_quantity INTEGER NOT NULL,
    acting_as TEXT NOT NULL,                -- normalize.ACTING_AS (PROPIETARIO / POSEEDOR / GESTOR)
    customer_id INTEGER REFERENCES customers(id),            -- the CLIENTE; NULL while pending
    ownership_card_id INTEGER REFERENCES ownership_cards(id),
    gestor_id INTEGER REFERENCES customers(id),              -- NULL: no gestor, or still pending
    status TEXT NOT NULL,                   -- pending_review / approved / rejected
    review_flags TEXT[] NOT NULL DEFAULT '{}',
    reviewed_by INTEGER REFERENCES users(id),
    reviewed_at TIMESTAMP,
    review_note TEXT,
    submitted_data JSONB NOT NULL,          -- exact copy of what was declared (legal evidence)
    owner_signature_path TEXT,
    gestor_signature_path TEXT,
    notarized_pdf_path TEXT,
    submitter_ip TEXT,
    user_agent TEXT,
    accepted_terms_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS declarations_status_idx ON declarations (status);
-- (This file used to also create declarations_customer_id_idx. 0009 moves
-- customer_id out of `declarations` and drops that index, and this file is
-- re-run on a database built from the CURRENT init.sql -- where the column no
-- longer exists -- so the CREATE INDEX had to go. Databases that already ran the
-- old version get the index dropped by 0009; new ones never had it.)
CREATE INDEX IF NOT EXISTS declarations_submitter_ip_created_at_idx ON declarations (submitter_ip, created_at);
