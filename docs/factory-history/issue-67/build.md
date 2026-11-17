STATUS: READY

# Build: Role and permission management on the users screen

## Summary
- Adds a "Roles y permisos" section under `/usuarios` (list, create, edit, delete roles), all in `app/web/blueprints/users.py` behind `users.manage`.
- Validation rules (label, accent/case-insensitive uniqueness, at least one known permission, delete guards) live in `app/core/user_admin.py` with the spec's exact Spanish messages.
- `admin` is read-only, other system roles keep their label, custom roles are fully editable and deletable when unused; internal names (`custom_<hex>`) never appear as page text.
- New tests: `tests/web/test_role_admin.py` (35 tests) and role rule tests in `tests/core/test_user_admin.py`; Spanish docs in `docs/WEB.md`.
- Draft PR: https://github.com/nickkmm/plates/pull/96

## Changes
- `app/core/user_admin.py`: `ROLE_LABEL_MAX_LENGTH`, `MSG_BAD_ROLE_LABEL`, `MSG_DUPLICATE_ROLE_LABEL`, `MSG_NO_PERMISSION`, `MSG_DELETE_SYSTEM_ROLE`, `MSG_DELETE_ROLE_IN_USE`; `new_role_name()` (`"custom_" + secrets.token_hex(4)`), `clean_permissions(conn, codes)` (keeps catalog codes from `db.list_permissions`, deduped, catalog order), `validate_role(conn, *, label, permissions, role_name=None)` (label, then folded uniqueness excluding `role_name`, then permissions), `validate_role_delete(role)`. Module docstring updated.
- `app/web/blueprints/users.py`: routes `roles`, `role_new`, `role_edit`, `role_delete`, helpers `_get_role_or_404` and `_render_role_form`. Admin POST renders the read-only page with 400; system-role POST keeps the stored label; `UniqueViolation` -> duplicate message, `ForeignKeyViolation` -> in-use message, both 400; PRG with `Rol creado.` / `Cambios guardados.` / `Rol eliminado.`; one commit per write; `role created/updated/deleted: by=%s role=%s` log lines. Module docstring covers the role routes.
- `app/web/templates/roles.html` (new): h1, notices, "← Volver a Usuarios", "Nuevo rol", table Rol / Permisos / Usuarios / Editar.
- `app/web/templates/role_form.html` (new): "Nombre del rol" (`name="label"`, disabled without a name for system roles), one checkbox per `db.list_permissions` entry, CSRF, back link, `Crear rol` / `Guardar cambios`; admin shows the read-only message, all boxes checked+disabled, no form; custom roles get a separate CSRF'd "Eliminar rol" form with `onsubmit='return confirm("¿Eliminar el rol " + {{ role.label|tojson }} + "?")'` (single-quoted attribute so `tojson`'s double quotes are safe; `'` is escaped to `'` by `tojson`).
- `app/web/templates/users.html`: "Roles y permisos" link next to "Nuevo usuario" in the toolbar.
- `app/core/db/roles.py`: docstring only (no longer "future" screen).
- `docs/WEB.md`: 4 route rows, `roles.html`/`role_form.html` template rows, `users.html` row mentions the link, new "Roles y permisos (crear un rol)" bullet, "Roles sembrados" text notes that seeded permissions can be changed.
- `tests/core/test_user_admin.py`, `tests/web/test_role_admin.py`: tests below.

