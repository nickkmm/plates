DECISION: ACCEPT
RISK: high
SURFACES: none
RESCOPED: no

## Summary
- Accepted as written. The issue spells out the schema, the data layer, the business rules, the error messages and the tests.
- Dependency #62 (roles and permissions, migration 0015) is already merged, so `0016_production_status.sql` is the next migration number.
- Risk is high because the migration runs against live `production_orders` on deploy and backfills every existing order as `delivered`.
- No screen changes in this task. Routes, templates and the production board belong to a separate task.

## Scope
Build exactly what issue #64 specifies:
- **Migration `0016_production_status.sql`** (safe to re-run) and the same end schema in `init.sql`. It adds a `production_status` column to `production_orders`. Orders that already exist are backfilled as `delivered`, and new orders default to `pending`. It also creates a `production_order_events` table (with `ON DELETE CASCADE` to the order and a nullable `user_id` FK to `users`) and adds both indexes. `insert_order` stays unchanged.
- **`app/core/db/production.py`** (re-exported from `app/core/db/__init__.py`, never commits): the status constants, `ProductionOrderSummary`, `list_production_orders` (status filter; search by order number or by plate, cleaned with `normalize.clean_plate`; fully voided orders excluded; active statuses sorted oldest first, `delivered` and "all" sorted newest first), `count_by_production_status`, `get_production_status`, `set_production_status` (a conditional `UPDATE ... AND production_status = from_status` that writes an event only when a row changed), `list_production_events`, and `production_statuses_for_orders` (one query).
- **`list_declarations_by_production_order`** in `app/core/db/declarations.py`.
- **`app/core/production.py`** (pure, typed, no Flask, no role argument): `next_status`, `previous_status`, `advance_order`, `revert_order`. They raise `ValueError` with the exact Spanish messages given in the issue, including the stale `expected_status` case, which must change nothing.
- **Tests** for acceptance criteria 1 to 5, and `factory/checks.sh` passes.
- **Docs** (Spanish prose): `docs/DATABASE.md` (the column, the new table, the diagram and the backfill note) and `docs/CORE.md`.

Behaviour to keep and document:
- `db.delete_order` keeps working because events cascade.
- `ingestion.replace_order` deletes the order and inserts a new one, so the replacement order starts at `pending` and the old order's events go with it. This is the reading the issue implies.

Left out, as the issue says: routes, templates, navigation and per-role permission checks (all in the production board task), changes to declaration stages or `/ingest`, and customer notifications.

## Why
The request is fully specified and fits the POS roadmap (part of #59). The code it builds on is where the issue says:
- `db.insert_order`, `db.delete_order` (plates are deleted first, then the order) and `OrderSummary` are in `app/core/db/orders.py`.
- `ingestion.replace_order` deletes and re-inserts through `delete_order`.
- `clean_plate` is in `app/core/normalize.py`, and `declarations.production_order_id` already exists.

It is one coherent data-layer change that fits in a single PR. It is rated high risk because it is a deploy-time migration with a backfill on the main orders table.

## Questions
None.
