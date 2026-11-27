# Move authentication, users and roles into app/auth

> Issue #113: Move authentication, users and roles into the auth domain

## Summary
- This is a refactor with no behaviour change. It is the second phase 1 step of `docs/MIGRATION_PLAN.md`: login, users, roles and permissions move into a new `app/auth/` package.
- Seven modules move with `git mv`. `app/core/db/users.py` and `roles.py` become an `app/auth/repository/` package, so both files keep their history.
- `app/core/db/__init__.py` stops re-exporting users and roles. Every caller in `app/` and `tests/` imports `app.auth.repository`, and every monkeypatch target follows the code to its new module.
- Every URL, blueprint name, `url_for` endpoint, template, flash and log line stays the same. The strict mypy bar and the docs follow the moved modules.
- This touches access control (the decorator and the session check), so the criteria check that the patched names are the ones the code calls.

## Context
- **Files to move** (all exist on `origin/main`):
  - `app/web/blueprints/auth_routes.py` holds the `auth_routes` blueprint (`/login`, `/logout`) and does `from app.web import auth`.
  - `app/web/blueprints/users.py` holds the `users` blueprint (`/usuarios/*`, roles screens). It does `from app.core import db, user_admin`, `from app.core.db import RoleRecord, UserRecord`, `from app.core.permissions import ADMIN_ROLE` and `from app.web.auth import permission_required`, and it calls `db.list_roles`, `db.create_user`, `db.delete_role` and the rest as module attributes.
  - `app/web/auth.py` holds `load_users`, `load_roles`, `authenticate`, `verify_credentials`, `is_safe_next`, `landing_url`, `current_user`, `has_permission`, `authenticated`, `permission_required` and `ROLE_ADMIN`. It imports `from app.core import db, permissions`, `app.core.db.users.UserRecord` and `app.web.navigation.NAV_ITEMS`, and it calls `db.get_user_by_username`, `db.get_user_by_id` and `db.get_user_permissions`.
  - `app/core/user_admin.py` uses `db.list_roles`, `db.count_active_users_with_role` and `db.list_permissions`, plus `RoleRecord`, `UserRecord` and `ADMIN_ROLE`.
  - `app/core/permissions.py` is pure (stdlib only).
  - `app/core/db/users.py` and `app/core/db/roles.py`: `roles.py` imports `PERMISSION_CODES` from `app.core.permissions`.
  - `app/web/hash_password.py` is the `python -m` CLI, and `pyproject.toml` lists it in `[tool.coverage.run] omit`.
- **`app/core/db/__init__.py`**:
  - It re-exports every function of `roles` (`PermissionRecord`, `RoleRecord`, `create_role`, `delete_role`, `get_role`, `get_role_permissions`, `list_permissions`, `list_roles`, `update_role`) and of `users` (`UserRecord`, `assign_role`, `count_active_users_with_role`, `create_user`, `get_user_by_id`, `get_user_by_username`, `get_user_permissions`, `list_users`, `list_users_with_role`, `set_user_password`, `set_user_roles`, `unassign_role`, `update_user`), with names in `__all__`.
  - Its docstring lists `users.py` and `roles.py`.
- **Other importers in `app/`**:
  - `app/web/app.py` (lines 63-65 and 77, docstring lines 30 and 37-38).
  - `app/schema_migrate.py` line 57 (`from app.web.auth import ROLE_ADMIN, load_roles, load_users`, plus the docstring at line 209).
  - The blueprints `cash_summary`, `customers`, `declarations`, `ingest`, `inventory`, `pricing`, `production` (also `has_permission`), `records` and `review`, which all do `from app.web.auth import permission_required`.
  - `app/web/blueprints/declarations.py` line 308, which calls `db.get_user_by_id`.
- **Tests**:
  - `tests/web/test_auth.py` does `from app.web import auth`.
  - `tests/core/test_permissions.py`, `tests/web/test_permissions.py`, `tests/core/db/test_roles.py` and `tests/test_schema_migrate.py` import `app.core.permissions`. `tests/core/test_user_admin.py` imports `RoleRecord, UserRecord` from `app.core.db`.
  - Many tests call user/role functions through `db.`. Find them with `rg -n "\bdb\.(create_user|get_user_by_username|get_user_by_id|list_roles|get_role|...)" tests`. Among them are `tests/core/db/test_users.py`, `tests/web/test_role_admin.py`, `test_auth.py`, `test_csrf.py` and `test_declarations.py`, and `tests/core/test_{user_admin,declarations,production,ingestion}.py` and `tests/core/db/test_orders.py`.
  - Monkeypatches: `tests/web/test_role_admin.py` line 306 `monkeypatch.setattr(db, "delete_role", ...)`, and lines 327 and 338 `monkeypatch.setattr(user_admin, "validate_role", ...)`.
  - `tests/conftest.py` seeds users with raw SQL and patches `get_conn` on `app.core.db` and `app.shared.db`. Repository functions receive `conn` from the caller, so that fixture needs no new target.
