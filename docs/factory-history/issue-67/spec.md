# Role and permission management on the users screen

> Issue #67: Add role and permission management to the users screen

## Summary
- Adds a "Roles y permisos" section to `/usuarios`: list roles, create a custom role, edit a role's permissions, delete an unused custom role.
- Everything stays in the existing users blueprint (`app/web/blueprints/users.py`) behind `users.manage`, on top of the existing data layer `app/core/db/roles.py`.
- The validation rules (label, uniqueness ignoring case and accents, at least one permission, delete guards) go in `app/core/user_admin.py` with the issue's Spanish messages.
- `admin` stays read-only. Other system roles keep their labels but their permissions can be edited. Internal role names are generated (`custom_<hex>`) and never shown as page text.
- Includes tests for every rule and route, and Spanish docs in `docs/WEB.md`.

## Context
- **Data layer (already exists, reuse as is):** `app/core/db/roles.py` provides `list_roles` (system roles first, then by label; `RoleRecord` has `name`, `label`, `is_system`, `permissions` (sorted codes), `user_count` (all `user_roles` rows, active users or not)), `list_permissions` (`PermissionRecord(code, label)` in `sort_order`), `get_role`, `create_role(conn, *, name, label, permissions)`, `update_role(conn, name, *, label, permissions)` (replaces the permission set and returns False for an unknown role), and `delete_role(conn, name)` (raises `psycopg.errors.ForeignKeyViolation` through `user_roles_role_fkey` while any user holds the role). The writes validate codes against `PERMISSION_CODES` and raise `ValueError`. They do not protect system roles; its docstring says that guard belongs to the screen, and it still calls the screen "future".
- **Schema:** `roles(name PK, label NOT NULL UNIQUE, is_system, created_at)` and `role_permissions` (ON DELETE CASCADE) in `app/migrations/0015_roles_and_permissions.sql`, plus `user_roles` (`app/migrations/0018_user_roles.sql`, no ON DELETE on the role FK). The label UNIQUE constraint is case- and accent-sensitive. Seeded system roles: `admin` (Administrador), `point_of_sale` (Punto de venta), `production` (Producción). No schema change is needed.
- **Permissions:** `app/core/permissions.py` holds `PERMISSIONS`, `PERMISSION_CODES` and `ADMIN_ROLE = "admin"`. `effective_permissions` always grants admin every code. `app/web/auth.py` (`current_user`, `permission_required`) reads roles and permissions from the DB on every request, so permission edits apply on the next request with no extra work.
- **Users screen today:** `app/web/blueprints/users.py` has `/usuarios`, `/usuarios/nuevo`, `/usuarios/<int:user_id>` and `/usuarios/<int:user_id>/password`, all `@permission_required("users.manage")`, using post/redirect/get with `flash`. A `UniqueViolation` is mapped to a Spanish message (`user_admin.MSG_DUPLICATE_USERNAME`), and log lines look like `user created: by=%s user=%s ...` through `current_app.logger.info`. The user form's role checkboxes come from `db.list_roles(conn)` on every request (`user_form.html`), so a new role shows up there immediately.
- **Rules module:** `app/core/user_admin.py` holds pure validation functions (no Flask). Each returns None or the first failing Spanish message, takes `conn` where it needs the DB, and never writes. `MSG_*` constants hold the messages.
- **Templates:** `app/web/templates/users.html` (list, `.toolbar` with `a.new` button, `table.list`, notice box) and `app/web/templates/user_form.html` (fieldsets, `label.check` checkboxes, `.error`/`.notice` boxes, a hidden `csrf_token` field in each `method="post"` form, `a.back` link). Both include `_nav.html`. New templates should follow the same structure and inline styles.
- **Accent folding:** `app/core/normalize.py` already strips accents with `unicodedata.normalize("NFKD", ...)` and drops combining characters (`_strip_accents`).
- **CSRF:** every POST form carries `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">` (`docs/WEB.md`, "Protección CSRF").
- **Tests:** `tests/web/test_users.py` is the model for the web tests: `authenticated_client` (admin), `produccion_client`, `punto_venta_client`, `db_conn`, `_fresh_client(username, password)` for multi-user tests, and the internal-name check in `TestList`. `tests/core/test_user_admin.py` holds the rule tests. `clean_db` rolls back through a savepoint, so roles created in a test do not leak.
- **Docs:** `docs/WEB.md` has the routes table (rows for `/usuarios...` near line 35), the `## Roles y permisos (auth.py)` section with the "Administración de usuarios" bullet, and the `## Templates` table (`users.html`, `user_form.html` rows).

