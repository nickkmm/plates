VERDICT: PASS

## Summary
- Role assignments move from `users.role` to a new `user_roles` table. Permissions are now the union over all of a user's roles, and `admin` in any role still grants everything.
- Access is still decided from the database on every request. The session no longer stores the role, and an old cookie's `role` value is ignored, so a forged or stale value grants nothing.
- Migration 0018 backfills each user's single old role. Nobody gains or loses access, and the access-unchanged test pins this.
- All new SQL uses psycopg placeholders. No secrets, data files or new dependencies are committed, and the login log line holds only the username and role names.

## Findings
None.

## Notes
- `app/web/auth.py`: `_session_is_stale` and `_check_session` drop every use of `session["role"]`. `current_user()` builds permissions from `db.get_user_permissions(conn, user.id)` plus `user.roles`, both read fresh from the database on each request. Nothing in `app/web` (templates included) still reads a role from the session. Test (d) covers a cookie that still carries `role`, so trusting the cookie can't come back unnoticed.
- `app/core/permissions.py`: `effective_permissions` still grants everything based only on the role name `admin`, and now any one row in `user_roles` is enough. The screens to come (#65, #67) that call `assign_role`/`set_user_roles` must sit behind `users.manage`/admin. They should also block an admin from removing the last active admin (`count_active_users_with_role` exists for that). They must also not let anyone create or rename a role to `admin`. This is a data-layer change, and those functions have no callers in routes yet.
- `app/core/db/users.py::set_user_roles`: `[]` deletes every row through `NOT (role = ANY('{}'))`. That fails closed (no roles gives 403), and tests at `tests/core/db/test_users.py:195` and `tests/web/test_permissions.py:142` cover it.
- `init.sql` now marks migrations 0001 to 0018 as applied on a fresh database. This doesn't touch security: `backfill_users` still runs, so a fresh volume gets its `AUTH_USERS` accounts with the same admin-by-default rule as before.
- The login log line now writes `roles=admin,production` instead of one role. It contains no personal data or secrets.
