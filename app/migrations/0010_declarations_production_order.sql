-- Option C of the declarations plan: link a declaration to the production order
-- that attended it ("Crear pedido" from an approved declaration). NULL = not
-- attended yet. ON DELETE SET NULL: deleting or replacing an order (replace_order)
-- just makes the declaration attendable again instead of blocking the delete.
--
-- Applied to an ALREADY-INITIALIZED database by app/schema_migrate.py -- every
-- statement must be safe to re-run and must produce the same end schema as the
-- equivalent lines in init.sql.

ALTER TABLE declarations
    ADD COLUMN IF NOT EXISTS production_order_id INTEGER REFERENCES production_orders(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS declarations_production_order_id_idx ON declarations (production_order_id);
