# Production status and history for production orders

> Issue #64: Add production statuses and history to production orders

## Summary
- Adds a `production_status` column to `production_orders` (`pending` → `in_progress` → `ready` → `delivered`) and a `production_order_events` history table through migration `0016_production_status.sql`, mirrored in `init.sql`.
- Existing orders are backfilled as `delivered`. New orders start as `pending` through the column default, so `insert_order` does not change.
- New data layer `app/core/db/production.py` and new business rules `app/core/production.py` (`advance_order` / `revert_order` with optimistic concurrency through `expected_status`).
- Tests cover the migration, the transitions, the error messages, the listing and counting, and delete/replace still working. Docs `DATABASE.md` and `CORE.md` are updated.
- No routes, templates or permission checks. Those belong to the production board task.

## Context
- **Schema.** `init.sql` creates `production_orders` (line 4) and adds the POS columns further down with `ALTER TABLE production_orders ADD COLUMN ...` (around line 278). Migrations live in `app/migrations/NNNN_*.sql`. The latest is `0015_roles_and_permissions.sql`, whose header comment shows the house style: what it does, why it is safe to re-run, "Same end schema as init.sql". `python -m app.schema_migrate` applies the migrations.
- **Orders data layer.** `app/core/db/orders.py` holds `insert_order` (it does not set a status), `delete_order` (it deletes plates, then the order, at line 170) and `OrderSummary`. `OrderSummary.plates` is built by `string_agg(... ORDER BY p.id)` in `_ORDER_SUMMARY_SELECT`, which puts `'reposición'`/`'anulada'`/`'sin placa'` placeholders where a plate has no number. `search_orders_by_number` matches the order number with `ILIKE %query.strip()%`.
- **Package exports.** `app/core/db/__init__.py` re-exports every submodule's public names (`from app.core.db.X import (...)` plus `__all__`) and documents the transaction contract: functions take an open `conn` and never commit or roll back.
- **Plates.** `plates.order_id`, `plates.plate_number`, `plates.quantity` (NOT NULL DEFAULT 1) and `plates.is_voided` (DEFAULT false).
- **Declarations.** `app/core/db/declarations.py` has `DeclarationRecord` (which includes `production_order_id`), `_SELECT_COLUMNS`, and `get_declaration`, which shows the `class_row(DeclarationRecord)` pattern to reuse.
- **Plate cleaning.** `app/core/normalize.py::clean_plate` upper-cases the value and strips whitespace.
- **Replace.** `app/core/ingestion.py::replace_order` deletes every order under an `order_number` through `db.delete_order`, then inserts a new one.
- **Pure-logic pattern.** `app/core/declarations.py` holds rules that take a `conn` and call `db.*`, with no Flask.
- **Tests.**
  - DB tests use the `db_conn` fixture (`tests/conftest.py`). It runs inside a savepoint that is rolled back, so DDL inside a test is rolled back too. `clean_db` truncates `TABLES ... CASCADE`, and the cascade also clears the new events table.
  - Per-module DB tests live in `tests/core/db/test_*.py`. Core logic tests live in `tests/core/test_*.py`.
  - `tests/test_schema_migrate.py` has the per-migration idempotency tests (`_0015_SQL`, `test_0015_..._safe_to_apply_twice_directly`), the backfill test pattern (`test_0008_...`), the `_APP_TABLES` tuple used by the schema-equivalence test `TestSchemaInvariant.test_migrated_old_schema_matches_fresh_init_sql`, and `_insert_order`.
  - Delete/replace tests are in `tests/core/db/test_orders.py` and `tests/core/test_ingestion.py`.
- **Docs.** `docs/DATABASE.md` has the mermaid `erDiagram` (line 43), the per-table column tables (`### production_orders` at line 193) and "Rarezas". `docs/CORE.md` has per-module sections (`### db/declarations.py`, `### declarations.py`, `### db/roles.py`, ...).

## Requirements
1. Create `app/migrations/0016_production_status.sql` with exactly the statements given in the issue, in this order:
   - add the column with `DEFAULT 'delivered'`;
   - set the default to `'pending'`;
   - create the status index;
   - create `production_order_events`;
   - create the `order_id` index.

   Add a header comment in the style of 0015: what the migration does, that existing orders are backfilled as `delivered` and new ones start as `pending`, why it is safe to re-run, and "Same end schema as init.sql".
