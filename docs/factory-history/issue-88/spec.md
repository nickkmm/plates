# Several roles per user through a user_roles table

> Issue #88: Let a user hold several roles through a user_roles table

## Summary
- A new `user_roles(user_id, role, assigned_at, assigned_by)` table becomes the only place role assignments live. Migration `0018` backfills it from `users.role` and then drops that column.
- A user's permissions become the union of the permissions of all their roles, and `admin` in any of them still grants everything. Every existing user keeps exactly the access they had.
- The session stops storing the role. `current_user()` reads every role on each request, and old cookies that still carry `role` keep working.
- The issue missed one problem, which this spec covers. A fresh `init.sql` database still runs every migration file, including 0004 and 0015, which read `users.role`. So `init.sql` must record the migrations it already contains (see Requirement 3 and Assumption 1).
- Tests and the Spanish docs (`DATABASE.md`, `WEB.md`, `CORE.md`) are updated. The historical migration tests move into throwaway schemas.

## Context
- **Schema.** `init.sql:185-192` creates `users` with `role TEXT NOT NULL CONSTRAINT users_role_fkey REFERENCES roles(name) ON UPDATE CASCADE`. `roles`, `permissions` and `role_permissions` are created before it (`init.sql:105-160`). `schema_migrations` is created at `init.sql:289`, but `init.sql` records no versions in it. Migrations live in `app/migrations/0001..0017`; the next one is `0018`.
- **Migration runner.** `app/schema_migrate.py::apply_sql_migrations` applies every file not yet listed in `schema_migrations`. `main()` then runs `run_backfills`, and `backfill_users` (around line 202) inserts `users(username, password_hash, role)` from `AUTH_USERS`/`AUTH_ROLES`, defaulting to `ROLE_ADMIN`. A fresh volume runs `init.sql` (`docker-compose.yml`) and then `python -m app.schema_migrate` (`.github/workflows/deploy.yml:50`). The tests do the same: `tests/conftest.py::_db_ready` applies `init.sql` to a new database and then calls `schema_migrate.main()`. So today, every migration file runs on top of a fresh `init.sql` schema. Two of them fail once `users.role` is gone:
  - `0004_translate_role_values_to_english.sql` runs `UPDATE users SET role ...`.
  - `0015_roles_and_permissions.sql` runs `SELECT DISTINCT role ... FROM users` and adds `users_role_fkey`.
- **Permissions.** `app/core/permissions.py::effective_permissions(role: str, stored)` takes a single role. `ADMIN_ROLE = "admin"`.
- **Users data layer.** `app/core/db/users.py` defines `UserRecord` (with `role`), `_SELECT_COLUMNS`, `get_user_by_username`, `get_user_by_id`, `create_user(role=)`, `list_users`, `update_user(role=, active=)`, `set_user_password` and `count_active_users_with_role` (which reads `users.role`). Exports live in `app/core/db/__init__.py` (imports around line 175, `__all__` around line 241).
- **Roles data layer.** In `app/core/db/roles.py`, `_SELECT_ROLES.user_count` counts `users u WHERE u.role = r.name`, and the `delete_role` docstring cites `users_role_fkey`. `get_role_permissions` stays.
- **Web.**
  - In `app/web/auth.py`, `current_user()` calls `effective_permissions(user.role, db.get_role_permissions(conn, user.role))` and caches the result on `flask.g`.
  - `_session_is_stale()` requires `role` in the session, and `_check_session()` keeps `session["role"]` in sync with the database.
  - `ROLES`, `ROLE_PRODUCCION` and `ROLE_PUNTO_VENTA` are used only inside `auth.py` and by `tests/web/test_auth.py::TestRoles`. `ROLE_ADMIN` is imported by `app/schema_migrate.py`.
  - `app/web/blueprints/auth_routes.py::login_post` writes `session["role"]` and logs `login: user=%s role=%s`.
