# Store roles and permissions in the database

> Issue #62: Store roles and permissions in the database

## Summary
- Adds the data layer for roles: a pure permission catalog (`app/core/permissions.py`), the `roles` and `role_permissions` tables with seed data, and a foreign key from `users.role` to `roles(name)`.
- New migration `0015_roles_and_permissions.sql` (safe to re-run) and the same end schema in `init.sql`. Any unknown `users.role` value is backfilled as a role with no permissions so the FK can never fail on production data.
- New `app/core/db/roles.py` CRUD plus `update_user`, `set_user_password` and `count_active_users_with_role` in `app/core/db/users.py`, all re-exported from `app.core.db`.
- No route, decorator, session or template changes: the app behaves exactly as today. Two existing migration tests need a small fix because the new FK rejects their old Spanish role values.
- Spanish docs updated in `docs/DATABASE.md` and `docs/CORE.md`.

## Context
- **Roles today.** `app/web/auth.py` defines `ROLE_ADMIN`, `ROLE_PRODUCCION`, `ROLE_PUNTO_VENTA`, `ROLES`, `FULL_ACCESS_ROLES` and `POS_ROLES`, and the decorators `login_required`, `consulta_allowed` and `pos_allowed` check `session["role"]` against them. Nothing in this file changes in this job.
- **Users table.** `init.sql` creates `users` at about line 109 (`role TEXT NOT NULL`, free text, no FK). `production_orders.created_by` and `declarations.reviewed_by`/`notarized_received_by` reference `users(id)`. `app/core/db/users.py` has `UserRecord(id, username, password_hash, role, active)`, `_SELECT_COLUMNS`, `get_user_by_username`, `get_user_by_id`, `create_user` and `list_users`.
- **Lookup-table pattern.** `plate_types` is created in `init.sql` before `plates`, seeded inline, and referenced by FK. Valid values also live in Python (`app/core/normalize.py::PLATE_TYPES`), and `app/core/db/pricing.py::set_price` validates against them before writing. Follow the same pattern for permission codes: validate in Python, no CHECK.
- **db package.** `app/core/db/__init__.py` states the transaction contract (functions get an open `conn` and never commit or roll back). Its docstring lists every submodule, and it re-exports each public name explicitly in its imports and in `__all__`. Records are dataclasses read with `class_row` (see `UserRecord`, `CustomerRecord`).
- **Migrations.** `app/schema_migrate.py` applies `app/migrations/*.sql` in filename order, each file in one transaction, and records it in `schema_migrations`. It then runs `backfill_users`, which inserts `AUTH_USERS` entries with role `AUTH_ROLES.get(username, "admin")`. The latest file is `0014_declaration_used_plates_photo.sql`, so the new file is `0015_roles_and_permissions.sql`.
- **Tests.**
  - `tests/conftest.py`: `_db_ready` builds `plates_test` from `init.sql`, then runs `schema_migrate.main()`. `clean_db` truncates `TABLES` (which include `users`, but not static lookup tables like `plate_types`), reseeds three test users with roles `admin`/`production`/`point_of_sale`, and wraps everything in savepoints that are rolled back.
  - `tests/test_schema_migrate.py`:
    - `TestSchemaInvariant` builds a fresh schema from `init.sql` and an "old" schema plus every migration, each in its own throwaway Postgres schema selected through `search_path`. It compares columns and constraints for the tables in `_APP_TABLES`.
    - `TestRenameToEnglish` shows the throwaway-schema technique for testing one migration on hand-made data.
    - `TestTranslateRoleValuesToEnglish` inserts users with roles `produccion`/`punto_venta` into the public `users` table. That insert will violate the new FK.
  - `tests/core/db/test_users.py` holds the users data tests.
- **Docs.**
  - `docs/DATABASE.md` has the mermaid `erDiagram` (around line 43), a column table for each table, and a `users.role` row that currently says "sin FK ni `CHECK`".
  - `docs/CORE.md` has a section for each module and each `db/` submodule, including `### db/users.py (Fase 2)` (around line 476).

## Requirements
1. **Permission catalog.** Create `app/core/permissions.py`: pure Python, no Flask and no database imports, strictly typed like the rest of `app/core`. It exposes:
   - `PERMISSIONS: tuple[tuple[str, str], ...]`: the 13 `(code, label)` pairs from the issue table, in that order and spelled exactly as given (`orders.capture`/`Registrar y editar órdenes` … `users.manage`/`Gestionar usuarios y roles`);
   - `PERMISSION_CODES: frozenset[str]`, built from `PERMISSIONS`;
   - `ADMIN_ROLE = "admin"`;
   - `effective_permissions(role: str, stored: Iterable[str]) -> frozenset[str]`. It returns `PERMISSION_CODES` when `role == ADMIN_ROLE`, whatever `stored` holds. For any other role it returns `frozenset(stored) & PERMISSION_CODES`.