## Requirements
1. **Routes** in `app/web/blueprints/users.py`, all decorated with `@permission_required("users.manage")`. No new blueprint, and no change to `app/web/app.py` or `app/web/navigation.py`:
   - `GET /usuarios/roles` (list)
   - `GET|POST /usuarios/roles/nuevo` (create)
   - `GET|POST /usuarios/roles/<name>` (edit)
   - `POST /usuarios/roles/<name>/delete` (delete)

   An unknown `<name>` returns 404 on edit (GET and POST) and on delete.
2. **List page** (new template `roles.html`): h1 `Roles y permisos`, a flash notice box, a `Nuevo rol` button linking to `/usuarios/roles/nuevo`, and a table with headers `Rol`, `Permisos`, `Usuarios` and `Editar`, in the order of `db.list_roles`:
   - `Rol` shows the label, followed by `(del sistema)` for system roles.
   - `Permisos` shows `Todos` for `admin` and the number of stored permission codes for every other role.
   - `Usuarios` shows `RoleRecord.user_count`.
   - `Editar` links to `/usuarios/roles/<name>`.

   `users.html` gets a link with the text `Roles y permisos` to `/usuarios/roles`, in the toolbar next to `Nuevo usuario`.
3. **Form page** (new template `role_form.html`, shared by create and edit, using the `user_form.html` patterns):
   - A text field `Nombre del rol` (`name="label"`).
   - One checkbox per permission from `db.list_permissions(conn)`, in that order, showing the Spanish label, with `name="permissions"` and the code as value. Never build the list from `PERMISSIONS`.
   - A hidden CSRF field.
   - A back link to `/usuarios/roles`.
   - On create, h1 `Nuevo rol` and submit button `Crear rol`. On edit, the role's label as h1 and submit button `Guardar cambios`. Checkboxes show the role's current permissions, or the submitted ones when re-rendering after an error.
   - On re-render after an error, keep the submitted label and permissions.
4. **Admin role is read-only:**
   - `GET /usuarios/roles/admin` shows `El rol Administrador siempre tiene todos los permisos y no se puede editar.`, the label not editable, every permission checkbox checked and disabled, and no `Guardar cambios` or `Eliminar rol` button.
   - `POST /usuarios/roles/admin` returns 400 and writes nothing.
