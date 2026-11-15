VERDICT: PASS

## Summary
- Adds a data layer only: the permission catalog, the `roles`/`role_permissions` tables with seed rows, a FK from `users.role`, and CRUD functions. Nothing in `app/web/` changes, so no route, session or access check changes.
- Every new query in `app/core/db/roles.py` and `app/core/db/users.py` uses psycopg placeholders. The only f-strings add constant SQL fragments or fixed test schema names.
- The seed gives each role the same access it has today. The rows are informational until the web layer reads them, and `effective_permissions` always grants `admin` every code, so an admin can't be locked out.
- No secrets, `.env`, business data or new dependencies are committed. Password hashes are only passed through: `set_user_password` takes a hash the caller already made and logs nothing.

## Findings
None.

## Notes
- `app/core/db/users.py`: `update_user` and `set_user_password` don't check who is calling, which fits the data-layer contract. The future management screen has to put them behind `users.manage`/admin. It also has to re-hash with werkzeug, and it should end or invalidate the sessions of a user who is deactivated or demoted, because `session["role"]` is cached at login.
- `app/core/permissions.py`: `effective_permissions` grants everything based only on the role *name* `admin`. `roles.name` has `ON UPDATE CASCADE` into `users.role`. No function here renames roles, but a future rename or create-role feature must not let anyone create or rename a role to `admin`. It also needs to keep the system-role guards that the spec puts out of scope here.
- `app/migrations/0015_roles_and_permissions.sql`: unknown legacy `users.role` values are backfilled with no permissions, which is fail-closed and the right default for when the routes start reading these tables.
- `UserRecord` (and so `list_users`) now also carries `created_at`. It already carried `password_hash`, which is not new here, but a future user-list template must not render or serialize the whole record.