- **Navigation and routes.** In `app/web/navigation.py::NAV_ITEMS`, `Inventario` needs `inventory.manage` (`/inventory`), `Registros` needs `records.view` (`/records`) and `Precios` needs `pricing.manage`. The pricing page is served at **`/precios`** (`app/web/blueprints/pricing.py:47`); there is no `/pricing` route. The 403 text for a user with no permissions is in `tests/web/test_permissions.py::NO_PERMISSIONS_TEXT`.
- **Tests that depend on the old shape.**
  - `tests/conftest.py`: `TABLES`, `_TEST_USERS` (username, hash, role), and `clean_db`, which runs `TRUNCATE ... CASCADE` and then `INSERT INTO users (username, password_hash, role)`.
  - `tests/test_schema_migrate.py`:
    - `_APP_TABLES`.
    - `test_0004_..._safe_to_apply_twice_directly` and `test_0015_..._safe_to_apply_twice_directly`, which run on `public`.
    - `TestMain.test_rerun_is_a_no_op`, which deletes every `schema_migrations` row on `public` and reruns `main()`. That replays 0004 and 0015.
    - `TestTranslateRoleValuesToEnglish`, which runs on `public`.
    - `TestRolesAndPermissions.test_users_role_fkey_cascades_on_update`, which reads `public.users`.
    - `TestBackfillUsers`, which reads `users.role`.
    - The throwaway-schema pattern to reuse is `TestRolesAndPermissions.test_unknown_role_is_backfilled_and_fk_added` together with `_PRE_0015_USERS_SQL`.
  - Other affected tests:
    - `tests/core/db/test_users.py`, `tests/core/db/test_roles.py`, `tests/core/db/test_orders.py:63` and `tests/core/test_ingestion.py:145,728` call `create_user(role=...)`.
    - `tests/web/test_auth.py` writes `sess["role"]`, calls `update_user(role=...)`, contains `TestRoles`, the two `test_session_missing_role_redirects_to_login` tests and `test_role_change_updates_the_session_role`.
    - `tests/web/test_login.py:123-138` asserts `sess["role"]`.
    - `tests/web/test_permissions.py` uses `create_user(role=)` and `update_user(role=)`.
- **Docs.**
  - `docs/DATABASE.md`: the note at line 30, the mermaid diagram at line 45 (`roles ||--o{ users`), the `### users` table (line 331) and `### roles` (lines 339-354).
  - `docs/WEB.md`: `## Roles y permisos` (lines 60-110).
  - `docs/CORE.md`: the `db/users.py` table (around line 478) and `effective_permissions` (line 507).

## Requirements
1. **Migration `app/migrations/0018_user_roles.sql`** (header comment in English, safe to run twice):
   - It creates `user_roles` and `user_roles_role_idx` exactly as the issue gives them: `user_id` FK to `users(id)` `ON DELETE CASCADE`; `role` FK to `roles(name)` `ON UPDATE CASCADE` with no `ON DELETE`; `assigned_at TIMESTAMP NOT NULL DEFAULT now()`; `assigned_by` FK to `users(id)` `ON DELETE SET NULL`; PK `(user_id, role)`.
   - A `DO` block then runs only while `users.role` exists. It checks `information_schema.columns` with `table_schema = current_schema()`, runs `INSERT INTO user_roles (user_id, role) SELECT id, role FROM users ON CONFLICT DO NOTHING`, then `ALTER TABLE users DROP COLUMN role`.
   - Backfilled rows have `assigned_by` NULL.
2. **`init.sql`.**
   - `users` has no `role` column.
   - `user_roles` and its index are created right after `users`, with the same definitions and unnamed FKs, so Postgres names them as the migration does: `user_roles_pkey`, `user_roles_user_id_fkey`, `user_roles_role_fkey`, `user_roles_assigned_by_fkey`.
   - Comments that mention `users.role` (the `roles` block at lines 105-111 and the `users` block) are updated.