- **mypy**: `pyproject.toml` has `[[tool.mypy.overrides]] module = ["app.core.*", "app.shared.*"]` with `disallow_untyped_defs = true`. `app/web/auth.py` has untyped defs (`_redirect_to_login`, `authenticated(view)`, the decorator internals).
- **Docs and comments that name the old paths**:
  - `.env.example` line 17 and `README.md` line 138 (`python -m app.web.hash_password`).
  - `docs/CORE.md` lines 301-302 and the `db/users.py` / `db/roles.py` sections (about 484-540).
  - `docs/WEB.md` lines 22-23, 73 and 118-148.
  - `docs/DATABASE.md` lines 14, 151, 331-332, 355, 381, 403 and 429.
  - `docs/ARCHITECTURE.md`, `docs/README.md` (the CORE/WEB rows) and the `CLAUDE.md` "Project" bullets.
  - The comment on line 21 of `web.Dockerfile`.
  - Docstrings in the moved files and in `tests/conftest.py`, `tests/web/test_auth.py`, `test_login.py`, `test_users.py`, `test_role_admin.py`, `test_permissions.py`, `test_roles.py`, `test_records_search_pdf.py`, `tests/core/test_user_admin.py`, `tests/core/db/test_users.py`, `test_roles.py` and `tests/test_schema_migrate.py` line 1290.
  - SQL comments in the applied migrations `app/migrations/0004_*.sql`, `0015_*.sql` and `0017_*.sql` also name `app/web/auth.py` and `app/core/permissions.py`. Applied migrations are never edited.
- **Pattern to follow**: #112 (job 017) did the same kind of move into `app/shared/`. It used `git mv`, kept call sites calling through a module attribute, extended the mypy override and updated the docs. `docs/MIGRATION_PLAN.md` ("Target layout", "Where today's code goes" line 131, "Phase 1") is the plan.

## Requirements
1. Create `app/auth/__init__.py` with only a module docstring and no imports, so importing a submodule loads nothing extra. The docstring describes the package (login, session check, permission decorator, users and roles admin, their SQL) and states the dependency rule: other domains may import `app.auth`, and `app.auth` imports no other domain package. `app.web.navigation` is the one allowed exception until phase 6. `app.shared` and the stdlib/third-party libraries are always fine.
2. Move each file with `git mv`, then change only imports, docstrings and comments in it:
   - `app/web/blueprints/auth_routes.py` → `app/auth/routes.py` (blueprint name stays `"auth_routes"`)
   - `app/web/blueprints/users.py` → `app/auth/admin_routes.py` (blueprint name stays `"users"`, same URL prefix and endpoints)
   - `app/web/auth.py` → `app/auth/decorators.py`
   - `app/core/user_admin.py` → `app/auth/service.py`
   - `app/core/permissions.py` → `app/auth/permissions.py`
   - `app/core/db/users.py` → `app/auth/repository/users.py`, and `app/core/db/roles.py` → `app/auth/repository/roles.py`
   - `app/web/hash_password.py` → `app/auth/hash_password.py`
3. Create `app/auth/repository/__init__.py`. It explicitly re-exports (no `import *`) every name `app/core/db/__init__.py` exported from `users` and `roles`, with a matching `__all__`. Its docstring points to `app/shared/db.py` for the connection and transaction contract.
4. In code that used `db.<user/role function>`, call the repository through its module attribute: `from app.auth import repository`, then `repository.list_roles(conn)`. Don't import the functions by name. Then a monkeypatch on `app.auth.repository` reaches the call. This applies to `app/auth/admin_routes.py`, `app/auth/decorators.py`, `app/auth/service.py` and `app/web/blueprints/declarations.py` line 308. `admin_routes.py` calls the service through its module attribute too (`from app.auth import service`, then `service.validate_role(...)`).
5. Remove the `roles` and `users` imports, their names in `__all__`, and the two docstring entries from `app/core/db/__init__.py`. The docstring may add one line saying users and roles now live in `app/auth/repository/`. Everything else in that file stays unchanged.
6. Update every other import:
   - `app/web/app.py` imports the blueprints from `app.auth.routes` and `app.auth.admin_routes`, and `authenticated`/`auth` from `app.auth.decorators`. Blueprint registration order and the context processors stay as they are.
   - `app/schema_migrate.py` imports `ROLE_ADMIN, load_roles, load_users` from `app.auth.decorators`.
   - Every blueprint in `app/web/blueprints/` imports `permission_required` (and `has_permission`) from `app.auth.decorators`.
   - `app/auth/repository/roles.py` imports `PERMISSION_CODES` from `app.auth.permissions`.
