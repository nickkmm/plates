STATUS: READY

# Build: Store roles and permissions in the database

## Summary
- New pure permission catalog `app/core/permissions.py` (13 codes, `ADMIN_ROLE`, `effective_permissions`).
- New `roles`/`role_permissions` tables with seed data and `users_role_fkey` (`ON UPDATE CASCADE`), via migration `0015` (re-runnable, backfills unknown roles) and `init.sql`.
- New `app/core/db/roles.py` CRUD plus `update_user`, `set_user_password`, `count_active_users_with_role` and `UserRecord.created_at`, all re-exported from `app.core.db`.
- No change in `app/web/`, `app/schema_migrate.py` or `tests/conftest.py`; Spanish docs updated. Draft PR #82.

## Changes
- `app/core/permissions.py` (new): `PERMISSIONS`, `PERMISSION_CODES`, `ADMIN_ROLE = "admin"`, `effective_permissions(role, stored)`. No Flask/DB imports.
- `app/migrations/0015_roles_and_permissions.sql` (new): creates `roles` and `role_permissions` (`IF NOT EXISTS`), seeds the 3 system roles and their permission sets (`ON CONFLICT DO NOTHING`), inserts missing `users.role` values as non-system roles (`label = name`, no permissions), then adds `users_role_fkey` in a `DO` block guarded by `conname` + `conrelid = to_regclass('users')`.
- `init.sql`: the same two tables and seed rows, created before `users`; `users.role` gets `CONSTRAINT users_role_fkey REFERENCES roles(name) ON UPDATE CASCADE`.
- `app/core/db/roles.py` (new): `RoleRecord`, `list_roles`, `get_role`, `get_role_permissions`, `create_role`, `update_role`, `delete_role` (codes validated against `PERMISSION_CODES` with a Spanish `ValueError` before any write; no commits).
- `app/core/db/users.py`: `created_at` field/column, `update_user`, `set_user_password`, `count_active_users_with_role`.
- `app/core/db/__init__.py`: docstring lists `roles.py`; the 10 new names imported and in `__all__`.
- `tests/core/test_permissions.py` (new), `tests/core/db/test_roles.py` (new), `tests/core/db/test_users.py` (extended).
- `tests/test_schema_migrate.py`: `roles`/`role_permissions` in `_APP_TABLES`; `TestTranslateRoleValuesToEnglish` inserts `produccion`/`punto_venta` into `roles` first (assertions unchanged); new 0015 re-apply test, `TestRolesAndPermissions` (exact seed, `ON UPDATE CASCADE` constraint def, `legacy_role` in a throwaway schema), and `TestBackfillUsers::test_runs_on_a_fresh_init_sql_database`.
- `docs/DATABASE.md`: erDiagram relations/entities, column sections for `roles` and `role_permissions`, rewritten `users.role` row, 0015 backfill note. `docs/CORE.md`: `permissions.py` and `db/roles.py` sections, new `db/users.py` rows, module table row.

## Acceptance criteria
1. [x] 0015 applied by the runner, rerun is a no-op: `TestApplySqlMigrations::test_rerun_applies_nothing_new` and `TestMain::test_rerun_is_a_no_op` pass (in the full checks.sh run, 1572 passed, against the local `plates_test` Postgres).
2. [x] Schema invariant with the new tables: `grep -n '"roles"' tests/test_schema_migrate.py` -> line 97 `... "declaration_cards", "roles", "role_permissions",`; `TestSchemaInvariant::test_migrated_old_schema_matches_fresh_init_sql` passes.
3. [x] Seed matches requirement 2 exactly: `TestRolesAndPermissions::test_seeded_roles_and_permissions_match_exactly` passes (set equality on roles and per-role permission sets).
4. [x] `legacy_role`: `TestRolesAndPermissions::test_unknown_role_is_backfilled_and_fk_added` passes (label `legacy_role`, `is_system` false, 0 permission rows, FK present on the throwaway schema's `users`, second run no error).
5. [x] `TestRolesAndPermissions::test_users_role_fkey_cascades_on_update` asserts `pg_get_constraintdef` == `FOREIGN KEY (role) REFERENCES roles(name) ON UPDATE CASCADE`; passes.
6. [x] `pytest tests/core/test_permissions.py` passes (7 tests; admin with no rows gets all 13, unknown codes dropped).
7. [x] `pytest tests/core/db/test_roles.py tests/core/db/test_users.py` passes (run together with test_permissions and test_schema_migrate: 79 passed with the DB reachable).
8. [x] `TestTranslateRoleValuesToEnglish` (both tests), `TestBackfillUsers` (including the new fresh-database test) pass.
9. [x] `git diff origin/main --stat -- app/web app/schema_migrate.py tests/web tests/conftest.py` prints nothing; all `tests/web/` tests pass in the full run.
10. [x] `python -c "from app.core import db; db.list_roles; db.update_user; db.count_active_users_with_role; db.RoleRecord"` -> ok; `grep -nE "flask|app.core.db" app/core/permissions.py` finds nothing (exit 1).
11. [x] `docs/DATABASE.md` has `roles`/`role_permissions` in the erDiagram (relations `roles ||--o{ users` and `roles ||--o{ role_permissions`) and column sections `### roles` / `### role_permissions`; the `users.role` row now describes the FK (no more "sin FK ni CHECK"). `docs/CORE.md` has `### permissions.py (catálogo de permisos)` and `### db/roles.py`. Prose in Spanish.

## Validation
- `bash factory/checks.sh` from the worktree, with `POSTGRES_PASSWORD`/`POSTGRES_USER` exported from the main checkout's `.env` so DB tests run (the worktree has no `.env`): ruff passed, mypy passed, pytest 1572 passed, shell/factory checks passed. EXIT=0.
- Targeted: `pytest tests/core/test_permissions.py tests/core/db/test_roles.py tests/core/db/test_users.py tests/test_schema_migrate.py` -> 79 passed.
- Note: no page changes, so no browser check.

## Log
### Round 1
- Created the venv and installed `requirements-dev.txt`. Docker Desktop was not running; started it so `plates-db-1` (127.0.0.1:5433) was up for the DB tests.
- Implemented the spec as above in four commits and opened draft PR #82.
- Environment note: `plates_test` is one database shared by every worktree, and `_db_ready` applies migrations durably, so it now has migration 0015 recorded and `users_role_fkey` in place. Other branches that don't contain 0015 can see `test_every_migration_file_is_recorded_as_applied` and their own `TestTranslateRoleValuesToEnglish` fail against it until this PR is merged or the test DB is reset. That's how the shared test DB works, not something this branch can avoid.