3. **A fresh `init.sql` database must still migrate.**
   - Right after `CREATE TABLE schema_migrations`, `init.sql` inserts one row for every `app/migrations/*.sql` filename (`0001_pos_fields.sql` … `0018_user_roles.sql`). Its comment explains that `init.sql` already contains every migration's schema, so the runner must not replay them; 0004 and 0015 would fail without `users.role`.
   - The comment also says that every future migration must add its row there.
   - The synthetic backfill versions (`0002_backfill_clientes`, `0003_backfill_usuarios`) are **not** inserted, so `backfill_users` still runs on a fresh database.
   - Do not edit 0004, 0015 or any other existing migration file.
4. **`app/core/permissions.py`.**
   - Replace `effective_permissions` with `effective_permissions(roles: Iterable[str], stored: Iterable[str]) -> frozenset[str]`. It returns `PERMISSION_CODES` when `ADMIN_ROLE in roles`, otherwise `frozenset(stored) & PERMISSION_CODES`.
   - `roles` must be materialised once, so that passing a generator works.
   - The docstring says that `stored` is the union of the `role_permissions` codes of all the user's roles.
   - Update the module docstring wording ("a role" → "a user's roles").
5. **`app/core/db/users.py`.**
   - `UserRecord` drops `role` and gains `roles: list[str]`, sorted by name. Every select fills it with `COALESCE((SELECT array_agg(ur.role ORDER BY ur.role) FROM user_roles ur WHERE ur.user_id = users.id), '{}') AS roles`.
   - Signatures as in the issue:
     - `create_user(conn, *, username, password_hash, roles, active=True, assigned_by=None) -> int` inserts the user plus one `user_roles` row per distinct role (`ON CONFLICT DO NOTHING`). An unknown role raises `ForeignKeyViolation`.
     - `update_user(conn, user_id, *, active) -> bool`.
     - `set_user_roles(conn, user_id, roles, *, assigned_by=None) -> bool` returns False for an unknown user. It deletes the rows that are not in `roles` and inserts the missing ones with `assigned_by`. Kept rows keep their `assigned_at`.
     - `assign_role(conn, user_id, role, *, assigned_by=None) -> bool` returns True when it added a row.
     - `unassign_role(conn, user_id, role) -> bool` returns True when it removed a row.
     - `get_user_permissions(conn, user_id) -> frozenset[str]` returns the union of stored `role_permissions` codes over the user's roles, in one SQL query, with no `effective_permissions` applied.
     - `list_users_with_role(conn, role) -> list[UserRecord]` returns the users holding `role`, ordered by id.
     - `count_active_users_with_role(conn, role)` counts active users that have a `user_roles` row for `role`.
   - Docstrings stop referring to `session["role"]` and `auth.ROLES`.
6. **`app/core/db/__init__.py`** imports `set_user_roles`, `assign_role`, `unassign_role`, `get_user_permissions` and `list_users_with_role`, and lists them in `__all__`.
7. **`app/core/db/roles.py`.**
   - `user_count` becomes `(SELECT count(*) FROM user_roles ur WHERE ur.role = r.name)::int`.
   - The `delete_role` docstring names `user_roles_role_fkey`.
   - The `RoleRecord` docstring says it counts users holding the role.
8. **`app/web/auth.py`.**
   - `current_user()` uses `permissions.effective_permissions(user.roles, db.get_user_permissions(conn, user.id))` inside the same single connection and the same `flask.g` cache.
   - `_session_is_stale()` returns `not session.get("user") or "user_id" not in session`.
   - `_check_session()` no longer reads or writes `session["role"]`.
   - Remove `ROLES`, `ROLE_PRODUCCION`, `ROLE_PUNTO_VENTA` and their comment block. Keep `ROLE_ADMIN`.
   - Rewrite the module docstring and the `_redirect_to_login`, `has_permission` and `permission_required` docstrings: a user has several roles, permissions are the union, admin in any role grants everything, role changes apply on the next request, and the session stores `user` and `user_id` only.