7. Update the tests: imports only, monkeypatch targets, and the `db.` → `repository.` prefix for user/role calls.
   - `tests/web/test_role_admin.py` patches `delete_role` on `app.auth.repository` and `validate_role` on `app.auth.service`.
   - `tests/web/test_auth.py` imports `app.auth.decorators`. Aliasing it as `auth` is fine.
   - Tests that call non-auth functions through `db.` keep doing so.
   - Test files stay where they are. No test is deleted, skipped, renamed or weakened, and the collected test count equals `origin/main`'s.
8. In `pyproject.toml`:
   - Extend the strict mypy override so it also covers `app.auth.service`, `app.auth.permissions`, `app.auth.repository` and `app.auth.repository.*`, either in the existing module list or in a second block with `disallow_untyped_defs = true`. Covering more of `app.auth` is allowed only if it passes without code changes beyond annotations.
   - Change the coverage `omit` to `app/auth/hash_password.py`.
9. Update the command to `python -m app.auth.hash_password` in `.env.example`, `README.md`, `docs/` and the docstrings of `app/auth/hash_password.py` and `app/auth/decorators.py`.
10. Point every path that names a moved module at its new path:
    - `docs/CORE.md`: move the users, roles, permissions and user_admin rows and sections under a short "`app/auth/`" heading, or state the new paths where they are.
    - `docs/WEB.md`: the `/login`, `/logout` and `/usuarios` route rows name `app/auth/routes.py` and `app/auth/admin_routes.py`. The permission catalog and login paragraphs name the new paths.
    - `docs/DATABASE.md`, `docs/ARCHITECTURE.md` and the `docs/README.md` rows: add `app/auth/` where they describe layers.
    - `CLAUDE.md` "Project" bullets: add a one-line `app/auth/` bullet (login, session check and `permission_required`, users and roles admin, their repository).
    - The `web.Dockerfile` comment, `app/web/app.py`'s docstring, the docstrings of the moved modules, and the test docstrings and comments listed in Context.
