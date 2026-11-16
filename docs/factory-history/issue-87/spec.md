# Permissions catalog table with an FK from role_permissions

> Issue #87: Add the permissions table and make role_permissions reference it

## Summary
- Intake accepted issue #87 as written. This spec turns it into checkable requirements with no rescoping.
- A new migration `0017_permissions.sql` and `init.sql` add a `permissions(code, label, sort_order)` table. It is seeded with the 13 entries of `app/core/permissions.py::PERMISSIONS`, in the same order.
- `role_permissions.permission` becomes a foreign key (`role_permissions_permission_fkey`), so Postgres itself rejects an unknown code. Rows with stale codes are deleted first. They grant nothing today, so nobody's access changes.
- `app/core/db/roles.py` gains `PermissionRecord` and `list_permissions(conn)` for the future roles screen. New tests and the Spanish docs in `docs/DATABASE.md` and `docs/CORE.md` cover the change.

## Context
- **Catalog in Python:** `app/core/permissions.py` defines:
  - `PERMISSIONS`, a tuple of 13 `(code, Spanish label)` pairs.
  - `PERMISSION_CODES`.
  - `ADMIN_ROLE`.
  - `effective_permissions(role, stored)`. It returns every code for admin. For any other role it returns the stored codes intersected with `PERMISSION_CODES`, so a stored code outside the catalog already grants nothing.

  The module docstring says codes are validated only in Python.
- **Data layer:** `app/core/db/roles.py` has `RoleRecord`, `list_roles`, `get_role`, `get_role_permissions`, `create_role`, `update_role`, `delete_role` and `_validated`. `_validated` raises `ValueError("Permisos no válidos: ...")` before any write. The module docstring says there is no DB-level check. `app/core/db/__init__.py` re-exports these functions:
  - an import block `from app.core.db.roles import (...)` around line 168
  - an alphabetical `__all__`
  - a module docstring that lists `roles.py` around line 57
- **Pattern to copy:** `app/migrations/0015_roles_and_permissions.sql`. It has:
  - an English header comment that explains what it does and why it is safe to re-run
  - `CREATE TABLE IF NOT EXISTS`
  - `INSERT ... ON CONFLICT DO NOTHING`
  - an FK added inside a `DO $$ ... IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = ... AND conrelid = to_regclass('...')) ... $$` block

  The highest existing migration is `0016_production_status.sql`, so the new file is `0017_permissions.sql`. Migrations run through `python -m app.schema_migrate`.
- **`init.sql`:** lines ~105-140 create `roles`, then `role_permissions` (`permission TEXT NOT NULL`, no FK), then seed both. The comment there says codes are "validated in Python (no CHECK)". Lines ~163 show the inline named-FK style: `role TEXT NOT NULL CONSTRAINT users_role_fkey REFERENCES roles(name) ...`.
- **Tests:** `tests/test_schema_migrate.py` contains:
  - `_APP_TABLES` (line ~97)
  - `_0015_SQL` and `_PRE_0015_USERS_SQL`
  - `TestApplySqlMigrations.test_0015_roles_and_permissions_sql_is_safe_to_apply_twice_directly` (the "apply twice directly" pattern)
  - `TestSchemaInvariant.test_migrated_old_schema_matches_fresh_init_sql`. It compares the `information_schema` columns and constraints of a fresh `init.sql` against an old schema with every migration applied.
  - `TestRolesAndPermissions.test_unknown_role_is_backfilled_and_fk_added` (the throwaway-schema pattern)
  - `TestPlateTypesAndInventoryCategoriesSeed` (the drift-guard pattern)

  `tests/core/db/test_roles.py` holds the integration tests for `db/roles.py`. `tests/conftest.py`'s `clean_db` truncates only the tables in `TABLES`, which does not include roles or permissions, so the seeded catalog persists across tests.
- **Docs:**
  - `docs/DATABASE.md` has the mermaid `erDiagram` (around lines 45-140, with entity blocks for `roles` and `role_permissions`) and the `### roles` and `### role_permissions` sections (around line 352).
  - `docs/CORE.md` has the `### db/roles.py` section (around line 512) and its function table.

