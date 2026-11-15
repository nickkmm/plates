ESCALATE

## Summary
- Adds migration `0017_permissions.sql` and an `init.sql` change: a new `permissions(code, label, sort_order)` table seeded with the 13 codes from `PERMISSIONS`, and `role_permissions.permission` becomes an FK to it.
- Before adding the FK, the migration deletes `role_permissions` rows whose code is not in the catalog. Those rows grant nothing today.
- Adds `PermissionRecord` / `list_permissions` with tests and Spanish docs. Round 1 gate PASS, 1629 tests, both reviews PASS with no findings.
- Escalated because it is a database migration on the table that decides route access, and every push to `main` runs the migration in production.

## Why
The work matches the spec and stays inside intake's Scope. Every acceptance criterion has convincing evidence, and the gate and both reviews pass. Even so, the change adds a migration that deletes rows and adds a constraint on `role_permissions` (permissions and access), and this project requires a person to check that before merging. The docs in `docs/DATABASE.md` and `docs/CORE.md` were updated, so the code does not drift from the docs.

## Acceptance criteria
1. 0017 applies once, then nothing: met. build.md records a manual run on a throwaway DB migrated from `origin/main` (19 `schema_migrations` rows, the second run printed "up to date"), and `test_rerun_applies_nothing_new` passes.
2. The `permissions` table equals `PERMISSIONS` in order: met. The drift-guard test and the `list_permissions` test pass. I spot-checked the seed in both `0017_permissions.sql` and `init.sql` against `app/core/permissions.py` lines 18-30: same 13 codes, same labels and order.
3. Inserting `('production', 'nope.code')` raises `ForeignKeyViolation`: met (`TestRolePermissionsForeignKey`, passing in the gate).
4. A stale row is deleted, other rows are kept, the FK exists once and a re-run is safe: met (`TestPermissions::test_unknown_permission_is_deleted_and_fk_added`).
5. The schema invariant passes with `permissions` in `_APP_TABLES`: met (full suite green).
6. `permissions.py` changes only in its docstring: met. I checked the diff myself and only the module docstring changed.
7. `tests/web/` is untouched and passes: met. `git diff --stat` lists no `tests/web` file, and the gate passed 1629 tests.
8. The exports work and are in alphabetical position: met per build.md and the code review.
9. Docs sections, the FK mention and the mermaid edge are present: met per build.md and the code review. The diff touches both doc files.

## For the human
Recommendation: don't merge yet. Check it first, then merge.

Reasons:
- It adds a database migration (`app/migrations/0017_permissions.sql`) plus an `init.sql` change, and merging to `main` runs it in production straight away.
- It touches role and route permissions. The migration runs `DELETE FROM role_permissions WHERE permission NOT IN (SELECT code FROM permissions)` and then adds `role_permissions_permission_fkey` with `ON DELETE CASCADE`.

What to check:
1. Optional: before merging, run `SELECT * FROM role_permissions WHERE permission NOT IN (<the 13 codes>)` on production to see which rows the migration will delete. They should be none, or only junk codes that grant nothing today, because `effective_permissions` already ignores them.
2. You are OK with `ON DELETE CASCADE`: deleting a row from `permissions` silently removes that grant from every role. The security review notes this can only remove access and never grant it. The catalog should stay effectively read-only, with the Python tuple as the source of truth.
3. The seed in the migration and in `init.sql` matches `app/core/permissions.py`. I checked this and the drift-guard test covers it.

Files to look at:
- `app/migrations/0017_permissions.sql`
- `init.sql` (the `roles` / `permissions` / `role_permissions` block)
- `app/core/db/roles.py`, `app/core/db/__init__.py`
- `tests/test_schema_migrate.py`, `tests/core/db/test_roles.py`
- `docs/DATABASE.md`, `docs/CORE.md`
