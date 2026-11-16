VERDICT: PASS

## Summary
- The branch adds the admin-only `/usuarios` screen: list, create, edit roles and active flag, and password reset. The rules live in `app/core/user_admin.py`.
- All four routes use `@permission_required("users.manage")`, which only `admin` holds. An unknown id returns 404 before any write.
- Passwords are hashed with werkzeug, never echoed back into the form, and never logged. The list page never shows hashes.
- The self-lockout and last-active-admin rules are enforced on the server. Forged role values are rejected, SQL stays parameterized and Jinja autoescapes all output.
- No new dependencies, secrets, `.env` or business data were committed.

## Findings
None.

## Notes
- No CSRF tokens (out of scope in the spec, and app-wide before this branch). These forms are now the most valuable CSRF target in the app, since they create an admin or reset a password. `SESSION_COOKIE_SAMESITE="Lax"` (`app/web/app.py:111`) blocks cross-site POSTs in current browsers, so this is not blocking. It is a good reason to add CSRF protection app-wide in a later task.
- `app/web/blueprints/users.py` `user_edit` loads the target in one connection, then validates and writes in a second one. Together with the unlocked `count_active_users_with_role`, two admins demoting each other at the same moment could leave no active admin. The spec accepts this race. A `SELECT ... FOR UPDATE` on the admin rows would close it later if needed.
- Any `users.manage` holder can grant `admin` and reset any user's password, including another admin's. Today that is the same set of people (only `admin` holds `users.manage`). The roles-screen task should revisit this before `users.manage` is granted to a non-admin role.
- A password reset doesn't end the target's existing sessions. Deactivation does take effect on the next request, because `auth.py` re-reads the user every time. This is acceptable for an admin-driven reset.
- Duplicate `roles` values in a forged POST are de-duplicated by `create_user`/`set_user_roles` (`dict.fromkeys`), so they cannot cause a 500.
