DECISION: ACCEPT
RISK: high
SURFACES: ui, security
RESCOPED: no

## Summary
- Accepted as written: a new `user_roles` table replaces `users.role`, and a user's permissions are the union of all their roles.
- The spec is precise and follows the code: `effective_permissions`, `UserRecord`, `current_user()` and `backfill_users` are the places that change.
- Its dependency (#87, the `permissions` table, migration 0017) is merged in PR #89, so `0018_user_roles.sql` is the right number.
- Risk is high: a production migration that drops a column, plus the auth and session path. The main guarantee is that every existing user keeps exactly the access they had.

## Scope
Build exactly what the issue specifies:
- Migration `0018_user_roles.sql` (can run twice safely). It creates `user_roles(user_id, role, assigned_at, assigned_by)` with PK `(user_id, role)` and an index on `role`. While `users.role` still exists, it backfills one row per user from `users.role` with `assigned_by` NULL, then drops `users.role`. `user_roles.role` has no `ON DELETE`, so a role that someone still holds cannot be deleted. Update `init.sql` to match, using the same constraint names Postgres gives the migration's FKs.
- `effective_permissions(roles, stored)`: every code if `admin` is among the roles, otherwise the stored codes that are in the catalog.
- `UserRecord.roles` (sorted list) replaces `role`. `create_user` takes `roles=`. `update_user` takes only `active`. New functions: `set_user_roles`, `assign_role`, `unassign_role`, `get_user_permissions` (union in one query) and `list_users_with_role`, all exported from `app.core.db`. `count_active_users_with_role` and `RoleRecord.user_count` count `user_roles` rows.
- `current_user()` computes permissions from all roles on each request. The session no longer stores the role, and old cookies that still carry `role` keep working. The login log line lists all roles. `ROLES`, `ROLE_PRODUCCION` and `ROLE_PUNTO_VENTA` are removed if nothing but a test uses them, and `ROLE_ADMIN` stays.
- `backfill_users` writes the role into `user_roles`. `BACKFILL_USERS_VERSION` does not change.
- Tests and Spanish docs as listed in the issue. Tests that apply the historical migrations 0004 and 0015 directly on `public` move to throwaway schemas that have the old `users` table. Those `.sql` files are not edited.

Left out, as the issue says: the users and roles admin screens (#65, #67), role hierarchies, separation of duties, choosing which roles are active per session, per-user permissions, and any change to route permissions or seeded role permissions.

## Why
The current code matches what the issue describes. `app/core/permissions.py` has `effective_permissions(role: str, ...)` for one role. About 28 places in `app/` and `tests/` read `.role`, `users.role` or `session["role"]`. Only the core DB layer calls `create_user` and `update_user`, so no screen breaks. The work is large but it all belongs together: splitting it would leave two places that hold role assignments. The issue's "access is unchanged" test and its migration test cover the main risk. The UI surface is the nav and the 403 message for a user with no roles.
