# Users admin screen at /usuarios

> Issue #65: Add the users admin screen

## Summary
- Adds an admin-only `/usuarios` screen (new `users` blueprint) to list login accounts, create one with roles and a password, edit its roles and active flag, and reset its password.
- Validation rules live in a new `app/core/user_admin.py` that reads the database through `conn` but never writes or commits. It covers username format, password rules, valid roles, the self-protection rules and "at least one active admin".
- Reuses the data layer #63 and #88 merged (`db.create_user`, `db.set_user_roles`, `db.update_user`, `db.set_user_password`, `db.count_active_users_with_role`, `db.list_roles`) and the existing `users.manage` permission. No schema change.
- Adds a `Usuarios` nav entry after `Clientes`, Spanish route docs in `docs/WEB.md`, and web and core tests for every rule in the issue.

## Context
- **Data layer** (`app/core/db/users.py`, re-exported from `app.core.db`):
  - `UserRecord` has `id, username, password_hash, roles (sorted names), active, created_at`.
  - `create_user(conn, *, username, password_hash, roles, active=True, assigned_by=None) -> int` raises `psycopg.errors.UniqueViolation` on a duplicate username, and `ForeignKeyViolation` on an unknown role.
  - `list_users(conn)` returns every user, ordered by id. `get_user_by_id(conn, id)` returns one user or `None`.
  - `update_user(conn, id, *, active)`, `set_user_roles(conn, id, roles, *, assigned_by)` and `set_user_password(conn, id, hash)` change one user.
  - `count_active_users_with_role(conn, role)` counts over `user_roles` joined to active `users`.
  - None of these functions commit. The caller commits (see `app/core/db/__init__.py`).
- **Roles** (`app/core/db/roles.py`): `list_roles(conn)` returns `RoleRecord(name, label, is_system, permissions, user_count)`, system roles first and then by label. The internal names are `admin`, `point_of_sale` and `production`, and each has a Spanish `label`.
- **Auth** (`app/web/auth.py`): `permission_required("<code>")` re-reads the user, their roles and their permissions on every request, so role changes and deactivation apply on the next request. `session["user_id"]` and `session["user"]` identify the logged-in user. `app/core/permissions.py` defines `ADMIN_ROLE = "admin"` and the `users.manage` code, which migration 0015 grants only to `admin`. Login strips the username (`app/web/blueprints/auth_routes.py`).
- **Pattern to copy**: `app/web/blueprints/customers.py` (blueprint, `db.get_conn()`, catching `UniqueViolation`, re-rendering with `error=` and status 400) and its templates `app/web/templates/customers.html` (list table, `a.ver` button) and `customer_detail.html` (`.error` box, fieldsets, `.submit` button, `a.back`). Both templates include `_nav.html`.
- **Nav**: `app/web/navigation.py::NAV_ITEMS` holds `(endpoint, label, permission, active_endpoints)` tuples. `app.py`'s context processor filters them per user.
- **Blueprint registration**: `app/web/app.py` imports and registers each blueprint in alphabetical order, and its module docstring lists every blueprint and its routes.
- **Dates**: the Jinja filter `bogota` (`app/core/timezones.format_bogota(value, pattern)`) shows a stored UTC timestamp in Bogotá time.
- **Password hashing**: `werkzeug.security.generate_password_hash`, as in `app/web/hash_password.py`.
- **Logging**: `current_app.logger.info(...)`, as in `auth_routes.py` (`"login: user=%s roles=%s"`).
- **No flash usage yet**. The app has no post/redirect/get notice mechanism, so this spec introduces Flask's `flash` / `get_flashed_messages` (session-backed, and the session cookie is already signed).
- **Tests**:
  - `tests/conftest.py` seeds `testuser` (admin), `produccionuser` (production) and `puntoventauser` (point_of_sale).
  - `tests/web/conftest.py` provides the `client`, `authenticated_client` (admin), `produccion_client` and `punto_venta_client` fixtures, plus `db_conn` / `clean_db`.
  - `tests/web/test_permissions.py::_create_and_log_in` shows how to create a user and log in as them.
- **Docs**: in `docs/WEB.md`, the `## Rutas` table, the `## Roles y permisos` section (its "Navegación" bullet) and the `## Templates` table.

