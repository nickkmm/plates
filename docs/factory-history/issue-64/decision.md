ESCALATE

## Summary
- Adds `production_orders.production_status` (pending → in_progress → ready → delivered) and a `production_order_events` history table through new migration `0016_production_status.sql`, mirrored in `init.sql`.
- New data layer `app/core/db/production.py` and pure rules `app/core/production.py` (`advance_order` / `revert_order` with optimistic concurrency), plus tests and Spanish docs.
- Round 1: gate PASS, code review PASS with no findings. The code matches the spec and stays inside intake's Scope.
- Escalated because this adds a database migration that backfills every existing order on deploy. A push to `main` runs it against production. The project rules say a person checks this before merging.

## Why
The implementation matches the spec, the docs are updated, and the change stays in scope. But it adds `app/migrations/0016_production_status.sql` and changes `init.sql`, and every push to `main` deploys and runs migrations against live `production_orders`. The project rules require a person to check that before merging. One more caveat: the gate's `checks.sh` run had no Postgres (598 passed, 1018 skipped), so the DB-backed criteria rest only on the builder's local full run (1616 passed, none skipped). The gate did not confirm them independently.

## Acceptance criteria
1. Migration backfill and idempotency: met per build.md (2 passed). The test in `tests/test_schema_migrate.py` drops the column and table, applies 0016, checks old order = `delivered` and new order = `pending`, then re-applies. The gate run skipped it (no DB).
2. Schema equivalence: met per build.md. `_APP_TABLES` includes `"production_order_events"` (diff confirms).
3. Fresh database: met. I checked the diff: `init.sql` has `production_status TEXT NOT NULL DEFAULT 'pending'` and `CREATE TABLE production_order_events`, and 0016 has the issue's five statements in order.
4. Advance through the chain: met per build.md (`TestAdvanceOrder`). The code reads the status, checks it, then runs the conditional UPDATE and inserts the event with `user_id`.
5. Revert: met per build.md (`TestRevertOrder`). `previous_status` logic checked in the diff.
6. Error messages: met. I checked the exact strings and the order of checks in `app/core/production.py` against the spec. Tests listed in build.md.
7. Conditional update: met. I read `set_production_status`: it inserts the event only when `rowcount == 1`.
8. Listing and counting: met per build.md. I checked the SQL in the diff: the shared fully-voided fragment, the ILIKE on the order number and on the `clean_plate` value, ASC for active statuses and DESC otherwise, and the `COALESCE` for `status_changed_at`.
9. Batch lookup: met. `= ANY(%s)` runs one query, and an empty input returns `{}` without querying (diff).
10. Declarations by order: met (diff plus test in `tests/core/db/test_declarations.py`).
11. Delete and replace with events: met per build.md. `ON DELETE CASCADE` is in both the migration and `init.sql`.
12. No Flask in the rules module: met. I ran it: case-sensitive `grep -n "flask"` finds nothing. The only match is "No Flask." in the docstring, and only with `-i`.
13. Docs: met. I checked that `docs/DATABASE.md` has the mermaid entity and relations, the column row, the `### production_order_events` section and the "anteriores a `0016` figuran como `delivered`" note. `docs/CORE.md` sections are listed in build.md.

## For the human
Recommendation: don't merge yet

The code itself looks correct and complete. These are the checks only you can make before merging:

1. **Migration on production (required by project rules).** Read `app/migrations/0016_production_status.sql` and the end of `init.sql`. On deploy it adds a NOT NULL column with `DEFAULT 'delivered'` to `production_orders`. Every existing order becomes `delivered` with no events, and then the default switches to `pending`. Confirm that marking every current order as delivered is what you want. If some orders are actually still in production on deploy day, they won't appear in the active queue until someone moves them.
2. **Product call: reverting from `delivered`.** `revert_order` lets an order go from `delivered` back to `ready`, to undo a mistaken delivery mark. The spec listed this as an Assumption, and the reviewer flagged it for you. Confirm or reject it (`app/core/production.py`, `revert_order`).
3. **DB tests weren't run by the gate.** The round-1 `checks.log` shows every Postgres test skipped. Before merging, run `pytest tests/test_schema_migrate.py tests/core/db/test_production.py tests/core/test_production.py tests/core/db/test_orders.py tests/core/test_ingestion.py tests/core/db/test_declarations.py` with the test database up, or look at the PR's CI result.
4. Minor: `production_order_events.user_id` references `users(id)` with no `ON DELETE` rule, so a user who has events couldn't be hard-deleted. The app has no `DELETE FROM users` today, so nothing breaks now.

Files: `app/migrations/0016_production_status.sql`, `init.sql`, `app/core/production.py`, `app/core/db/production.py`, `tests/test_schema_migrate.py`, `docs/DATABASE.md`.