11. Leave alone `app/migrations/*.sql`, `docs/factory-history/`, `docs/MIGRATION_PLAN.md` (it describes today's layout and the plan) and `ARCHITECTURE_AND_POS_PLAN.md` (a historical plan).
12. Nothing else changes: no URL, blueprint name, endpoint, template, flash or log message, SQL, permission rule or session behaviour. Templates and `app/web/{helpers,navigation,csrf,antispam}.py` stay where they are.
13. The PR description:
    - says the `rg` checks exclude the applied migrations, `docs/factory-history/` and `docs/MIGRATION_PLAN.md`, and why;
    - says the repository is a package so both table modules keep their history;
    - lists any improvements made with their reasons, or says there were none.

## Acceptance criteria
1. These paths exist: `app/auth/__init__.py`, `routes.py`, `admin_routes.py`, `decorators.py`, `service.py`, `permissions.py`, `hash_password.py`, `repository/__init__.py`, `repository/users.py` and `repository/roles.py`. These no longer exist: `app/web/auth.py`, `app/web/hash_password.py`, `app/web/blueprints/auth_routes.py`, `app/web/blueprints/users.py`, `app/core/user_admin.py`, `app/core/permissions.py`, `app/core/db/users.py` and `app/core/db/roles.py`. Check with `ls`.
2. `.venv/Scripts/python -m app.auth.hash_password secret` prints one line that starts with a werkzeug method prefix (`scrypt:` or `pbkdf2:`) and exits 0.
3. `rg -n "app\.web\.auth\b|app\.web\.hash_password|app\.core\.(user_admin|permissions)|app\.core\.db\.(users|roles)" app tests docs .env.example README.md CLAUDE.md web.Dockerfile --glob '!docs/factory-history/**' --glob '!app/migrations/**'` finds nothing.
4. `rg -n "app/web/auth\.py|app/web/hash_password|app/web/blueprints/(auth_routes|users)\.py|app/core/(user_admin|permissions)\.py|app/core/db/(users|roles)\.py|core/db/(users|roles)\.py|\bdb/(users|roles)\.py" app tests docs README.md CLAUDE.md web.Dockerfile --glob '!docs/factory-history/**' --glob '!app/migrations/**' --glob '!docs/MIGRATION_PLAN.md'` finds nothing.
5. `rg -n "roles|users|UserRecord|RoleRecord" app/core/db/__init__.py` matches at most one docstring line that points to `app/auth/repository/`, and no import or `__all__` entry. `.venv/Scripts/python -c "import app.core.db as d; assert not hasattr(d, 'create_user') and not hasattr(d, 'list_roles')"` exits 0.
6. `rg -n "\bdb\.(PermissionRecord|RoleRecord|create_role|delete_role|get_role|get_role_permissions|list_permissions|list_roles|update_role|UserRecord|assign_role|count_active_users_with_role|create_user|get_user_by_id|get_user_by_username|get_user_permissions|list_users|list_users_with_role|set_user_password|set_user_roles|unassign_role|update_user)\b" app tests` finds no code call. Only comments may match, such as the `tests/conftest.py` line 402 comment, which should also be updated.
7. `rg -n "setattr\(" tests/web/test_role_admin.py` shows `delete_role` patched on the `app.auth.repository` module and `validate_role` on the `app.auth.service` module. `rg -n "repository\.delete_role|service\.validate_role" app/auth/admin_routes.py` matches, so the patched names are the ones called.
8. `rg -n "^(from|import) app\.(core|web|customers|pricing|inventory|production|orders|cash_summary|declarations)" app/auth` matches only `from app.web.navigation import NAV_ITEMS` in `decorators.py`.
9. `rg -n 'Blueprint\("auth_routes"|Blueprint\("users"' app/auth` matches `routes.py` and `admin_routes.py`. `git diff origin/main...HEAD -- app/web/templates app/migrations init.sql` is empty.
10. `git diff -M --stat origin/main...HEAD` shows the eight moved files as renames (`=>`), not as a delete plus an add.
11. `pyproject.toml` has a `[[tool.mypy.overrides]]` with `disallow_untyped_defs = true` that covers `app.auth.service`, `app.auth.permissions` and `app.auth.repository.*`, and its coverage `omit` names `app/auth/hash_password.py`.
12. `.venv/Scripts/python -m pytest -q --collect-only | tail -1` reports the same number of tests as on `origin/main`. `git diff origin/main...HEAD --stat -- tests` deletes no test file.
13. `rg -n "app/auth" CLAUDE.md docs/CORE.md docs/WEB.md docs/ARCHITECTURE.md` matches in each file. `rg -n "python -m app.auth.hash_password" .env.example README.md` matches in both.

## Out of scope
- JWT, tokens, `shared/auth.py`, or any change to login, session, permission or role behaviour (phase 2).
- Moving any other domain (customers, pricing, orders, declarations and so on), the remaining `app/core` modules, templates, or `app/web/{helpers,navigation,csrf}.py`.
- Adding `app/auth/schemas.py`, `/api/*` endpoints, or reshaping routes into a service layer beyond what the moved code already does.
- Renaming functions, constants or blueprints, or changing their signatures.
- Moving or renaming test files. An optional `tests/auth/` reorganisation is left for later.
- Editing applied migrations, `docs/factory-history/`, `docs/MIGRATION_PLAN.md` or `ARCHITECTURE_AND_POS_PLAN.md`.
- Any refactor of the moved code beyond imports, docstrings, comments and the module-attribute call style in Requirement 4.

## Assumptions
- **Repository layout**: the repository is a `repository/` package with `users.py` and `roles.py`, not one merged module. Both `git mv`s keep their history, and the issue allows either layout.
- **Module-attribute calls**: callers use `repository.<fn>` and `service.<fn>` rather than importing names. This matches today's `db.<fn>` style and keeps monkeypatch targets live.
- **Declarations blueprint**: `app/web/blueprints/declarations.py` calls `app.auth.repository.get_user_by_id` directly instead of going through a new service function. The issue allows either, and the smallest change wins.
- **`app.web.navigation` exception**: `decorators.py` keeps importing `app.web.navigation` for `landing_url`. The issue keeps navigation in `app/web/` until phase 6, so this one exception is allowed.
- **Strict mypy**: only the modules that came from `app/core/` get the strict override. That is what the issue requires, and `decorators.py` has untyped Flask decorator code.
- **Test files**: they stay in place. The issue only allows changing import paths and monkeypatch targets, and this keeps the diff reviewable.
- **Extra docs**: docs outside the three the issue names (`DATABASE.md`, `README.md`, `CLAUDE.md`, `web.Dockerfile`) are updated too, because a path the code no longer has is a stale doc.
