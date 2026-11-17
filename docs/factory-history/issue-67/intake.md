DECISION: ACCEPT
RISK: high
SURFACES: ui, security
RESCOPED: no

## Summary
- Accepted as written: the issue fully specifies the routes, Spanish texts, validation rules, logging, docs and tests.
- Both dependencies are in place on `main`: multi-role users (`user_roles`, migration 0018) and CSRF protection (#92).
- The data layer already exists (`app/core/db/roles.py`: `list_roles`, `list_permissions`, `get_role`, `create_role`, `update_role`, `delete_role`), so this is mostly a screen plus validation rules.
- Rated high risk because it changes who can do what in the app: a wrong guard could lock staff out or grant too much.

## Scope
Build role management inside the existing users blueprint, all behind `users.manage`, exactly as the issue specifies:
- `GET /usuarios/roles`: list with `Rol` (label plus `(del sistema)` for system roles), `Permisos` (count, or `Todos` for admin), `Usuarios` (all `user_roles` rows, active or not), `Editar`, and a `Nuevo rol` button. `/usuarios` gets a `Roles y permisos` link.
- `GET|POST /usuarios/roles/nuevo` and `GET|POST /usuarios/roles/<name>`: name field plus one checkbox per permission from `db.list_permissions` (in `sort_order`, Spanish labels). The `admin` role is read-only with the stated message, and a POST to it returns 400 and changes nothing. Other system roles keep their label read-only but their permissions can be edited.
- `POST /usuarios/roles/<name>/delete`, shown only for custom roles, with the stated confirm text.
- Unknown role returns 404. Use post/redirect/get with the notices `Rol creado.`, `Cambios guardados.` and `Rol eliminado.`.
- The rules go in `app/core/user_admin.py` with the issue's Spanish messages (400 on the form): label trimmed, 1 to 60 characters, unique ignoring case and accents; at least one permission; unknown codes ignored; internal name generated as `custom_` + `secrets.token_hex(4)` and never shown; no deleting system roles or roles that any user (active or not) holds. Log `role created/updated/deleted: by=<username> role=<name>`.
- Docs: add the routes to the `docs/WEB.md` routes table and write the `## Roles y permisos` section in Spanish.
- Tests: acceptance criteria 1 to 6 from the issue, including that a permission change takes effect on the user's next request and that no page shows an internal role name.

Left out (per the issue): new permission codes, changing what a code grants, renaming system roles, and per-user permissions.

## Why
The request is precise and fits the existing design. `app/core/db/roles.py` already provides the reads and writes and says outright that it is the data layer for this screen. `app/web/auth.py` reads roles and permissions from the database on every request, so edits take effect right away. `user_roles.role` has no `ON DELETE` (migration 0018), so the database itself also blocks deleting a role that users still hold. Every business decision (who can manage roles, that admin is locked, the delete rules) is already made in the issue, so nothing is left to ask.