5. **Other system roles** (`is_system` true, not admin): the label is shown but not editable, and the server ignores any submitted label, keeping the stored one. The permissions can be edited.
6. **Custom roles** (`is_system` false): both the label and the permissions can be edited. The edit page shows an `Eliminar rol` button in its own POST form (with CSRF) to `/usuarios/roles/<name>/delete`, with `onsubmit="return confirm('¿Eliminar el rol <label>?')"`. The label must be escaped safely for the JS string (for example with Jinja's `|tojson`). System roles never show this button.
7. **Rules in `app/core/user_admin.py`** (pure, no Flask, never writes), with new `MSG_*` constants holding exactly these texts:
   - **Label:** trimmed. Empty or longer than 60 characters gives `Escribe el nombre del rol.`
   - **Uniqueness:** compare against every other role's label after case-folding and stripping accents (NFKD plus dropping combining characters, the same approach as `normalize._strip_accents`). A match gives `Ya existe un rol con ese nombre.` The role being edited is excluded from the comparison.
   - **Permissions:** submitted codes not in `db.list_permissions(conn)` are dropped silently, and duplicates are removed. If none remain: `Marca al menos un permiso.`
   - **Order:** the label checks run first, then the permission check. Only the first failing message is returned.
   - **Delete:** a system role gives `No se puede eliminar un rol del sistema.`; a role with `user_count > 0` gives `No se puede eliminar un rol con usuarios asignados.`
   - **Internal name:** a helper generates the name of a new role as `"custom_" + secrets.token_hex(4)`. It is never typed by the user and never shown as page text.
8. **Error handling in the web layer:**
   - A rule failure re-renders the form with status 400, the message in the `.error` box, and nothing written.
   - A failed delete re-renders the role's edit page with status 400 and the message.
   - A `psycopg.errors.UniqueViolation` from `create_role`/`update_role` (a label race) maps to `Ya existe un rol con ese nombre.` with status 400.
   - A `psycopg.errors.ForeignKeyViolation` from `delete_role` (a user was assigned in between) maps to `No se puede eliminar un rol con usuarios asignados.` with status 400.
9. **Post/redirect/get and notices:**
   - Create redirects to `/usuarios/roles` with `Rol creado.`
   - Edit redirects to `/usuarios/roles/<name>` with `Cambios guardados.`
   - Delete redirects to `/usuarios/roles` with `Rol eliminado.`

   Each write commits once.
10. **Logging:** after each successful write, `current_app.logger.info("role created: by=%s role=%s", session["user"], name)`, with `updated` and `deleted` lines in the same form. `role=` is the internal name.
11. **Internal names never shown:** no page shows an internal role name (`custom_...`, `point_of_sale`, `production`, `admin`) as visible text. Using it in `href`, `action` or `value` attributes is allowed.
12. **Docstrings:** update the module docstring of `app/web/blueprints/users.py` to cover the role routes. Update `app/core/db/roles.py`'s docstring so it no longer calls the screen "future".
13. **Docs (Spanish prose)** in `docs/WEB.md`:
    - Add rows for the four new routes to the `## Rutas` table, in the format of the existing `/usuarios` rows.
    - Add rows for `roles.html` and `role_form.html` to the `## Templates` table, and mention the `Roles y permisos` link in the `users.html` row.
    - In `## Roles y permisos (auth.py)`, add a bullet explaining how an administrator creates a role (Usuarios → Roles y permisos → Nuevo rol, name plus at least one permission, then assign it in the user's form). It must also state that Administrador cannot be edited, that the labels of system roles do not change, and that only custom roles with no users can be deleted. Update the "Roles sembrados" text if needed so it no longer implies the permissions are fixed.
14. **Tests:**
    - Core tests for the new rules in `tests/core/test_user_admin.py`.
    - Web tests in a new `tests/web/test_role_admin.py`, following `tests/web/test_users.py`. They cover acceptance criteria 1 to 9 below.

## Acceptance criteria
1. Admin creates a role and assigns it. Through the routes, admin POSTs `/usuarios/roles/nuevo` with label `Bodega` and permission `inventory.manage`, gets a redirect to `/usuarios/roles` with `Rol creado.`, then POSTs `/usuarios/{id}` giving a user the roles `production` plus the new role. That user, logged in through `_fresh_client`, gets 200 on `/inventory` and `/records` and 403 on `/pricing`. The `GET /usuarios/{id}` form lists `Bodega` as a role checkbox. Verify: a test in `tests/web/test_role_admin.py`.
2. A permission removal takes effect on the next request. No route checks `production.manage` yet (nothing in `app/web/` uses it), so the test does two things. First, a logged-in production session gets 200 on `/records`; admin POSTs `/usuarios/roles/production` with every current code except `records.view`; the same session gets 403 on `/records` on its next request. Second, after admin removes `production.manage` from `production`, `db.get_user_permissions` for the production user no longer contains `production.manage` and the role's other codes are unchanged. Verify: a test in `tests/web/test_role_admin.py`.
3. Each rule returns 400 with its exact message and writes nothing (role rows and `role_permissions` unchanged). Verify: one web test per case in `tests/web/test_role_admin.py`:
   - an empty or whitespace-only label
   - a 61-character label
   - a label equal to an existing one with different case and accents (for example `bodega` vs `Bódega`, and `PRODUCCION` vs `Producción`)
   - no permissions, or only unknown codes
4. Unknown permission codes are ignored. Submitting a known code together with a made-up one creates the role with only the known code. Editing a custom role to its own label with other casing succeeds. A 60-character label succeeds and is stored trimmed. Verify: tests in `tests/web/test_role_admin.py` and `tests/core/test_user_admin.py`.
5. Admin role and system role protection:
   - `GET /usuarios/roles/admin` shows `El rol Administrador siempre tiene todos los permisos y no se puede editar.` with no `Guardar cambios` and no `Eliminar rol`.
   - `POST /usuarios/roles/admin` returns 400 and leaves admin's `role_permissions` unchanged.
   - POSTing `/usuarios/roles/production` with a different `label` keeps the label `Producción`.
   - `POST /usuarios/roles/production/delete` returns 400 with `No se puede eliminar un rol del sistema.` and the role still exists.
   - `POST /usuarios/roles/admin/delete` returns 400 with the same message.

   Verify: tests in `tests/web/test_role_admin.py`.
6. Delete:
   - Deleting a custom role held by an inactive user returns 400 with `No se puede eliminar un rol con usuarios asignados.` and the role still exists.
   - Deleting a custom role with no users redirects to `/usuarios/roles` with `Rol eliminado.` and `db.get_role` returns None.
   - The edit page of a custom role contains `Eliminar rol` and `¿Eliminar el rol `. The edit page of `production` contains neither.

   Verify: tests in `tests/web/test_role_admin.py`.
7. List page:
   - `GET /usuarios/roles` as admin returns 200 with h1 `Roles y permisos`, the headers `Rol`, `Permisos`, `Usuarios` and `Editar`, `Administrador (del sistema)`, `Todos`, the correct permission count for `production` (4 from the seed), the user counts, and `Nuevo rol`.
   - `GET /usuarios` contains a link `Roles y permisos` to `/usuarios/roles`.
   - Unknown names return 404: `GET /usuarios/roles/no_existe`, `POST /usuarios/roles/no_existe` and `POST /usuarios/roles/no_existe/delete`.

   Verify: tests in `tests/web/test_role_admin.py`.
8. Non-admins are refused. `punto_venta_client` and `produccion_client` get 403 on GET and POST of every route: `/usuarios/roles`, `/usuarios/roles/nuevo`, `/usuarios/roles/production` and `/usuarios/roles/production/delete`. No role or permission changes. Verify: a parametrized test in `tests/web/test_role_admin.py`.
9. No internal role names in visible text. After creating a custom role and assigning it to a user, the visible text (HTML with tags removed) of `/usuarios`, `/usuarios/roles`, `/usuarios/roles/nuevo`, the custom role's edit page, `/usuarios/roles/point_of_sale` and `/usuarios/{id}` contains no `custom_`, no `point_of_sale` and no internal names of the other seeded roles used as text. Verify: a test in `tests/web/test_role_admin.py`.
10. Logging. A successful create, edit and delete each log `role created: by=<admin username> role=custom_...`, `role updated: ...` and `role deleted: ...`, captured with `caplog`. The generated name matches `^custom_[0-9a-f]{8}$`. Verify: tests in `tests/web/test_role_admin.py`.
11. Rule functions. The new functions in `app/core/user_admin.py` return each message listed in requirement 7 for the matching input, and None for valid input. Verify: tests in `tests/core/test_user_admin.py`.
12. Docs. `docs/WEB.md` contains rows for `/usuarios/roles`, `/usuarios/roles/nuevo`, `/usuarios/roles/<name>` and `/usuarios/roles/<name>/delete`, rows for `roles.html` and `role_form.html`, and Spanish text under `## Roles y permisos` describing how to create a role. Verify: `grep -n "usuarios/roles" docs/WEB.md`.
13. `app/web/navigation.py`, `app/web/app.py`, `app/core/permissions.py` and every file in `app/migrations/` are unchanged. Verify: `git diff --stat origin/main -- app/web/navigation.py app/web/app.py app/core/permissions.py app/migrations` prints nothing.

## Out of scope
- New permission codes, migrations, or any change to which route requires which code.
- Adding permission codes from the screen. The codes only come from migrations.
- Renaming system roles or editing their labels, and editing the internal name of any role.
- Per-user permissions outside roles.
- Changing `app/web/navigation.py`, even though the `Usuarios` tab will not be highlighted on the role pages, and changing `app/web/app.py`. No new blueprint.
- Changing `app/core/db/roles.py` behaviour (only its docstring), or adding accent-insensitive constraints or extensions (`unaccent`) to the database.
- Showing which users hold a role, bulk actions, or reassigning users when deleting a role.
- Changing the existing user routes beyond adding the `Roles y permisos` link.

## Assumptions
- **Label is editable for custom roles.** The issue only makes labels read-only for system roles and lists "renaming system roles" as out of scope, which implies custom roles can be renamed.
- **Internal names may appear in attributes.** They appear in `href`, `action` and `value` attributes, like the existing `user_form.html` checkboxes. The "never rendered" requirement (issue AC 5) covers visible text only, since the edit URL itself has to carry the name.
- **Uniqueness ignores case and accents only.** Inner whitespace is not collapsed, matching the issue's wording; a label is only trimmed at the ends.
- **Permisos count for non-admin roles** is the number of stored `role_permissions` codes (`len(RoleRecord.permissions)`). Admin shows `Todos` whatever is stored.
- **A failed delete re-renders the edit page with 400.** It does not flash on the list, which keeps the "status 400 on the form" convention from the issue.
- **Admin POST response** is the read-only admin page with status 400 and the read-only message. The issue only requires the 400 and no write.
- **Form field names** are `label` and `permissions`, and the templates are named `roles.html` and `role_form.html`. These are internal English identifiers chosen for consistency with `user_form.html`'s `roles` field.
- **Criterion 2 uses `records.view`.** No route requires `production.manage` yet (no `app/web` code uses it), so issue AC 2 is checked two ways: removing `records.view` must turn `/records` into a 403 on the next request, and removing `production.manage` must take it out of the user's stored permissions. Adding a `production.manage` route is out of scope.
