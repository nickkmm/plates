-- Photo of the used plates the client hands in, asked for on every card of a
-- declaration (DETERIORO or PÉRDIDA). Stored name in the uploads volume, like
-- card_front_path. NULL on declarations made before the form asked for it.
-- Safe to re-run; same end schema as init.sql.

ALTER TABLE declaration_cards ADD COLUMN IF NOT EXISTS used_plates_photo_path TEXT;