## Acceptance criteria
1. [x] Create and assign: `TestCreateAndAssign::test_admin_creates_a_role_and_assigns_it` passes (redirect to `/usuarios/roles`, `Rol creado.`, 200 on `/inventory` and `/records`, 403 on pricing, `Bodega` checkbox on `/usuarios/{id}`). Note: the pricing page's route is `/precios` (`app/web/blueprints/pricing.py`); `/pricing` doesn't exist (would be 404), so the test uses `/precios`.
2. [x] Permission removal on next request: `TestPermissionChanges::test_removing_a_permission_applies_on_the_next_request` (records.view removed -> same session 403 on `/records`) and `test_removing_production_manage` (`get_user_permissions` loses only `production.manage`) pass.
3. [x] Each rule 400 + exact message + nothing written: `TestValidation::test_create_rejected_with_400_and_nothing_written[empty|whitespace|61-characters|duplicate-system|no-permission|unknown-only]`, `test_duplicate_ignores_case_and_accents` (`bodega` vs `Bódega`), `test_edit_rejected_with_400_and_nothing_written` pass.
4. [x] Unknown codes ignored, own label with other casing, 60-char trimmed label: `test_unknown_codes_are_ignored`, `test_custom_role_can_take_its_own_label_with_other_casing`, `test_sixty_character_label_is_stored_trimmed`, plus core `TestCleanPermissions`, `TestValidateRole::test_the_edited_role_is_left_out` pass.
5. [x] Admin/system protection: `TestSystemRoles` (read-only admin page, admin POST 400 with unchanged `role_permissions`, production label kept as `Producción`, production/admin delete 400 with `No se puede eliminar un rol del sistema.`) passes.
6. [x] Delete: `TestDelete` (inactive holder -> 400 in-use message, unused -> `Rol eliminado.` and `get_role` None, FK race -> in-use message, custom page has `Eliminar rol` and `¿Eliminar el rol `, production page has neither) passes.
7. [x] List page, users link, 404s: `TestList` passes (headers, `Administrador (del sistema) Todos`, production count 4, user counts, `Nuevo rol`, link on `/usuarios`, 404 on GET/POST/delete of `no_existe`).
8. [x] Non-admins 403: `TestAccess::test_non_admins_get_403_and_change_nothing[produccion_client|punto_venta_client]` passes for GET+POST of `/usuarios/roles/nuevo` and `/usuarios/roles/production`, GET `/usuarios/roles`, POST `/usuarios/roles/production/delete`; roles unchanged. (GET on the POST-only delete route is a 405 from Flask before auth, so it isn't asserted as 403; POST `/usuarios/roles` is likewise a 405.)
9. [x] No internal names in visible text: `TestNoInternalNames::test_pages_never_show_internal_role_names` passes over all six pages.
10. [x] Logging: `TestLogging::test_create_update_delete_are_logged` passes; generated name matches `^custom_[0-9a-f]{8}$`.
11. [x] Rule functions: `tests/core/test_user_admin.py` 35 passed (`TestValidateRole`, `TestCleanPermissions`, `TestValidateRoleDelete`, `test_new_role_name`).
12. [x] Docs: `grep -n "usuarios/roles" docs/WEB.md` shows the 4 route rows (lines 39-42), the template rows (309-310) and the Roles y permisos text.
13. [x] `git diff --stat origin/main -- app/web/navigation.py app/web/app.py app/core/permissions.py app/migrations` prints nothing.

## Validation
- `bash factory/checks.sh` with `TEST_POSTGRES_DB=plates_test_11 TEST_REQUIRE_DB=1` and the `.env` Postgres credentials: passed (ruff, mypy, 1773 passed in 17:53, shell and factory script checks).
- `pytest tests/web/test_role_admin.py`: 35 passed. `pytest tests/core/test_user_admin.py`: 35 passed.
- `ruff check` on the two test files: passed.
- Browser check not done: `agent-browser` is not installed on this machine and there is no local admin login I could use without touching the user's local database; the pages were verified through the rendered HTML in the web tests instead.

## Log
### Round 1
Implemented the spec: rules in `user_admin.py`, four routes in the users blueprint, `roles.html` and `role_form.html`, toolbar link on `users.html`, docstrings, `docs/WEB.md`, core and web tests. Deviations, both forced by the codebase: AC 1 uses `/precios` (the real pricing route) instead of `/pricing`; the delete confirm uses a single-quoted `onsubmit` with double-quoted JS strings so `|tojson` output stays valid inside the attribute. Gate passed; pushed and opened draft PR #96.
