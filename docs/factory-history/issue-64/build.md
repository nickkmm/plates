STATUS: READY

# Build: Production status and history for production orders

## Summary
- Migration `0016_production_status.sql` and `init.sql` add `production_orders.production_status` and the `production_order_events` history table. Orders that already exist are backfilled as `delivered`, and new ones start `pending`.
- New data layer `app/core/db/production.py`, re-exported from `app.core.db`, plus `db.list_declarations_by_production_order`.
- New rules `app/core/production.py`: `advance_order`/`revert_order` with optimistic concurrency through `expected_status` and the exact Spanish messages. No Flask and no roles.
- Tests cover the migration, the data layer, the rules, and delete/replace with events. `docs/DATABASE.md` and `docs/CORE.md` are updated.
- `factory/checks.sh` passes with Postgres running: all 1616 tests passed and none were skipped for lack of a database. Draft PR #84.

## Changes
- `app/migrations/0016_production_status.sql` (new): has the issue's five statements in order, with a header in the style of 0015 (what it does, the backfill, why it is safe to re-run, "Same end schema as init.sql").
- `init.sql`: appends the `production_status TEXT NOT NULL DEFAULT 'pending'` column, its index, the `production_order_events` table and its `order_id` index. They go after `users` exists, so the FK can reference it.
- `app/core/db/production.py` (new): has the status constants, the `ProductionOrderSummary` and `ProductionEvent` dataclasses, and these functions: `list_production_orders`, `count_by_production_status`, `get_production_status`, `set_production_status` (a conditional UPDATE that inserts the event only when `rowcount == 1`), `list_production_events` (LEFT JOIN `users`) and `production_statuses_for_orders` (`= ANY(%s)`, `{}` without a query). The fully-voided exclusion is one shared SQL fragment. An order with no plates is kept.
- `app/core/db/declarations.py`: adds `list_declarations_by_production_order` (`_SELECT_COLUMNS`, `class_row`, `ORDER BY id`).
- `app/core/db/__init__.py`: adds the module to the docstring and the new names to the imports and `__all__`. No name collides at package level, and nothing else referenced `db.STATUS_*`.
- `app/core/production.py` (new): `next_status`, `previous_status`, `advance_order`, `revert_order`. Each move reads the status once, then runs the checks in the order the spec gives. It does not commit.
- `tests/test_schema_migrate.py`: adds `_0016_SQL`, a backfill test (drops the column and the table inside the test, applies 0016, runs `db.insert_order`, then re-applies 0016) and an idempotency test. `"production_order_events"` is added to `_APP_TABLES`.
- `tests/core/db/test_production.py` (new) and `tests/core/test_production.py` (new): data-layer and rules tests.
- `tests/core/db/test_declarations.py`, `tests/core/db/test_orders.py`, `tests/core/test_ingestion.py`: one new test each (declarations by order, delete with events, replace with events).
- `docs/DATABASE.md`: in the mermaid diagram, adds the new column and entity and two relations. Also adds a `production_status` row, a `### production_order_events` section with the `replace_order` note, and a "Rarezas" note on the backfill as `delivered`.
- `docs/CORE.md`: adds `### db/production.py` and `### production.py` sections, and a `list_declarations_by_production_order` row under `db/declarations.py`.

