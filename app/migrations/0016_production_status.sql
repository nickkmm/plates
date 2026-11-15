-- Production status of every order (pending -> in_progress -> ready ->
-- delivered, English identifiers; the Spanish labels belong to the web layer)
-- and its history, `production_order_events` (who moved it, from what, to
-- what, when). See docs/DATABASE.md; the rules live in app/core/production.py.
--
-- The column is added with DEFAULT 'delivered', so every order that exists
-- when this runs is backfilled as delivered (history, already handed over);
-- the default then becomes 'pending', so every order inserted afterwards
-- starts pending without db.insert_order changing.
--
-- Safe to re-run: ADD COLUMN IF NOT EXISTS (a second run adds nothing and
-- rewrites no status), SET DEFAULT is idempotent, CREATE TABLE / CREATE INDEX
-- IF NOT EXISTS. Same end schema as init.sql.

ALTER TABLE production_orders ADD COLUMN IF NOT EXISTS production_status TEXT NOT NULL DEFAULT 'delivered';
ALTER TABLE production_orders ALTER COLUMN production_status SET DEFAULT 'pending';
CREATE INDEX IF NOT EXISTS production_orders_production_status_idx ON production_orders (production_status);
CREATE TABLE IF NOT EXISTS production_order_events (
    id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES production_orders(id) ON DELETE CASCADE,
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    user_id INTEGER REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS production_order_events_order_id_idx ON production_order_events (order_id);
