DECISION: ACCEPT
RISK: high
SURFACES: security
RESCOPED: no

## Summary
- Accepted as written: schema, permission catalog and data-access functions for roles, with no change in app behaviour.
- The issue already settles the business rules: which roles exist, their Spanish labels, and the permission set for each role.
- Risk is high because it adds a migration with a foreign key on `users.role` against live production data, and the tables will later decide who can do what.
- No UI change. Security surface because the permission model lives here.

## Scope
Build exactly what issue #62 specifies:
- A pure `app/core/permissions.py` with the 13 permission codes and Spanish labels in the listed order, `PERMISSIONS`, `PERMISSION_CODES`, `ADMIN_ROLE = "admin"`, and `effective_permissions(role, stored)`. Admin always gets every code. Other roles get the stored codes, with unknown codes dropped.
- Migration `0015_roles_and_permissions.sql`, safe to re-run, plus the same end schema in `init.sql`:
  - the `roles` and `role_permissions` tables;
  - the three seeded system roles and the seeded permissions given in the issue;
  - any unknown `users.role` value backfilled as a non-system role whose label is its name and which has no permissions;
  - `users_role_fkey` (`ON UPDATE CASCADE`), added only when it doesn't already exist.
- `backfill_users` must still work on a brand-new database, with a test.
- `app/core/db/roles.py` with `RoleRecord`, `list_roles`, `get_role`, `get_role_permissions`, `create_role`, `update_role` and `delete_role`, re-exported from `app/core/db/__init__.py`. The functions follow the never-commit conn contract.
- `users.py` additions: `created_at` on `UserRecord` (default None), `update_user`, `set_user_password` and `count_active_users_with_role`.
- Tests for acceptance criteria 1 to 4: idempotent migration, the `legacy_role` backfill, every new data function, and `effective_permissions`.
- Docs in Spanish: `docs/DATABASE.md` (both tables, the FK and the diagram) and a mention in `docs/CORE.md`.

Left out, as the issue says: any change to `auth.py` decorators, the session, routes or templates; any screen for managing users or roles; and renaming the existing role identifiers. Login and every existing route test must pass unchanged.

## Why
Today roles are hard-coded in `app/web/auth.py` (`ROLES`, `FULL_ACCESS_ROLES`, `POS_ROLES`), and `app/core/db/users.py` has only create, lookup and list functions. That makes this a well-bounded data-layer slice that a later task can wire into routes. The migration runner (`app/schema_migrate.py`) tracks applied files by name, so using 0015 before 0014 exists is safe. Nothing in this task needs the production-board code from #60: the `production.*` codes are only seeded here, not enforced. The issue fixes every role and permission decision, so nothing is left for INGSE to decide.

## Questions