2. Update `init.sql` so a fresh database ends with the same schema:
   - `production_orders.production_status TEXT NOT NULL DEFAULT 'pending'`;
   - the index `production_orders_production_status_idx`;
   - the table `production_order_events` (columns, FK `ON DELETE CASCADE` to `production_orders(id)`, nullable FK `user_id` to `users(id)`, `created_at TIMESTAMP NOT NULL DEFAULT now()`);
   - the index `production_order_events_order_id_idx`.

   Place them after both referenced tables exist.
3. Add `"production_order_events"` to `_APP_TABLES` in `tests/test_schema_migrate.py` so the schema-equivalence test covers the new table.
4. Do not change `db.insert_order`, `db.delete_order` or `ingestion.replace_order`. Deleting an order cascades its events.
5. Create `app/core/db/production.py`, strictly typed, never committing, with a module docstring that points to the transaction contract in `app/core/db/__init__.py`. It contains:
   - **Constants.** `STATUS_PENDING = "pending"`, `STATUS_IN_PROGRESS = "in_progress"`, `STATUS_READY = "ready"`, `STATUS_DELIVERED = "delivered"`, `PRODUCTION_STATUSES` (a tuple in that order) and `ACTIVE_STATUSES` (the first three).
   - **`ProductionOrderSummary`** (a dataclass) with these fields:
     - `id: int`, `order_number: str`, `order_date: date`, `requested_by: str`, `delivery_date: date | None`, `production_status: str`;
     - `plate_count: int`: the sum of `quantity` over plates with `is_voided` false, 0 when there are none;
     - `plates: str | None`: plate numbers joined by `', '` in `p.id` order, built like `OrderSummary.plates` with the same placeholders;
     - `declaration_ids: list[int]`: ids of declarations whose `production_order_id` is this order, ascending, `[]` when there are none;
     - `status_changed_at: datetime | None`: the latest event `created_at`, otherwise the order's `created_at`.
   - **`list_production_orders(conn, *, status: str | None, query: str | None = None, limit: int = 200) -> list[ProductionOrderSummary]`.**
     - Excludes orders that have at least one plate when all of their plates are `is_voided`.
     - `status=None` returns every status. Otherwise it filters on `production_status = status`.
     - A non-empty `query` (after `strip()`) keeps an order when `order_number ILIKE %query.strip()%`, or when any of its plates has `plate_number ILIKE %clean_plate(query)%`.
     - Sorting: when `status` is in `ACTIVE_STATUSES`, by `order_date ASC, id ASC`. When `status` is `delivered` or `None`, by `order_date DESC, id DESC`.
     - Applies `limit`.
   - **`count_by_production_status(conn) -> dict[str, int]`.** It has one key for every value in `PRODUCTION_STATUSES` (0 when there are no orders in that status) and uses the same fully-voided exclusion.
   - **`get_production_status(conn, order_id) -> str | None`.** Returns `None` when the order does not exist.
   - **`set_production_status(conn, order_id, *, from_status, to_status, user_id) -> bool`.**
     - Runs `UPDATE production_orders SET production_status = %s WHERE id = %s AND production_status = %s`.
     - When exactly one row changed, it inserts a `production_order_events` row (`order_id, from_status, to_status, user_id`) and returns True.
     - Otherwise it inserts nothing and returns False.
   - **`list_production_events(conn, order_id)`.** Returns a list of a small dataclass (e.g. `ProductionEvent`) with `from_status, to_status, username: str | None, created_at`, oldest first (`created_at, id`). It LEFT JOINs `users`, so a NULL `user_id` gives `username=None`.
   - **`production_statuses_for_orders(conn, order_ids) -> dict[int, str]`.** Uses one query (`WHERE id = ANY(%s)`) and returns `{}` for an empty input without querying.
6. Add `list_declarations_by_production_order(conn, order_id) -> list[DeclarationRecord]` to `app/core/db/declarations.py`, ordered by `id`, reusing `_SELECT_COLUMNS` and `class_row(DeclarationRecord)`.
7. Re-export from `app/core/db/__init__.py`, by adding them to the imports and to `__all__`:
   - every public name in `app/core/db/production.py`;
   - `list_declarations_by_production_order`.

   The status constants keep their `STATUS_*` names. `db.declarations` already has its own `STATUS_PENDING_REVIEW` etc., so check that nothing collides at the package level. The new names do not overlap with those.