## Requirements
1. **Validation module** `app/core/user_admin.py`: pure, strictly typed, no Flask imports. Every function takes `conn` where it needs the database, never writes and never commits, and returns either `None` (valid) or one Spanish error string. Exact messages:
   - `USERNAME_RE = ^[A-Za-z0-9._-]{3,50}$`. On failure: `El usuario debe tener entre 3 y 50 caracteres: letras, números, punto, guion o guion bajo.`
   - Duplicate username: `Ese usuario ya existe.` The web layer returns this when `create_user` raises `UniqueViolation`; there is no pre-check query. Expose the message as a module constant.
   - Password shorter than 10 characters: `La contraseña debe tener al menos 10 caracteres.`
   - Password and confirmation differ: `Las contraseñas no coinciden.`
   - No role selected: `Elige al menos un rol.`
   - Any submitted role name not in `{r.name for r in db.list_roles(conn)}`: `Elige un rol válido.`
   - Editing yourself and the new active flag is false: `No puedes desactivar tu propio usuario.`
   - Editing yourself, you currently hold `admin` and the new roles don't include it: `No puedes quitarte el rol de administrador.`
   - The target is currently active and holds `admin`, the change removes `admin` or deactivates it, and `db.count_active_users_with_role(conn, "admin") <= 1`: `Debe quedar al menos un administrador activo.`
2. Suggested functions in that module (the builder may adjust the names, keeping the behaviour):
   - `validate_new_user(conn, *, username, password, password_confirm, roles) -> str | None`. Checks in order: username, roles (none selected, then invalid), password length, password match.
   - `validate_user_update(conn, *, target: UserRecord, actor_id: int, roles: list[str], active: bool) -> str | None`. Checks in order: roles (none, then invalid), self-deactivation, self admin removal, last active admin.
   - `validate_password(password, password_confirm) -> str | None`. Length first, then match.

   Use `permissions.ADMIN_ROLE` for `"admin"`.
3. **Blueprint** `app/web/blueprints/users.py`, `bp = Blueprint("users", __name__)`, registered in `app/web/app.py`, with the module docstring route list updated. Every route is decorated with `@permission_required("users.manage")`. Endpoints:
   - `users.users`: `GET /usuarios`.
   - `users.user_new`: `GET|POST /usuarios/nuevo`.
   - `users.user_edit`: `GET|POST /usuarios/<int:user_id>`.
   - `users.user_password`: `POST /usuarios/<int:user_id>/password`.

   An unknown `user_id` on any of the last two returns 404 (`abort(404)`), for GET and POST alike.
4. **List** (`GET /usuarios`, template `users.html`, same look as `customers.html`):
   - h1 `Usuarios` and a `Nuevo usuario` link button to `/usuarios/nuevo`.
   - A table with columns `Usuario`, `Roles`, `Estado`, `Creado`, plus an `Editar` link per row to `/usuarios/<id>`.
   - `Roles` shows the labels from `db.list_roles` for the user's role names, sorted and joined with `, `, or `Sin roles` when there are none.
   - `Estado` shows `Activo` or `Inactivo`.
   - `Creado` shows `created_at|bogota('%d/%m/%Y')`.
   - The page never renders `password_hash` or an internal role name: only labels are passed to the text.
   - Show the flashed notice, if any, above the table.
