-- Last phase of the declarations plan: the formal link from a FABRICATED plate
-- to its ownership card, `plates.ownership_card_id`. See docs/DATABASE.md for
-- the rule and app/core/db/ownership_cards.py::resolve_card_id_for_plate for
-- the code that keeps it up to date on every insert/edit.
--
-- Rule: only plates of orders whose customer is CLIENTE or GESTOR (or whose
-- type is still NULL = not classified yet) can carry a card; EMPRESA and
-- TRÁNSITO have no "tarjeta de propiedad", so their plates are never linked.
-- Ranges (plate_to), batch replacements, voided and annulled plates are never
-- linked either. The link matches ownership_cards.plate_number (UNIQUE) against
-- plates.plate_number; no card -> NULL.
--
-- Applied to an ALREADY-INITIALIZED database by app/schema_migrate.py, same
-- as the earlier migrations -- every statement here must be safe to re-run
-- (ADD COLUMN / CREATE INDEX IF NOT EXISTS; the backfill only touches rows
-- still NULL). Must produce the exact same end schema as the equivalent
-- lines in init.sql.

ALTER TABLE plates ADD COLUMN IF NOT EXISTS ownership_card_id INTEGER REFERENCES ownership_cards(id);

CREATE INDEX IF NOT EXISTS plates_ownership_card_id_idx ON plates (ownership_card_id);

-- One-time backfill of already-fabricated plates (production had no cards
-- when this shipped, so it is expected to link nothing there). Orders without
-- a customer or customers with a NULL type are included, same as the runtime
-- rule: a card is only matched by exact plate number.
UPDATE plates
SET ownership_card_id = oc.id
FROM ownership_cards oc, production_orders o
LEFT JOIN customers c ON c.id = o.customer_id
WHERE plates.ownership_card_id IS NULL
  AND o.id = plates.order_id
  AND plates.plate_number = oc.plate_number
  AND plates.plate_to IS NULL
  AND plates.is_voided IS NOT TRUE
  AND plates.is_plate_annulled IS NOT TRUE
  AND plates.is_batch_replacement IS NOT TRUE
  AND (c.type IS NULL OR c.type NOT IN ('EMPRESA', 'TRÁNSITO'));
