VERDICT: PASS

## Summary
- Reviewed the 3 commits: migration 0018, init.sql, permissions, users data layer, auth, schema_migrate, tests and docs.
- The code follows the spec. 0018 is guarded and safe to run twice. init.sql records the 18 migration versions and leaves out the two backfill versions. No existing migration file changed.
- `effective_permissions` takes the roles and materialises them once. `current_user()` reads the roles and permissions in one connection. The session no longer depends on `role`. The old `ROLES` constants are removed.
- The `grep` for `users.role`, `session["role"]` and `user.role` on `app/` and `tests/` now hits only historical-schema comments and docstrings in `tests/test_schema_migrate.py`. `test_roles.py` is unchanged. The gate passed on 309162a.

## Findings
None.

## Notes
- The `backfill_users` docstring in `app/schema_migrate.py` and the `permission_required` docstring in `app/web/auth.py` have awkward line wraps (a stray "ON" at the end of a line, and a long line). This is cosmetic only.
