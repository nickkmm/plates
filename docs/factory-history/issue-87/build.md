STATUS: READY

# Build: Permissions catalog table with an FK from role_permissions

## Summary
- New `permissions(code, label, sort_order)` table, seeded with the 13 `PERMISSIONS` entries in tuple order (migration `0017_permissions.sql` + `init.sql`).
- `role_permissions.permission` is now an FK (`role_permissions_permission_fkey`, `ON DELETE CASCADE ON UPDATE CASCADE`); the migration first deletes rows with unknown codes.
- `db.PermissionRecord` and `db.list_permissions(conn)` added for the future roles screen; Python validation unchanged.
- New tests (drift guard, double-apply, throwaway schema, FK violation, `list_permissions`) and Spanish docs in `docs/DATABASE.md` / `docs/CORE.md`.
- Draft PR: https://github.com/nickkmm/plates/pull/89

## Changes
- `app/migrations/0017_permissions.sql` (new): English header comment; `CREATE TABLE IF NOT EXISTS permissions`; seed of 13 rows `ON CONFLICT DO NOTHING`; `DELETE` of `role_permissions` rows not in the catalog; guarded `DO` block (conname + conrelid) adding `role_permissions_permission_fkey`.
- `init.sql`: `permissions` table and seed created before `role_permissions`; `role_permissions.permission` declares the named inline FK; comment above `roles` updated (no longer says "validated in Python (no CHECK)", mentions `permissions`).
- `app/core/permissions.py`: module docstring only (mirrored in the table, a test keeps them identical, adding a code needs a migration and a tuple entry).
- `app/core/db/roles.py`: `PermissionRecord(code, label)` dataclass, `list_permissions(conn)` (ordered by `sort_order`, `class_row`), module docstring updated (Python validation first for a clean `ValueError`, FK also enforces).
- `app/core/db/__init__.py`: exports `PermissionRecord` and `list_permissions` in the import block and `__all__`; `roles.py` docstring line updated.
- `tests/test_schema_migrate.py`: `"permissions"` in `_APP_TABLES`; `_0017_SQL`; `TestApplySqlMigrations.test_0017_permissions_sql_is_safe_to_apply_twice_directly`; new `TestPermissions` class with the drift guard and the throwaway-schema test (`_PRE_0015_USERS_SQL` + `_0015_SQL`, insert `gone.code`, apply 0017, check deletion, rest unchanged, FK count 1, second run).
- `tests/core/db/test_roles.py`: `TestListPermissions` and `TestRolePermissionsForeignKey`; module docstring mentions `permissions`.
- `docs/DATABASE.md`: mermaid relation + `permissions` entity, `role_permissions.permission` marked `PK, FK`; new `### \`permissions\`` section; `role_permissions.permission` row describes the FK and the 0017 cleanup.
- `docs/CORE.md`: `db/roles.py` summary row, section prose, `PermissionRecord`, `list_permissions` row; `permissions.py` section mentions the mirror table.

## Acceptance criteria
1. [x] 0017 applied once, then nothing: created `plates_ac1_6` from `origin/main`'s `init.sql`, ran `origin/main`'s `python -m app.schema_migrate` (applied up to `0016_production_status.sql`), then the branch's: printed `applied 0017_permissions.sql`; `schema_migrations` had `0017_permissions.sql` (19 rows), `permissions` had 13 rows; second run printed only `schema_migrate: up to date.` and the count stayed 19. DB dropped afterwards. `test_rerun_applies_nothing_new` passes.
2. [x] Table equals `PERMISSIONS` in order: `TestPermissions::test_permissions_table_matches_permissions_catalog_exactly` and `TestListPermissions::test_returns_the_catalog_in_order` pass.
3. [x] `('production', 'nope.code')` raises `ForeignKeyViolation`: `TestRolePermissionsForeignKey::test_unknown_code_inserted_directly_raises_foreign_key_violation` passes.
4. [x] Stale row deleted, others kept, FK exactly once, re-run safe: `TestPermissions::test_unknown_permission_is_deleted_and_fk_added` passes.
5. [x] `TestSchemaInvariant::test_migrated_old_schema_matches_fresh_init_sql` passes with `"permissions"` in `_APP_TABLES`.
6. [x] `git diff origin/main -- app/core/permissions.py` shows only docstring lines.
7. [x] `git diff --stat origin/main -- tests/web` is empty; the full suite (incl. `tests/web/`) passes, 1629 passed.
8. [x] `.venv/Scripts/python -c "from app.core.db import PermissionRecord, list_permissions; ..."` works; `PermissionRecord` sits between `PRODUCTION_STATUSES` and `PriceRow`, `list_permissions` between `list_ownership_cards_for_customer` and `list_production_events` (ASCII order, as the neighbours; the existing `__all__` itself isn't fully sorted on main).
9. [x] `docs/DATABASE.md` has `### \`permissions\``, `role_permissions_permission_fkey`, `ON DELETE CASCADE` in the `role_permissions` row, and `permissions ||--o{ role_permissions : "permission"`; `docs/CORE.md`'s `db/roles.py` section mentions `list_permissions` and `PermissionRecord`.

## Validation
- `bash factory/checks.sh` with `.env` credentials, `TEST_POSTGRES_DB=plates_test_6`, `TEST_REQUIRE_DB=1`: passed (ruff, mypy, 1629 tests passed, no skips, shell/factory checks).
- `ruff check app/` and `mypy app/` re-run after the last docstring edit: passed.
- Targeted pytest run of the new and related tests on `plates_test_6`: 31 passed.
- Manual migration check (AC1) on a throwaway `plates_ac1_6` database: passed.

## Log
### Round 1
Set up `.venv` and installed `requirements-dev.txt`. Implemented spec requirements 1-11: migration 0017, `init.sql`, docstrings, `PermissionRecord`/`list_permissions` and exports, tests in `tests/test_schema_migrate.py` and `tests/core/db/test_roles.py`, Spanish docs. Kept CRLF working-tree line endings in edited files. Gate script passes against `plates_test_6`. Pushed `factory/006-add-the-permissions-table` and opened draft PR #89 (no AI-attribution line in the body, per `CLAUDE.md`).