2. **Migration file.** Create `app/migrations/0015_roles_and_permissions.sql`, safe to run twice in a row on the same database. In order, it:
   - creates `roles (name TEXT PRIMARY KEY, label TEXT NOT NULL UNIQUE, is_system BOOLEAN NOT NULL DEFAULT false, created_at TIMESTAMP DEFAULT now())` with `IF NOT EXISTS`;
   - creates `role_permissions (role TEXT NOT NULL REFERENCES roles(name) ON DELETE CASCADE ON UPDATE CASCADE, permission TEXT NOT NULL, PRIMARY KEY (role, permission))` with `IF NOT EXISTS`;
   - seeds the three system roles `('admin','Administrador',true)`, `('point_of_sale','Punto de venta',true)` and `('production','Producción',true)` with `ON CONFLICT DO NOTHING`;
   - seeds the permission rows with `ON CONFLICT DO NOTHING`:
     - `admin`: all 13 codes;
     - `point_of_sale`: `orders.capture`, `cash_summary.view`, `declarations.review`, `records.view`, `production.view`, `production.deliver`;
     - `production`: `records.view`, `production.view`, `production.manage`, `production.deliver`;
   - inserts every distinct `users.role` value missing from `roles` as `(name, label = name, is_system = false)`, with no permission rows;
   - adds `CONSTRAINT users_role_fkey FOREIGN KEY (role) REFERENCES roles(name) ON UPDATE CASCADE` (no ON DELETE clause) inside a `DO` block. The block skips the add when the constraint already exists **on the `users` table that the current `search_path` resolves to**, for example by checking `pg_constraint WHERE conname = 'users_role_fkey' AND conrelid = to_regclass('users')`. A check on `conname` alone would find the constraint in `public` and silently skip it in `TestSchemaInvariant`'s throwaway schema.

   Add a leading comment in the style of the other migration files.
3. **init.sql.** It reaches the same end schema:
   - `roles` and `role_permissions` are created (with their seed rows, `ON CONFLICT DO NOTHING`) before `users`;
   - `users.role` gets the FK named `users_role_fkey` with `ON UPDATE CASCADE`;
   - a short comment points to the migration and `app/core/permissions.py`.
4. **Schema-invariant coverage.** Add `"roles"` and `"role_permissions"` to `_APP_TABLES` in `tests/test_schema_migrate.py`, so `TestSchemaInvariant` compares them, and keep it passing.
5. **Repair the 0004 tests.** Change `TestTranslateRoleValuesToEnglish` so its inserts of `produccion`/`punto_venta` users no longer violate the new FK, while it still tests 0004 the same way. For example, insert those two names into `roles` (`ON CONFLICT DO NOTHING`) before inserting the users; `clean_db` rolls the inserts back. Do not weaken what the test asserts.
6. **Migration tests** in `tests/test_schema_migrate.py`:
   - Re-applying `0015_roles_and_permissions.sql` directly on the already-migrated public schema raises nothing and changes no row counts in `roles`/`role_permissions`.
   - After migration, `roles` holds exactly the three seeded system rows, and `role_permissions` holds exactly the seeded sets listed in requirement 2.
   - A `legacy_role` test in a throwaway schema (same technique as `TestRenameToEnglish`):
     - create a `users` table shaped as it was before 0015, with users whose roles are `admin` and `legacy_role`;
     - run the 0015 SQL;
     - assert that `legacy_role` exists with `label = 'legacy_role'`, `is_system = false` and no permission rows, and that `users_role_fkey` exists on that schema's `users`;
     - run the SQL again with no error.
   - `users_role_fkey` on public `users` has `ON UPDATE CASCADE`: `pg_get_constraintdef` contains `ON UPDATE CASCADE`.
