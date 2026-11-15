VERDICT: PASS

## Summary
- Adds migration 0017 and init.sql with the `permissions` table, seeded with the 13 catalog rows in tuple order. `role_permissions_permission_fkey` is added with the conrelid guard, after a DELETE of stale codes.
- `PermissionRecord` and `list_permissions` are added to `db/roles.py` and exported alphabetically in both the import block and `__all__`. `permissions.py` has docstring-only changes.
- The new tests match the spec: a double-apply test, a throwaway-schema test, a drift guard, a `list_permissions` test and an FK violation test. `_APP_TABLES` includes `permissions`.
- `docs/DATABASE.md` (section, FK row, mermaid) and `docs/CORE.md` are updated in Spanish. No `tests/web` files were touched.

## Findings
None.

## Notes
- The seed SQL in init.sql and 0017 matches the `PERMISSIONS` labels, and the drift guard covers future drift.
- The migration header comment and the init.sql comment are accurate and match the neighbouring style.