8. Create `app/core/production.py`: pure, strictly typed, no Flask import, no role or permission argument. It contains:
   - `next_status(status: str) -> str | None` and `previous_status(status: str) -> str | None`. Both follow `PRODUCTION_STATUSES` and return `None` past either end or for an unknown status.
   - **`advance_order(conn, order_id: int, user_id: int | None, *, expected_status: str) -> str`.** It reads the current status with `db.get_production_status` and raises `ValueError` with the exact message for the first check that fails:
     1. The order does not exist: `"La orden no existe."`
     2. `expected_status == "delivered"`: `"La orden ya fue entregada."`
     3. The current status is not `expected_status`, or `expected_status` has no next status: `"El estado de la orden cambió mientras tanto. Revisa el estado actual e inténtalo de nuevo."`
     4. `db.set_production_status(conn, order_id, from_status=expected_status, to_status=next, user_id=user_id)` returns False: the same "cambió mientras tanto" message.

     On success it returns the new status.
   - **`revert_order(...)`** has the same signature. It moves one step back, with these checks:
     1. The order does not exist: `"La orden no existe."`
     2. `expected_status == "pending"`: `"La orden todavía no ha empezado."`
     3. A stale status, or a conditional update that changed nothing: the "cambió mientras tanto" message.

     Reverting from `delivered` to `ready` is allowed.
   - Neither function commits.
9. Add tests (they skip without Postgres, like the existing DB tests).
   - In `tests/test_schema_migrate.py`:
     - add `_0016_SQL`;
     - add a backfill test following the `test_0008` pattern: drop `production_order_events` and the `production_status` column inside the test, insert an order, apply `_0016_SQL`, and assert the old order is `delivered`. Then call `db.insert_order` and assert the new order is `pending`. Then apply `_0016_SQL` again and assert both statuses are unchanged;
     - add an idempotency test (`test_0016_..._safe_to_apply_twice_directly`) on the migrated schema.
   - Create `tests/core/db/test_production.py` for the data layer. It covers `set_production_status` returning True/False and writing or not writing an event, `list_production_events`, `production_statuses_for_orders`, `list_production_orders` and `count_by_production_status`.
   - Add a test for `list_declarations_by_production_order` in `tests/core/db/test_declarations.py`.
   - Create `tests/core/test_production.py` for the rules.
   - Add delete and replace tests with events present, in `tests/core/db/test_orders.py` and `tests/core/test_ingestion.py`.
10. Update the docs, in Spanish prose:
    - **`docs/DATABASE.md`:**
      - add a `production_status` row to the `production_orders` column table (values, default `'pending'`, that the web layer owns the Spanish labels);
      - add a new `### production_order_events` section;
      - add `production_order_events` to the mermaid diagram, with its relations to `production_orders` and `users`, and the new column in the `production_orders` entity;
      - add a note that orders existing when `0016` ran were backfilled as `delivered`;
      - mention that `replace_order` gives the new order `pending` and drops the old order's events.
    - **`docs/CORE.md`:** add a `### production.py` section (transitions, error messages, optimistic concurrency through `expected_status`, no roles) and a `### db/production.py` section, and mention `list_declarations_by_production_order` under `### db/declarations.py`.

## Acceptance criteria
1. **Migration backfill and idempotency.** `pytest tests/test_schema_migrate.py -k 0016` passes, with Postgres running:
   - an order present before `0016` is applied ends up `delivered`;
   - an order inserted afterwards with `db.insert_order` is `pending`;
   - applying `0016` a second time raises nothing and changes no status.
2. **Schema equivalence.** `pytest tests/test_schema_migrate.py -k "migrated_old_schema_matches_fresh_init_sql or every_migration_file"` passes, and `_APP_TABLES` contains `"production_order_events"`.
3. **Fresh database.** `init.sql` contains `production_status TEXT NOT NULL DEFAULT 'pending'` and `CREATE TABLE production_order_events`, and `app/migrations/0016_production_status.sql` exists with the issue's statements.
4. **Advance through the whole chain.** In `tests/core/test_production.py`, `advance_order` called three times on a new order moves it `pending` → `in_progress` → `ready` → `delivered`, returning each new status. After each call, `db.list_production_events` has exactly one more event with the right `from_status`/`to_status`, and its `user_id` equals the id passed in. Check it by querying `production_order_events.user_id`, or the username of a seeded user.
5. **Revert.** `revert_order` from `ready` returns `in_progress`, the status is `in_progress`, and one event `ready` → `in_progress` is recorded.
6. **Error messages.** Each case raises `ValueError` with the exact text, and the status and event count are unchanged:
   - a nonexistent order id gives `"La orden no existe."`;
   - `advance_order(..., expected_status="delivered")` gives `"La orden ya fue entregada."`;
   - `revert_order(..., expected_status="pending")` gives `"La orden todavía no ha empezado."`;
   - a stale `expected_status` gives the "cambió mientras tanto" message. Test it by advancing once with `expected_status="pending"`, then calling again with `expected_status="pending"`, to simulate two users clicking at once.