## Acceptance criteria
1. [x] Migration backfill and idempotency: `pytest tests/test_schema_migrate.py -k 0016` gave 2 passed, run against the local Postgres. The tests assert that the old order is `delivered`, the order from `db.insert_order` is `pending`, and both are unchanged after a second apply.
2. [x] Schema equivalence: `pytest tests/test_schema_migrate.py -k "migrated_old_schema_matches_fresh_init_sql or every_migration_file"` gave 2 passed. `_APP_TABLES` contains `"production_order_events"`.
3. [x] Fresh database: `init.sql` has `production_status TEXT NOT NULL DEFAULT 'pending'` on line 318 and `CREATE TABLE production_order_events` on line 321. `app/migrations/0016_production_status.sql` exists with the issue's statements. Every DB test also runs on a schema built from `init.sql`.
4. [x] Advance through the whole chain: `TestAdvanceOrder.test_advances_through_the_whole_chain_recording_each_step` passes. It checks each return value and status, that the event count grows by exactly one per step with the right from/to, and that `production_order_events.user_id` equals the id passed in.
5. [x] Revert: `TestRevertOrder.test_revert_from_ready_goes_back_to_in_progress` passes (returns `in_progress`, the status is `in_progress`, and there is one `ready` → `in_progress` event with that user).
6. [x] Error messages: these `TestErrors` tests pass, each comparing the exact `str(exc)` and checking that the status and event count did not change:
   - `test_nonexistent_order` for advance and revert;
   - `test_advance_delivered`;
   - `test_revert_pending`;
   - `test_stale_expected_status_on_advance`: advances once from `pending`, then calls again with `pending`;
   - `test_stale_expected_status_on_revert`;
   - `test_unknown_expected_status`;
   - `test_conditional_update_that_changes_nothing`.
7. [x] Conditional update: `TestSetProductionStatus` passes. With the right `from_status`, the call returns True and writes one event. With the wrong one, it returns False and writes no event.
8. [x] Listing and counting: `TestListProductionOrders` and `TestCountByProductionStatus` in `tests/core/db/test_production.py` pass. They cover:
   - the status filter, with `None` returning all;
   - a partial order-number query (`" nt 89 "`) and a plate query (`"abc 123"` finds `ABC123`);
   - the fully voided order excluded, while partly voided and empty orders are kept;
   - `pending` oldest first, `delivered` and `None` newest first;
   - `plate_count` ignoring voided plates;
   - `declaration_ids`;
   - `status_changed_at`: the order's `created_at` at first, then the latest event time;
   - all four count keys, with the fully voided order excluded.
9. [x] Batch lookup: `TestProductionStatusesForOrders` passes. Several ids are looked up in one call, an unknown id is absent from the result, and `[]` gives `{}`.
10. [x] Declarations by order: `TestListDeclarationsByProductionOrder` in `tests/core/db/test_declarations.py` passes (ordered by id, and `[]` for an order with none).
11. [x] Delete and replace with events: these tests pass.
    - `TestDeleteOrder.test_removes_an_order_with_production_events_and_its_events` in `tests/core/db/test_orders.py`.
    - `TestReplaceOrder.test_replacing_an_order_with_production_events_starts_the_new_one_pending` in `tests/core/test_ingestion.py`: the new order is `pending` with no events, and the old order's events are gone.
12. [x] No Flask in the rules module: `grep -n "flask" app/core/production.py` prints nothing (exit 1).
13. [x] Docs: `docs/DATABASE.md` mentions `production_status` and `production_order_events`, both in the mermaid diagram, and has the "Las órdenes anteriores a `0016` figuran como `delivered`" note. `docs/CORE.md` has `### db/production.py` (line 535) and `### production.py` (line 560).

## Validation
- `bash factory/checks.sh`, run from the worktree with `POSTGRES_USER`/`POSTGRES_PASSWORD` exported from the main checkout's `.env` so the DB tests run: **passed**. The steps:
  - ruff and mypy: clean.
  - pytest: 1616 passed in 943s. It was slow because another job's pytest was using `plates_test` at the same time.
  - shell-script and factory checks: OK.
- The targeted pytest runs listed under criteria 1, 2 and 4 to 11: **passed**.
- `ruff check` on the changed test files: **passed**.

## Log
### Round 1
- Set up `.venv` in the worktree and implemented the spec in five commits: schema, data layer, rules, tests, docs.
- Product calls taken from the spec's Assumptions:
  - revert from `delivered` to `ready` is allowed;
  - an unknown `expected_status` gives the "cambió mientras tanto" message;
  - an order with zero plates is listed;
  - a blank query is ignored.
- Pushed `factory/005-add-production-statuses-and` and opened draft PR #84 (https://github.com/nickkmm/plates/pull/84). Following CLAUDE.md, the PR body has no AI-attribution line.