5. **Create** (`/usuarios/nuevo`, template `user_form.html`, same look as `customer_detail.html`):
   - h1 `Nuevo usuario`.
   - Fields, with visible labels: `Usuario` (text, `name="username"`), `Roles` (one checkbox per role from `db.list_roles`, in that order, showing the role's `label`, with `name="roles"` and `value=<role name>`), `Contraseña` (`name="password"`, type password) and `Repite la contraseña` (`name="password_confirm"`, type password).
   - Submit button `Crear usuario`.
   - On POST, strip the username but not the password.
   - When validation passes, call `db.create_user(conn, username=..., password_hash=generate_password_hash(password), roles=..., assigned_by=session["user_id"])` and commit. Then flash `Usuario creado.` and redirect to `/usuarios`.
6. **Edit** (`/usuarios/<id>`, same `user_form.html`):
   - h1 `Usuario <username>`. The username is shown but is not an editable field.
   - Form 1 (POST `/usuarios/<id>`): the `Roles` checkboxes with the user's current roles checked, an `Activo` checkbox (`name="active"`, checked when the user is active) and the button `Guardar cambios`.
   - A valid POST calls `db.set_user_roles(conn, id, roles, assigned_by=session["user_id"])` and `db.update_user(conn, id, active=...)` in one connection and commits once. Then it flashes `Cambios guardados.` and redirects to `/usuarios/<id>`.
   - Form 2, titled `Cambiar contraseña`, posts to `/usuarios/<id>/password`. It has fields `Nueva contraseña` (`name="password"`) and `Repite la contraseña` (`name="password_confirm"`) and the button `Cambiar contraseña`.
   - A valid password POST calls `db.set_user_password` with a fresh hash and commits. Then it flashes `Contraseña actualizada.` and redirects to `/usuarios/<id>`.
   - Show the flashed notice on the edit page.
7. **Validation errors**: re-render the same form with status 400 and the message in the `.error` box. Nothing is written to the database.
   - Create form: keep the entered username and the checked roles.
   - Edit form: keep the submitted roles and the `Activo` state.
   - Password form: re-render the edit page with the user's stored roles and active flag, and show the error.
   - Password inputs never get a `value` attribute, so a password is never echoed back.
8. **Logging**, one `current_app.logger.info` line per successful write. `<actor>` is `session["user"]` and `<roles>` is the target's role names after the change, comma-separated:
   - `user created: by=<actor> user=<username> roles=<roles>`
   - `user updated: by=<actor> user=<username> roles=<roles>`
   - `user password reset: by=<actor> user=<username> roles=<roles>`

   Never log a password or a hash.
9. **Nav**: append to `NAV_ITEMS`, right after the `Clientes` entry, `("users.users", "Usuarios", "users.manage", ("users.users", "users.user_new", "users.user_edit"))`.
10. **Notices**: use Flask `flash(...)` in the views and `get_flashed_messages()` in `users.html` / `user_form.html`. Do not add a notice mechanism anywhere else.
11. **Docs** (Spanish prose) in `docs/WEB.md`:
    - Add rows to the `## Rutas` table for the four routes, covering what each does, the `core`/`db` calls, the template and `@permission_required("users.manage")`.
    - Add rows to the `## Templates` table for `users.html` and `user_form.html`.
    - In `## Roles y permisos`, mention that admins manage users at `/usuarios`, including the rules: no self-deactivation, no removing your own admin role, at least one active admin, immutable username, and deactivation instead of deletion.
12. **Tests**:
    - `tests/web/test_users.py`: route integration tests with `db_conn` and the existing client fixtures.
    - `tests/core/test_user_admin.py`: the validation functions against `db_conn`, covering each message, including the case where the last-admin rule must *not* fire because another active admin exists.

## Acceptance criteria
1. Admin creates a user: `POST /usuarios/nuevo` with a valid username, the role `point_of_sale` and a matching 10+ character password returns 302 to `/usuarios`. Following it shows `Usuario creado.` and the new username in the table. A fresh client logging in as that user with that password is redirected to `/ingest`, the first nav entry `point_of_sale` may open. Verify in `tests/web/test_users.py`.
2. A user is logged in as `produccionuser`. The admin changes that user's roles at `/usuarios/<id>` to `point_of_sale` only, and the production client's next `GET /ingest` returns 200 where it was 403 before. The admin then unchecks `Activo`, and the production client's next request redirects to `/login`. Verify in `tests/web/test_users.py`.
3. Each validation message in Requirement 1 is returned by a POST with status 400 and appears in the response body. Each case has its own test in `tests/web/test_users.py` (plus core tests in `tests/core/test_user_admin.py`):
   - bad username (for example `ab` and `a b c`);
   - duplicate username (`testuser`);
   - a 9-character password;
   - mismatched passwords;
   - no role;
   - a forged role value such as `superuser`;
   - admin unchecking their own `Activo`;
   - admin unchecking their own `admin` role;
   - the last-active-admin rule, hit without a self rule firing first. Set-up: create a non-system role holding only `users.manage` (`db.create_role`) and a user with only that role, and log in as that user. `testuser` is then the only active admin. Deactivating `testuser` gets the message, and so does unchecking `admin` on `testuser`. After a second active admin is created, the same deactivation succeeds.
4. After each failing POST in criterion 3, the database is unchanged: the user count, roles and active flag are the same as before, as asserted in the tests. The re-rendered create form still contains the entered username, and no response body contains the submitted password string.
5. `GET /usuarios/999999`, `POST /usuarios/999999` and `POST /usuarios/999999/password` as admin each return 404.
6. `produccion_client` and `punto_venta_client` get 403 on `GET /usuarios`, `GET /usuarios/nuevo`, `POST /usuarios/nuevo`, `GET /usuarios/<id>`, `POST /usuarios/<id>` and `POST /usuarios/<id>/password`, and no user is created or changed. Verify in `tests/web/test_users.py`.
7. The nav shows `Usuarios`, after `Clientes`, for admin. It does not show `Usuarios` for the production and point_of_sale users. Assert on the rendered HTML of a page each of them can open.
8. `GET /usuarios` as admin never contains any user's `password_hash` value, nor the strings `point_of_sale` or `production`. It does contain the labels of those roles and `Sin roles` for a user created with no roles via `db.create_user(..., roles=[])`. Assert in a test.
9. A user given both `production` and `point_of_sale` at `/usuarios/<id>` gets, on their next request, a 200 on a `point_of_sale`-only route (for example `/ingest`) and on a `production`/`records.view` route (`/records`). Verify in `tests/web/test_users.py`.
10. A password reset via `POST /usuarios/<id>/password` redirects to `/usuarios/<id>` and shows `Contraseña actualizada.`. Afterwards that user can log in with the new password and not with the old one. Verify in a test.
11. The log lines are emitted and contain no password or hash. Assert with `caplog`: the create test's log record contains `user created: by=testuser user=<new>` and does not contain the password or `password_hash`.
12. `docs/WEB.md` has rows for `/usuarios`, `/usuarios/nuevo`, `/usuarios/<id>` and `/usuarios/<id>/password`, and template rows for `users.html` and `user_form.html`. Check by reading the file.

## Out of scope
- Creating, editing or deleting roles, and any screen for role permissions (next task).
- Self-service password change, password reset by e-mail, 2FA, login throttling and password complexity rules beyond the 10-character minimum.
- Deleting users. Deactivation is the only way to remove access.
- Changing a username after creation.
- CSRF tokens. The app has none, and #63 left them out too.
- Any schema change or migration, and any change to `app/core/db/users.py` / `roles.py` signatures.
- Search or filter on the user list, pagination, or showing the `assigned_by` / `assigned_at` audit data.
- Adding flash notices to other screens, or refactoring `customers.py` / its templates.
- Changing `app/web/auth.py`'s login or per-request logic.
- Editing `factory/checks.sh` or `factory/config.json`.

## Assumptions
- Notices use Flask `flash`. The repo has no post/redirect/get notice mechanism yet, and `flash` is the smallest standard one (it rides on the already signed session cookie).
- After a create, the redirect goes to the list. After saving changes or a password, it goes to the edit page. The issue allows either, and the edit page is where the admin stays working.
- Usernames are stripped of surrounding whitespace before validation, because login strips them too. Passwords are not stripped. Uniqueness stays case-sensitive, matching the DB constraint and login.
- Only the first failing rule is shown, in the order listed in Requirement 2. This matches the single `error` box pattern of `customer_detail.html`.
- When an admin edits their own account, the self rules take precedence over the last-active-admin rule. The self message is the more precise explanation.
- The last-admin check runs in the same connection right before the write, without row locks. Two admins demoting each other at the same instant is an accepted, negligible race for a staff-only screen.
- Any holder of `users.manage` may grant any role, including `admin`. Today only `admin` holds `users.manage`, and limiting this is a roles-screen question for the next task.
- The `Roles` checkbox `value` attributes carry internal role names. They are form values, not displayed text, and the "no internal role name" rule (criterion 8) applies to the list page, as the issue states.
- Only `docs/WEB.md` is updated, as the issue asks. `docs/CORE.md` doesn't catalogue `permissions.py` either, so `user_admin.py` is documented by its own docstring.
