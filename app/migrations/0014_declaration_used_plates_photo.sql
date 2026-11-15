-- Photo of the used (deteriorated) plates the client hands in, asked for on
-- every DETERIORO card of a declaration. Stored name in the uploads volume,
-- like card_front_path. NULL on declarations made before the form asked for
-- it and on PÉRDIDA cards. Safe to re-run; same end schema as init.sql.

ALTER TABLE declaration_cards ADD COLUMN IF NOT EXISTS used_plates_photo_path TEXT;
