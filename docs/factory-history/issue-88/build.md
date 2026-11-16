STATUS: READY

# Build: Several roles per user through a user_roles table

## Summary
- New `user_roles(user_id, role, assigned_at, assigned_by)` table (migration `0018`) is the only place role assignments live; it is backfilled from `users.role`, which is then dropped.
- A user's permissions are the union over all their roles; `admin` in any role grants everything. Every seeded user keeps exactly the access they had (tested).
- The session stores only `user` and `user_id`; old cookies that still carry `role` keep working and the value is ignored.
- `init.sql` now records every `app/migrations/*.sql` version in `schema_migrations`, so a fresh database no longer replays 0004/0015 (which read `users.role`). Existing migrations are untouched.
- Draft PR: https://github.com/nickkmm/plates/pull/90

## Changes
- `app/migrations/0018_user_roles.sql` (new): creates `user_roles` + `user_roles_role_idx` as the issue gives them; a `DO` block, only while `users.role` exists in `current_schema()`, inserts one row per user (`ON CONFLICT DO NOTHING`, `assigned_by` NULL) and drops `users.role`.
- `init.sql`: `users` without `role`; `user_roles` and its index right after `users` (unnamed FKs, same names as the migration); updated comments; after `CREATE TABLE schema_migrations`, an `INSERT` of the 18 migration filenames with a comment explaining why and that every future migration must add its row. The backfill versions are not inserted.
- `app/core/permissions.py`: `effective_permissions(roles, stored)`; `roles` materialised once via `set(roles)`; docstrings updated.
- `app/core/db/users.py`: `UserRecord.roles` (sorted, `COALESCE(array_agg ...)` in every select); `create_user(roles=..., assigned_by=None)`; `update_user(active=)`; new `set_user_roles`, `assign_role`, `unassign_role`, `get_user_permissions` (one query), `list_users_with_role`; `count_active_users_with_role` joins `user_roles`.
- `app/core/db/__init__.py`: imports and `__all__` for the five new functions.
- `app/core/db/roles.py`: `user_count` from `user_roles`; `RoleRecord`, `delete_role` and module docstrings.
- `app/web/auth.py`: `current_user()` uses `effective_permissions(user.roles, db.get_user_permissions(conn, user.id))` in the same connection/`flask.g` cache; `_session_is_stale()` no longer needs `role`; `_check_session()` drops the role sync; `ROLES`, `ROLE_PRODUCCION`, `ROLE_PUNTO_VENTA` removed (`ROLE_ADMIN` kept); docstrings rewritten.
- `app/web/blueprints/auth_routes.py`: no `session["role"]`; log line `login: user=%s roles=%s`.
- `app/schema_migrate.py::backfill_users`: inserts `users(username, password_hash)` then the `user_roles` row with the same `AUTH_ROLES`/admin-default rule.
- Tests: `tests/conftest.py` (`user_roles` in `TABLES`, seeds user_roles per test user); signature updates in `test_orders.py`, `test_ingestion.py`, `test_roles.py`; new cases in `tests/core/db/test_users.py`, `tests/core/test_permissions.py`, `tests/web/test_permissions.py` (web tests a-d and the access-unchanged test); session tests rewritten in `test_auth.py`/`test_login.py` (`TestRoles` deleted); `tests/test_schema_migrate.py` gains `_throwaway_schema`, moves 0004/0015 direct-apply tests, `TestTranslateRoleValuesToEnglish` and `TestMain.test_rerun_is_a_no_op` into throwaway schemas, adds `TestUserRoles` (0018 + fresh init.sql), `user_roles` in `_APP_TABLES`, the `user_roles_role_fkey` check, and `TestBackfillUsers` reading `user_roles`.
- Docs (Spanish): `docs/DATABASE.md` (`### user_roles`, mermaid, NIST note, line-30 note, `### roles`, `init.sql` versions), `docs/WEB.md` (`## Roles y permisos`), `docs/CORE.md` (`db/users.py`, `effective_permissions`, `db/roles.py`), `AGENTS.md` (Schema bullet), `README.md` (one line that listed `role` on `users`).