## Requirements
1. **The migration file.** Add `app/migrations/0017_permissions.sql`. It starts with an English header comment in the style of 0015. The comment says what the migration does, that rows with unknown codes grant nothing today and are deleted, and why it is safe to re-run (it can also mention the conrelid guard). The file does the following, in order:
   - `CREATE TABLE IF NOT EXISTS permissions (code TEXT PRIMARY KEY, label TEXT NOT NULL UNIQUE, sort_order INTEGER NOT NULL);`
   - One `INSERT INTO permissions (code, label, sort_order) VALUES ... ON CONFLICT DO NOTHING;` with the 13 pairs of `PERMISSIONS`, with `sort_order` 1 to 13 in tuple order. Copy the labels byte for byte from `app/core/permissions.py` (UTF-8, accents included).
   - `DELETE FROM role_permissions WHERE permission NOT IN (SELECT code FROM permissions);`
   - A guarded `DO` block that filters on `conname = 'role_permissions_permission_fkey' AND conrelid = to_regclass('role_permissions')`. It runs `ALTER TABLE role_permissions ADD CONSTRAINT role_permissions_permission_fkey FOREIGN KEY (permission) REFERENCES permissions(code) ON DELETE CASCADE ON UPDATE CASCADE`.
2. **`init.sql` table and seed.** In `init.sql`, create `permissions` with the same definition before `role_permissions`. Seed it with the same 13 rows and sort orders.
3. **`init.sql` FK and comments.** In `init.sql`, declare `role_permissions.permission TEXT NOT NULL CONSTRAINT role_permissions_permission_fkey REFERENCES permissions(code) ON DELETE CASCADE ON UPDATE CASCADE`. Update the comment block above `roles`/`role_permissions` so it no longer claims the codes are validated only in Python, and mention the `permissions` table.
4. **Fresh and migrated schemas match.** A fresh `init.sql` and the old schema with every migration applied produce identical columns and constraints, as checked by `TestSchemaInvariant`.
5. **`app/core/permissions.py` stays the same apart from its docstring.** `PERMISSIONS`, `PERMISSION_CODES`, `ADMIN_ROLE` and `effective_permissions` do not change. Update only the module docstring to say three things:
   - the catalog is mirrored in the `permissions` table
   - a test keeps the table and the tuple identical
   - adding a code requires both a new migration and an entry in the tuple
6. **`app/core/db/roles.py` additions.**
   - Add `@dataclass class PermissionRecord` with fields `code: str` and `label: str`, plus a short docstring.
   - Add `list_permissions(conn: psycopg.Connection[DictRow]) -> list[PermissionRecord]`. It returns every `permissions` row as `PermissionRecord`, ordered by `sort_order`. Use `class_row` like `list_roles`.
   - Keep `_validated` and every existing function's behaviour.
   - Update the module docstring: codes are validated in Python first, which gives a clean `ValueError`, and are also enforced by the FK.
7. **Exports.** Export `PermissionRecord` and `list_permissions` from `app/core/db/__init__.py`, in both the `roles` import block and `__all__`, each in its alphabetical position. Update the `roles.py` line of the module docstring if it describes the module's tables.
8. **Tests in `tests/test_schema_migrate.py`.**
   - Add `"permissions"` to `_APP_TABLES`.
   - Add a module-level `_0017_SQL` read like `_0015_SQL`.
   - Add a test in `TestApplySqlMigrations` that applies `_0017_SQL` twice directly on the migrated test database. Neither run raises, and the row counts of `permissions` and `role_permissions` do not change.
   - Add a throwaway-schema test, using the same structure as `TestRolesAndPermissions.test_unknown_role_is_backfilled_and_fk_added` (create the schema, set `search_path`, clean up in `finally`). It builds the post-0015 state by running `_PRE_0015_USERS_SQL` and then `_0015_SQL`, and inserts `('production', 'gone.code')` into `role_permissions`. Then it applies `_0017_SQL` and asserts all of the following:
     - the `gone.code` row is gone
     - every other `role_permissions` row from before is still present (compare the full set)
     - exactly one `role_permissions_permission_fkey` exists on that schema's `role_permissions`
     - a second `_0017_SQL` run raises nothing
   - Add a drift-guard test that asserts `SELECT code, label FROM permissions ORDER BY sort_order` equals `list(PERMISSIONS)` exactly. This covers same codes, same labels and the same order. Use the style of `TestPlateTypesAndInventoryCategoriesSeed`.
9. **Tests in `tests/core/db/test_roles.py`.**
   - Add a test that `db.list_permissions(db_conn)` returns 13 `PermissionRecord`s whose `(code, label)` pairs equal `PERMISSIONS` in order.
   - Add a test that a direct SQL `INSERT INTO role_permissions (role, permission) VALUES ('production', 'nope.code')` raises `psycopg.errors.ForeignKeyViolation`.
