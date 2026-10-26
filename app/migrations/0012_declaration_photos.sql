-- Identity photos attached to a declaration: the declarant's cédula (front and
-- back) and the ownership card (front and back) of every card. Stored names in
-- the uploads volume, like declarant_signature_path. NULL on declarations made
-- before the form asked for them. Safe to re-run; same end schema as init.sql.

ALTER TABLE declarations ADD COLUMN IF NOT EXISTS declarant_id_front_path TEXT;
ALTER TABLE declarations ADD COLUMN IF NOT EXISTS declarant_id_back_path TEXT;
ALTER TABLE declaration_cards ADD COLUMN IF NOT EXISTS card_front_path TEXT;
ALTER TABLE declaration_cards ADD COLUMN IF NOT EXISTS card_back_path TEXT;