## Acceptance criteria
1. [x] 0018 exists and its test passes: `pytest tests/test_schema_migrate.py -k 0018` → `TestUserRoles::test_0018_backfills_user_roles_and_drops_users_role` passed (one row per user equal to old role, `assigned_by` NULL, `users.role` gone, second apply leaves rows identical).
2. [x] `TestSchemaInvariant::test_migrated_old_schema_matches_fresh_init_sql` passes with `"user_roles"` in `_APP_TABLES` (full suite).
3. [x] `TEST_POSTGRES_DB=plates_test_7_fresh` on a database that did not exist (`pg_database` count 0 beforehand): `pytest tests/core/db/test_users.py tests/core/test_permissions.py tests/core/db/test_roles.py` → 59 passed; then `tests/test_schema_migrate.py tests/web/test_permissions.py tests/web/test_auth.py tests/web/test_login.py` on the same DB → 130 passed (includes `test_fresh_init_sql_records_every_migration_and_applies_none`). Test DB dropped afterwards.
4. [x] `git diff origin/main --stat -- app/migrations/` → only `app/migrations/0018_user_roles.sql | 38 +++`.
5. [x] Route-access tests pass (`test_roles.py`, `test_permissions.py`, `test_auth.py::TestPermissionRequired`, ...). `git diff origin/main -- tests/web/test_roles.py` is empty.
6. [x] `tests/web/test_permissions.py::TestSeveralRoles` has (a) `test_permissions_are_the_union_of_the_roles`, (b) `test_assigning_and_unassigning_admin_applies_on_the_next_request`, (c) `test_user_whose_roles_are_all_removed_gets_the_403_page`, (d) `test_old_cookie_with_role_still_works_and_the_role_in_it_is_ignored`; all pass.
7. [x] `TestAccessUnchanged::test_every_seeded_user_keeps_the_access_of_their_single_role` passes for `testuser`, `produccionuser`, `puntoventauser`.
8. [x] The grep prints only comments in `app/migrations/0004`, `0015` (plus 0015's own `users_role_fkey` guard SQL, which the grep's `users.role` regex matches) and `0018`, and docstrings/historical fixtures in `tests/test_schema_migrate.py`. Listed in the PR body.
9. [x] `grep -n "ROLES\b\|ROLE_PRODUCCION\|ROLE_PUNTO_VENTA" app/web/auth.py` → only `AUTH_ROLES` lines; `grep -n ROLE_ADMIN app/web/auth.py app/schema_migrate.py` matches both files.
10. [x] `tests/web/test_login.py::test_login_stores_user_and_user_id_but_no_role_in_session` asserts `"role" not in sess`; `test_successful_login_is_logged` asserts the exact message `login: user=testuser roles=admin`. Both pass.
11. [x] `pytest tests/core/test_permissions.py tests/core/db/test_users.py tests/core/db/test_roles.py` → 59 passed.
12. [x] `.venv/Scripts/python -c "from app.core.db import set_user_roles, assign_role, unassign_role, get_user_permissions, list_users_with_role; print('ok')"` → `ok`.
13. [x] `docs/DATABASE.md` has `### user_roles`, `users ||--o{ user_roles`, `roles ||--o{ user_roles` and no `roles ||--o{ users`; `docs/WEB.md` describes several roles per user and the union; `docs/CORE.md` lists `set_user_roles` and `get_user_permissions`; `AGENTS.md` mentions the `init.sql` `schema_migrations` row.

## Validation
- `ruff check app/` → passed. `mypy app/` → passed (46 files).
- `pytest -q -x` on `plates_test_7` (existing DB, migrated through 0018 by `_db_ready`) → 1651 passed.
- Targeted runs on the brand-new `plates_test_7_fresh` → 59 + 130 passed.
- `bash factory/checks.sh` with `TEST_POSTGRES_DB=plates_test_7 TEST_REQUIRE_DB=1` → passed (ruff, mypy, 1651 tests, shell and factory script checks; exit 0).
- No page check: there is no template change; every visible effect (403 page text, nav items, redirects) is asserted by the web tests.

## Log
### Round 1
- Set up `.venv` in the worktree, implemented the spec in three commits (code, tests, docs), pushed, and opened draft PR #90.
- Added `_throwaway_schema` in `tests/test_schema_migrate.py` to reuse the existing throwaway-schema pattern for the moved historical tests. The 0004 throwaway schemas use `_PRE_0015_USERS_SQL` + 0015 (so `roles` exists and `users.role` has its FK, as in production when 0004 last mattered).
- `TestMain.test_rerun_is_a_no_op` now runs `main()` twice in a schema built from `_OLD_INIT_SQL`, keeps its assertions (users unchanged after the second run, each version recorded once) and also checks every migration file got recorded.
- Also updated one `README.md` line and the `production_status` note in `docs/DATABASE.md` that referred to `users.role`, and the `docs/CORE.md` `db/roles.py` paragraph that said auth reads `get_role_permissions` per request.