10. **`docs/DATABASE.md`, in Spanish prose.**
    - Add a `### permissions` section before `### role_permissions`. It contains a columns table (`code`, `label`, `sort_order`), explains the seed (the 13 codes from `PERMISSIONS`, from `init.sql` and migration `0017`) and explains why `sort_order` exists (stable display order for the future roles screen, matching the tuple).
    - Update the `role_permissions.permission` row: it is now an FK to `permissions(code)`, `ON DELETE CASCADE ON UPDATE CASCADE`, named `role_permissions_permission_fkey`, added in migration `0017`, which first deletes rows with unknown codes. Python validation stays in place.
    - In the mermaid diagram, add `permissions ||--o{ role_permissions : "permission"` and a `permissions` entity block, and mark `role_permissions.permission` as `PK, FK`.
11. **`docs/CORE.md`, in Spanish prose.**
    - In `### db/roles.py`, add `list_permissions(conn)` to the function table and describe `PermissionRecord(code, label)`.
    - Say that the catalog from `permissions.PERMISSIONS` is mirrored in the `permissions` table, and that the FK now enforces codes as well as the Python validation.

## Acceptance criteria
1. Running `python -m app.schema_migrate` on a test database migrated up to 0016 applies `0017_permissions.sql`, and a second run applies nothing. To verify, check `SELECT version FROM schema_migrations` for a 0017 entry after the first run, and check that the second run records no new version. `TestApplySqlMigrations.test_rerun_applies_nothing_new` also still passes.
2. `SELECT code, label FROM permissions ORDER BY sort_order` returns exactly the 13 pairs of `PERMISSIONS`, in order. Verified by the new drift-guard test in `tests/test_schema_migrate.py` and by the `list_permissions` test in `tests/core/db/test_roles.py`.
3. Inserting `('production', 'nope.code')` into `role_permissions` raises `psycopg.errors.ForeignKeyViolation`. Verified by the new test in `tests/core/db/test_roles.py`.
4. In a pre-0017 schema, a `role_permissions` row with code `gone.code` is deleted by the migration, all other rows remain, `role_permissions_permission_fkey` exists exactly once, and re-running the migration raises nothing. Verified by the new throwaway-schema test in `tests/test_schema_migrate.py`.
5. `TestSchemaInvariant.test_migrated_old_schema_matches_fresh_init_sql` passes with `"permissions"` in `_APP_TABLES`.
6. `PERMISSIONS`, `PERMISSION_CODES`, `ADMIN_ROLE` and `effective_permissions` are unchanged. `git diff origin/main -- app/core/permissions.py` shows only docstring lines.
7. No file under `tests/web/` is modified (`git diff --stat origin/main -- tests/web` is empty), and all of them pass. Admin, point_of_sale and production keep exactly the same access.
8. `from app.core.db import PermissionRecord, list_permissions` works, and both names appear in `app.core.db.__all__` in alphabetical position.
9. `docs/DATABASE.md` contains a `### \`permissions\`` section, the `role_permissions` section mentions `role_permissions_permission_fkey` and `ON DELETE CASCADE`, and the mermaid block contains `permissions ||--o{ role_permissions : "permission"`. `docs/CORE.md`'s `db/roles.py` section mentions `list_permissions` and `PermissionRecord`.

## Out of scope
- `user_roles`, and any change to `users.role` or `users_role_fkey` (next task in #86).
- Reading the catalog from the table in `permission_required`, `effective_permissions`, `get_role_permissions` or the navigation. Routes keep checking the Python codes.
- Removing or changing `_validated` or the `ValueError` message.
- Any UI or screen, any new permission code, any change to which route needs which code, or any change to the seeded `role_permissions` grants.
- Editing any already-applied migration (0001-0016).
- Adding `permissions` to `tests/conftest.py`'s `TABLES`. It is static seed data and must not be truncated.
- Touching `factory/checks.sh` or `factory/config.json`.

## Assumptions
- **What "post-0015 state" means in the throwaway-schema test:** it is built by running `_PRE_0015_USERS_SQL` and then the real `_0015_SQL`, rather than by hand-writing the tables. This reuses existing fixtures and matches what production really looked like before 0017.
- **How the drift guard compares:** it compares an ordered list rather than sets. The issue asks that `sort_order` reproduce the tuple order, and a list catches drift in codes, labels and order together.
- **The `0017` double-apply test runs on the shared migrated test database and compares row counts, the same as the 0015 test.** It is the existing pattern, and the issue asks for "adds no rows".
- **The `ForeignKeyViolation` test inserts with role `production`, a seeded system role,** so that the failure can only come from the permission FK and not from the role FK.