9. **`app/web/blueprints/auth_routes.py`.** `login_post` no longer sets `session["role"]`. The log line becomes `current_app.logger.info("login: user=%s roles=%s", user.username, ",".join(user.roles))`.
10. **`app/schema_migrate.py::backfill_users`.**
    - It inserts `users (username, password_hash)` with `ON CONFLICT (username) DO NOTHING`.
    - It then runs `INSERT INTO user_roles (user_id, role) SELECT id, %s FROM users WHERE username = %s ON CONFLICT DO NOTHING`, using the same `roles.get(username, ROLE_ADMIN)` rule.
    - `BACKFILL_USERS_VERSION` does not change.
    - Update its docstring.
11. **`tests/conftest.py`.**
    - `_TEST_USERS` keeps one role per user. `clean_db` inserts the users, then their `user_roles` rows (by username lookup or `RETURNING id`).
    - Add `"user_roles"` to `TABLES`.
    - Update the comments that describe the role column.
12. **Existing tests.**
    - Update every `create_user(..., role=X)` call to `roles=[X]`.
    - Update every `update_user(..., role=..., active=...)` call to `update_user(..., active=...)`. Where a test used `update_user` to *change* a role, use `set_user_roles`.
    - Update every `user.role` assertion to `user.roles`, and every `sess["role"] = ...` write in `_log_in_as` and similar helpers is removed.
    - Tests that asserted the removed session-role behaviour are rewritten to assert the new behaviour:
      - `tests/web/test_login.py` checks `"role" not in sess`.
      - The two `test_session_missing_role_redirects_to_login` tests now assert that a session with `user` and `user_id` but no `role` is accepted.
      - `test_role_change_updates_the_session_role` asserts only the access change.
    - `TestRoles` in `tests/web/test_auth.py` is deleted together with `auth.ROLES`.
    - In `tests/test_schema_migrate.py`, `TestBackfillUsers` reads roles from `user_roles` (joined to `users`).
    - `TestRolesAndPermissions.test_users_role_fkey_cascades_on_update` becomes a check that `user_roles_role_fkey` on `public.user_roles` is `FOREIGN KEY (role) REFERENCES roles(name) ON UPDATE CASCADE`.
