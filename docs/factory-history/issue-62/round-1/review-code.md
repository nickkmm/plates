VERDICT: PASS

## Summary
- Permission catalog, migration 0015, init.sql, roles data module, user additions and re-exports all match the spec.
- Migration is re-runnable, and its FK guard uses `conrelid = to_regclass('users')` as required. Unknown `users.role` values are backfilled before the FK is added.
- The data functions follow the conn contract. They validate codes before writing and return `False` for missing rows.
- `app/web`, `app/schema_migrate.py`, `tests/web` and `tests/conftest.py` have no diff.
- The gate run skipped the DB tests because Postgres was unreachable. The builder reports a run with the DB up (1572 passed). I did not re-run them.

## Findings
None.

## Notes
- `round-1/checks.md` shows DB-backed tests skipped in the gate environment. The DB-dependent migration and roles tests were therefore verified only by the builder's own run.
- `init.sql` puts the comment `-- admin / production / point_of_sale` on the same line as the long `role` column definition. This is fine but a little crowded.