7. **backfill_users on a fresh database.** Add a test that `schema_migrate.backfill_users` runs without error against a database whose schema came from the current `init.sql`. Inside the test, `AUTH_USERS`/`AUTH_ROLES` map one user to the default (`admin`) and one to each of `production` and `point_of_sale`. The test asserts the rows are inserted. Do not change `backfill_users` itself.
8. **Roles data module.** Create `app/core/db/roles.py` following the conn contract (never commits or rolls back):
   - `@dataclass RoleRecord(name: str, label: str, is_system: bool, permissions: list[str], user_count: int)`. `permissions` holds the stored codes sorted alphabetically. `user_count` counts every `users` row with that role, active or not.
   - `list_roles(conn) -> list[RoleRecord]`: system roles first, then by `label`.
   - `get_role(conn, name) -> RoleRecord | None`.
   - `get_role_permissions(conn, name) -> frozenset[str]`: the stored codes, or an empty frozenset for an unknown role. It does not apply `effective_permissions`.
   - `create_role(conn, *, name, label, permissions: Iterable[str]) -> None`: inserts a role with `is_system = false` and its permission rows. It raises `ValueError` before writing anything if any code is not in `PERMISSION_CODES`. A duplicate name or label raises psycopg's `UniqueViolation`; no pre-check.
   - `update_role(conn, name, *, label, permissions: Iterable[str]) -> bool`: sets the label and replaces the permission set (delete the role's rows, then insert the new ones). It applies the same `ValueError` validation before writing, and returns `False` without writing when the role doesn't exist.
   - `delete_role(conn, name) -> bool`: deletes the role, and its permission rows by cascade. It returns `False` when nothing was deleted. If any user still has the role, the FK raises psycopg's `ForeignKeyViolation`; no pre-check.
9. **Users data additions.** In `app/core/db/users.py`:
   - add `created_at: datetime | None = None` as the last field of `UserRecord`, and add `created_at` to `_SELECT_COLUMNS`;
   - `update_user(conn, user_id, *, role: str, active: bool) -> bool`: `False` when there is no such user;
   - `set_user_password(conn, user_id, password_hash: str) -> bool`: `False` when there is no such user;
   - `count_active_users_with_role(conn, role: str) -> int`: counts users with that role and `active = true`.

   None of them commits.
10. **Re-exports.** Re-export `RoleRecord`, `list_roles`, `get_role`, `get_role_permissions`, `create_role`, `update_role`, `delete_role`, `update_user`, `set_user_password` and `count_active_users_with_role` from `app/core/db/__init__.py`, both in the imports and in `__all__`. Add `roles.py` to the submodule list in its docstring.
11. **Tests for the new functions.**
    - New `tests/core/test_permissions.py` (pure, no DB) covers:
      - the catalog has 13 unique codes in issue order, and `PERMISSION_CODES` matches;
      - admin with empty `stored` gets all 13 codes;
      - admin with junk in `stored` still gets exactly the 13;
      - a non-admin role gets `stored` minus unknown codes;
      - a non-admin role with empty `stored` gets an empty set.
    - New `tests/core/db/test_roles.py` covers every function in requirement 8, including:
      - list ordering;
      - `user_count`;
      - an unknown role from `get_role`/`get_role_permissions`;
      - `ValueError` on an unknown code, with no row written;
      - `UniqueViolation` on a duplicate name or label;
      - `update_role` replacing (not merging) permissions, and returning `False` for an unknown role;
      - `delete_role` cascading its permission rows, returning `False` for an unknown role, and raising `ForeignKeyViolation` while a user has the role.
    - Extend `tests/core/db/test_users.py` for `created_at` being populated, `update_user`, `set_user_password` and `count_active_users_with_role` (inactive users not counted), including the unknown-id `False` returns.
12. **Docs**, Spanish prose:
    - `docs/DATABASE.md`:
      - add `roles` and `role_permissions` to the mermaid diagram, with the `roles ||--o{ users` and `roles ||--o{ role_permissions` relations;
      - add a column-table section for each new table;
      - rewrite the `users.role` row to say it is now a FK to `roles(name)` with `ON UPDATE CASCADE`, still storing English identifiers that are never shown, and that the Spanish display name lives in `roles.label`;
      - note the backfill of unknown roles done by migration 0015.
    - `docs/CORE.md`:
      - add a section for `permissions.py` (catalog, `ADMIN_ROLE`, `effective_permissions`, and that the routes don't consume it yet);
      - add a section for `db/roles.py`;
      - add the new functions to the `db/users.py` table.
13. **No behaviour change.** Nothing changes in `app/web/` (auth, decorators, routes, templates, session) or in `app/schema_migrate.py`. `roles`/`role_permissions` are not added to `TABLES` in `tests/conftest.py`: they are static seed data, like `plate_types`.

## Acceptance criteria
1. Running `python -m app.schema_migrate` (or the test session's `_db_ready`) on a database with the current schema applies `0015_roles_and_permissions.sql`. A second run reports nothing new. To verify, `TestApplySqlMigrations::test_rerun_applies_nothing_new` and `TestMain::test_rerun_is_a_no_op` pass.
2. `TestSchemaInvariant::test_migrated_old_schema_matches_fresh_init_sql` passes with `roles` and `role_permissions` in `_APP_TABLES`. To verify, run `pytest tests/test_schema_migrate.py -k SchemaInvariant` and `grep -n '"roles"' tests/test_schema_migrate.py`.
3. The seeded roles and permissions match requirement 2 exactly, checked by the new seed test in `tests/test_schema_migrate.py`.
4. The `legacy_role` test passes: the role is inserted as non-system with `label = 'legacy_role'` and no permissions, `users_role_fkey` exists, and a second run of the SQL succeeds.
5. On the test database, `SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'users_role_fkey'` returns `FOREIGN KEY (role) REFERENCES roles(name) ON UPDATE CASCADE`. A test asserts this.
6. `pytest tests/core/test_permissions.py` passes. It shows admin gets all 13 codes with no stored rows, and that unknown stored codes are dropped for other roles.
7. `pytest tests/core/db/test_roles.py tests/core/db/test_users.py` passes and covers every function in requirements 8 and 9.
8. `TestTranslateRoleValuesToEnglish` and the `backfill_users` tests, including the new fresh-database test, pass.
9. `git diff origin/main --stat -- app/web app/schema_migrate.py tests/web tests/conftest.py` shows no changes. Every login and route test in `tests/web/` passes unchanged.
10. `python -c "from app.core import db; db.list_roles; db.update_user; db.count_active_users_with_role; db.RoleRecord"` runs without error, and `app/core/permissions.py` imports nothing from `flask` or `app.core.db`. To verify the imports, run `grep -nE "flask|app.core.db" app/core/permissions.py`, which should find nothing.
11. `docs/DATABASE.md` contains `roles` and `role_permissions` in the `erDiagram` block and column sections for both tables, and no longer says `users.role` has no FK. `docs/CORE.md` has sections for `permissions.py` and `db/roles.py`. The prose is in Spanish.

## Out of scope
- Any change to `app/web/auth.py` (`ROLES`, `FULL_ACCESS_ROLES`, `POS_ROLES`, decorators, `landing_url`), to the session, routes or templates, or any use of `effective_permissions` by the web layer. That is the next task.
- Any screen, route or form to manage users or roles.
- Renaming or translating the role identifiers `admin`/`point_of_sale`/`production`.
- Changing `app/schema_migrate.py` or `backfill_users`, or validating `AUTH_ROLES` values against `roles`.
- Guards against deleting or editing system roles, or against removing the last active admin, in the data layer. The later management task decides those using `is_system` and `count_active_users_with_role`.
- A CHECK constraint on `role_permissions.permission`, and an ON DELETE action on `users_role_fkey`.
- Editing any already-applied migration (0001 to 0014), or `factory/checks.sh`/`factory/config.json`.
- `docs/WEB.md`'s `## Roles y permisos` section. The runtime access rules described there don't change.

## Assumptions
- **Permissions are sorted alphabetically.** `RoleRecord.permissions` holds the stored codes sorted alphabetically: deterministic and simple. The screen task can reorder them by catalog order.
- **`user_count` includes inactive users.** It counts all users with the role, active or not, because the FK blocks deleting a role that has any user. The active-only count is `count_active_users_with_role`.
- **Validation lives in the data functions.** `create_role`/`update_role` raise `ValueError` on unknown permission codes, because the issue says codes are validated in Python rather than by a CHECK. Same idea as `pricing.set_price` validating against `PLATE_TYPES`.
- **Missing rows return `False`.** `update_role`, `delete_role`, `update_user` and `set_user_password` return `bool` (`False` = no such row) instead of raising. The issue left the return type open, and this lets callers show a 404 without a separate lookup.
- **Data functions don't protect system roles.** They don't block editing or deleting system roles, because the issue only asks for data access and the guards belong with the management screen. The FK already blocks deleting a role that users still have.
- **The 0004 test changes are allowed.** Adjusting `TestTranslateRoleValuesToEnglish` is necessary and not a behaviour change: the FK added here rejects its legacy Spanish role values. Acceptance criterion 5 of the issue ("existing route tests unchanged") covers route tests, not migration tests.
- **`backfill_users` is untouched.** On a fresh database it only writes `admin` or values from `AUTH_ROLES`. An unknown `AUTH_ROLES` value would now fail the FK, which is acceptable: that backfill is already applied in production and the issue rules out behaviour changes there.