7. **Conditional update.** In `tests/core/db/test_production.py`, `set_production_status` with a wrong `from_status` returns False and writes no event. With the right one it returns True and writes one event.
8. **Listing and counting.** These tests pass in `tests/core/db/test_production.py`:
   - `list_production_orders` filters by status, and `status=None` returns all;
   - a `query` matches by partial order number and by plate number written with lowercase and spaces (e.g. `"abc 123"` finds plate `ABC123`);
   - an order whose plates are all `is_voided` is excluded;
   - `pending` results come oldest first, while `delivered` and `None` results come newest first;
   - `plate_count` ignores voided plates;
   - `declaration_ids` lists linked declarations;
   - `status_changed_at` equals the latest event time once one exists;
   - `count_by_production_status` returns all four keys with the right counts, again excluding fully voided orders.
9. **Batch lookup.** `production_statuses_for_orders` returns the right map for several ids in one call, and `{}` for an empty list.
10. **Declarations by order.** `list_declarations_by_production_order` returns the declarations linked to an order, and `[]` for an order with none. The test is in `tests/core/db/test_declarations.py`.
11. **Delete and replace with events.**
    - `db.delete_order` on an order that has events succeeds, and both the order and its events are gone. The test is in `tests/core/db/test_orders.py`.
    - `ingestion.replace_order` on an order that has events succeeds, and the replacement order is `pending` with no events. The test is in `tests/core/test_ingestion.py`.
12. **No Flask in the rules module.** `grep -n "flask" app/core/production.py` prints nothing.
13. **Docs.**
    - `docs/DATABASE.md` mentions `production_status` and `production_order_events`, including in the mermaid diagram, plus the backfill-as-`delivered` note.
    - `docs/CORE.md` has sections for `production.py` and `db/production.py`.

## Out of scope
- Routes, blueprints, templates, navigation and Spanish status labels in the web layer. These belong to the production board task.
- Any permission or role check (`production.manage`, `production.deliver`) in the new functions.
- Changes to `insert_order`, `delete_order`, `replace_order`, `/ingest`, or declaration stages or statuses.
- Customer notifications.
- Validating `status` against `PRODUCTION_STATUSES` in `list_production_orders`, adding a CHECK constraint or a lookup table for statuses, or adding pagination beyond `limit`.
- Editing an already-applied migration, `factory/checks.sh` or `factory/config.json`.

## Assumptions
- **Orders with zero plates.** They are not "fully voided", so `list_production_orders` and `count_by_production_status` include them. The issue only excludes orders whose plates are all voided.
- **Partial search.** `query` matches partially (`ILIKE %...%`) on both order number and plate. This matches `search_orders_by_number`, and the board is a search box.
- **Empty query.** A whitespace-only `query` is treated as `None`. There is nothing to match.
- **Sort for "all".** `status=None` sorts newest first, as the issue groups it with `delivered`. Ties break on `id`.
- **Revert from `delivered`.** `revert_order` from `delivered` is allowed (→ `ready`), and `"La orden ya fue entregada."` is only raised by `advance_order`. The issue describes revert as a one-step undo and only lists the `pending` message as extra. Undoing a mistaken delivery mark is the obvious use. This is a product call the approver may want to confirm.
- **Unknown status.** An unknown `expected_status` raises the "cambió mientras tanto" message. It can never equal the stored status.
- **Event row shape.** Events are returned as a dataclass (`ProductionEvent`), not a dict. This matches the `class_row` dataclass style used across `app/core/db/`.
- **`plates` placeholders.** `ProductionOrderSummary.plates` uses the same placeholder text as `OrderSummary.plates`. The issue says "like `OrderSummary.plates`".
