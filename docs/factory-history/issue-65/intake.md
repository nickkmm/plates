DECISION: ACCEPT
RISK: high
SURFACES: ui, security
RESCOPED: no

## Summary
- Accepted as written: an admin-only `/usuarios` screen to list, create, edit (roles and active flag) and reset the password of login accounts.
- Both dependencies are merged: #63 (roles and permissions, `users.manage`) and #88 (several roles per user through `user_roles`, PR #90).
- The issue already settles the rules only the owner could decide: who can use it, password length, the last-active-admin guard, and no deletion.
- Rated high risk because it changes who can log in and with which permissions.

## Scope
Build exactly what the issue specifies, all behind `permission_required("users.manage")`:
- `GET /usuarios`: table with `Usuario`, `Roles` (the role labels, sorted and joined with `, `, or `Sin roles`), `Estado` (`Activo` / `Inactivo`), `Creado` (date in Bogotá time) and an `Editar` link, plus a `Nuevo usuario` button. It never shows a password hash or an internal role name.
- `GET|POST /usuarios/nuevo`: username, one checkbox per role (by label, in `db.list_roles` order), password and confirmation. Creates the user with the selected roles.
- `GET|POST /usuarios/<id>`: edit roles and `Activo`. The username cannot be changed. A separate `Cambiar contraseña` form posts to `POST /usuarios/<id>/password`. An unknown id returns 404.
- On success, redirect (post/redirect/get) and show the notice `Usuario creado.`, `Cambios guardados.` or `Contraseña actualizada.`.
- On a validation error, show the form again with status 400. Keep the values entered except the passwords, and use the exact Spanish messages listed in the issue. That includes the self-deactivation, self-admin-removal and last-active-admin rules (the admin count is over `user_roles`). A duplicate username is caught from the UNIQUE violation.
- The validation rules live in a pure `app/core/user_admin.py` that never commits.
- A `Usuarios` nav entry after `Clientes`, visible only to users with `users.manage`.
- Info log lines without passwords or hashes. Route docs in Spanish in `docs/WEB.md`.
- Tests for acceptance criteria 1 to 6, and `factory/checks.sh` passes.

Left out, as the issue says: creating, editing or deleting roles; self-service password change, e-mail reset, 2FA and login throttling; deleting users.

## Why
The data layer this screen needs is already merged on `origin/main` through #63 and #88: `create_user(roles=...)`, `update_user(active=...)`, `set_user_roles`, `set_user_password`, `count_active_users_with_role` over `user_roles`, and `db.list_roles`. `users.manage` is already in the permission catalog (`app/core/permissions.py`) and granted to `admin` (migration 0015). So this is one blueprint, two templates, a validation module and tests, which fits in one pull request. Risk is high because it controls login accounts and permissions. The spec must keep the last-active-admin guard and the role-name validation safe against forged form posts, and must make sure no hash ever reaches the page or the logs.

## Questions