13. **Historical migration tests move off `public`.**
    - `test_0004_..._safe_to_apply_twice_directly`, `test_0015_..._safe_to_apply_twice_directly` and both `TestTranslateRoleValuesToEnglish` tests run in throwaway schemas. Each schema has a `roles` table (for 0004's legacy Spanish values) and the historical `users` table with `role`, following the `TestRolesAndPermissions` and `_PRE_0015_USERS_SQL` pattern. Every schema is dropped in `finally`.
    - `TestMain.test_rerun_is_a_no_op` must no longer replay every migration on `public`. It runs `schema_migrate.main()` twice inside a throwaway schema built from `_OLD_INIT_SQL`, the same technique as `TestSchemaInvariant`. It keeps its assertions that the second run adds no users and records each version once.
    - Any other test that fails because it replays 0004 or 0015 on `public` is moved the same way.
14. **New tests.**
    - `_APP_TABLES` gains `"user_roles"`.
    - **0018 migration test** (throwaway schema): `roles`, `permissions` and `role_permissions` plus a pre-0018 `users` table with `role` (`users_role_fkey` included), with users holding `admin`, `point_of_sale`, `production` and a custom role. After applying 0018:
      - each user has exactly one `user_roles` row, equal to its old role, with `assigned_by IS NULL`;
      - `users.role` no longer exists in that schema;
      - a second apply raises nothing and leaves `user_roles` unchanged.
    - **Fresh init.sql test**: a throwaway schema built from `init.sql` lists, in `schema_migrations`, exactly the set of `app/migrations/*.sql` basenames. Running `apply_sql_migrations` on it returns `[]`.
    - **Access unchanged**: for each `_TEST_USERS` entry, compute two sets and assert they are equal:
      - "before", the old single-role rule: `PERMISSION_CODES` if the role is `admin`, else `db.get_role_permissions(conn, role) & PERMISSION_CODES`;
      - "after", `effective_permissions(user.roles, db.get_user_permissions(conn, user.id))`.
    - **`tests/core/test_permissions.py`**: `admin` in a list of several roles gets every code; `[]` with no stored codes gets `frozenset()`; unknown stored codes are still dropped.
    - **`tests/core/db/test_users.py`**:
      - `set_user_roles` replaces the set and preserves `assigned_at` of a kept row; it returns False for an unknown user.
      - `assign_role` returns True, then False when the user already has the role.
      - `unassign_role` returns True, then False.
      - `get_user_permissions` is the union of two roles' stored codes.
      - `count_active_users_with_role` ignores inactive users and users without the role.
      - `list_users_with_role` returns only holders, ordered by id.
      - `create_user` with an unknown role raises `ForeignKeyViolation`.
      - `UserRecord.roles` comes back sorted.
    - **Web tests** (in `tests/web/test_permissions.py`):
      - (a) A user with roles `production` and a custom role holding only `inventory.manage` gets 200 on `/inventory` and `/records` and 403 on `/precios`. The nav HTML contains `Inventario` and `Registros`.
      - (b) `assign_role(..., "admin")` for that user, committed, makes the same logged-in client get 200 on `/precios` on the next request. `unassign_role(..., "admin")` brings back 403 on the next request.
      - (c) A user whose roles are all removed (`set_user_roles(..., [])`) gets 403 on `/`, and the page contains `NO_PERMISSIONS_TEXT`.
      - (d) A session cookie that also carries `role` (set via `session_transaction`, e.g. `sess["role"] = "production"` next to valid `user` and `user_id`) still reaches a permitted route with 200, and access follows the database roles, not the cookie value.
15. **Docs, written in Spanish.**
    - `docs/DATABASE.md`:
      - Remove `role` from `### users`.
      - Add a new `### user_roles` section: its columns, the 0018 backfill from `users.role`, and why `role` has no `ON DELETE`.
      - Mermaid: add `users ||--o{ user_roles` and `roles ||--o{ user_roles`, and drop `roles ||--o{ users`.
      - Add the NIST core RBAC note the issue gives.
      - Fix the line-30 note and the `### roles` text that says `users.role`.
      - Add a sentence on `init.sql` recording the migration versions it already contains.
    - `docs/WEB.md` `## Roles y permisos`: several roles per user, permissions are the union, `admin` in any role means everything, and the session stores `user` and `user_id` but not the role. Old cookies with `role` are still valid.
    - `docs/CORE.md`: the `db/users.py` table (new and changed functions) and the `effective_permissions` paragraph.
    - `AGENTS.md`: under "Schema", add that a new migration also adds its version row to `init.sql`'s `schema_migrations` insert.

## Acceptance criteria
1. `app/migrations/0018_user_roles.sql` exists. The new 0018 migration test in `tests/test_schema_migrate.py` passes: each user gets one `user_roles` row equal to its old role with `assigned_by` NULL, `users.role` is gone, and a second apply changes nothing. Check with `pytest tests/test_schema_migrate.py -k 0018`.
2. `TestSchemaInvariant::test_migrated_old_schema_matches_fresh_init_sql` passes with `"user_roles"` in `_APP_TABLES`.
3. A brand-new test database works. Run `TEST_POSTGRES_DB=plates_test_7_fresh pytest tests/core/db/test_users.py` against a database that did not exist before: the tests run and pass, they don't skip or error in `_db_ready`. The fresh init.sql test from Requirement 14 also passes.
4. `git diff origin/main -- app/migrations/` shows only the new `0018_user_roles.sql`, with no change to any existing migration file.
5. The pre-existing `tests/web/` tests that check which routes `admin`, `point_of_sale` and `production` can or cannot open (`test_roles.py`, `test_permissions.py`, `test_auth.py::TestPermissionRequired` and the others) pass, changed only in fixtures and call signatures. `git diff origin/main -- tests/web/test_roles.py` shows no changed status-code expectation.
6. The four web tests (a) to (d) from Requirement 14 exist in `tests/web/test_permissions.py` and pass.
7. The access-unchanged test passes: for `testuser`, `produccionuser` and `puntoventauser` the old single-role permission set equals the new union.
8. `grep -rn "users.role\|session\[\"role\"\]\|user\.role\b" app/ tests/` prints hits only in comments of `app/migrations/*.sql` (0004, 0015, 0018) and in historical-schema fixtures or comments inside `tests/test_schema_migrate.py`. The PR body lists each remaining hit with its reason.
9. `grep -n "ROLES\b\|ROLE_PRODUCCION\|ROLE_PUNTO_VENTA" app/web/auth.py` matches only `AUTH_ROLES` text, and `grep -n "ROLE_ADMIN" app/web/auth.py app/schema_migrate.py` still matches both files.
10. Logging in as `testuser` through `POST /login` leaves a session without a `role` key (asserted in `tests/web/test_login.py`). The app log line reads `login: user=testuser roles=admin`.
11. `pytest tests/core/test_permissions.py tests/core/db/test_users.py tests/core/db/test_roles.py` passes, including the new cases in Requirement 14.
12. `from app.core.db import set_user_roles, assign_role, unassign_role, get_user_permissions, list_users_with_role` works. Check with `python -c "..."` from the worktree.
13. The docs contain the new content:
    - `docs/DATABASE.md` has a `### user_roles` section and the mermaid lines `users ||--o{ user_roles` and `roles ||--o{ user_roles`, and no `roles ||--o{ users` line.
    - `docs/WEB.md` `## Roles y permisos` describes several roles per user and the union of their permissions.
    - `docs/CORE.md` lists `set_user_roles` and `get_user_permissions`.
    - `AGENTS.md` mentions the `init.sql` `schema_migrations` row.

## Out of scope
- The users and roles admin screens (#65, #67), and any template or UI change beyond what follows from the new permission computation.
- Role hierarchies, separation-of-duty constraints, choosing which roles are active per session, and per-user permissions outside roles.
- Changing which permission any route requires, or the seeded `role_permissions` of any role.
- Editing any existing migration file (0001–0017), including adding guards to 0004 or 0015.
- Changing `BACKFILL_USERS_VERSION`, `load_users()`/`load_roles()`, or the `AUTH_USERS`/`AUTH_ROLES` env-var parsing.
- Removing `get_role_permissions` from `app/core/db/roles.py`. It stays for the "before" side of the access test and for future screens.
- Validating role names in Python. The `user_roles_role_fkey` FK stays the only check.
- Touching `factory/checks.sh` or `factory/config.json`.

## Assumptions
1. **`init.sql` records every `app/migrations/*.sql` version in `schema_migrations`.** Without this, `_db_ready`, CI and a fresh docker volume all replay 0004 and 0015 on a schema with no `users.role` and fail. The issue forbids editing those files. Recording the versions is the smallest fix that keeps them untouched, but it adds a convention for future migrations. The approver should confirm this with a human, because the alternative is to add column guards to 0004 and 0015, which the issue forbids.
2. **The issue's `/pricing` means `/precios`.** The pricing nav item (`pricing.manage`) is served at `/precios`, and no `/pricing` route exists.
3. **Old cookies with `role` keep working.** "Keeps working" for test (d) means the request succeeds and access follows the database roles; the stale `role` value in the cookie is ignored. The `role` key does not need to be removed from old cookies.
4. **Session tests are rewritten, not just re-signatured.** Tests whose subject was the removed behaviour (`session["role"]` written at login, a session without `role` treated as stale, the role synced into the session) are rewritten to assert the new behaviour. The issue itself changes that behaviour, so criterion 3 of the issue ("only fixture/call changes") applies to the route-access tests, not to these.
5. **`TestRoles` in `tests/web/test_auth.py` is deleted.** It only exists to pin `auth.ROLES`, which the issue removes.
6. **`TestMain.test_rerun_is_a_no_op` moves to a throwaway schema built from `_OLD_INIT_SQL`.** Wiping `schema_migrations` on `public` replays 0004 and 0015 against the new schema. The test's intent, an old database migrated twice, is kept.
7. **Web tests (a) to (d) go in `tests/web/test_permissions.py`.** That file already holds the per-request role and permission tests and the `_create_and_log_in` helper.
