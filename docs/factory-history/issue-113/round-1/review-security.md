VERDICT: PASS

## Summary
- This is a pure move of login, the session check, `permission_required`, the users/roles admin screens, the permission catalog and the users/roles SQL into `app/auth/`. The 8 files moved with `git mv` and show as renames.
- Access control is unchanged. Every `@permission_required` and `@authenticated` decorator and every role check stays in place; only the import paths changed (`app.web.auth` became `app.auth.decorators`).
- `authenticate` and `current_user` call the same functions, now through `repository.<fn>`. Password hashing (`check_password_hash`, `generate_password_hash`), session keys and the per-request re-read of roles and the active flag are untouched.
- No SQL, templates, migrations, `init.sql`, log messages or dependencies changed, and no secrets or business data were committed.

## Findings
None.

## Notes
- `decorators.py` and `admin_routes.py` now take `get_conn` from `app.shared.db` instead of `app.core.db`. That is the same function object, which `app.core.db` re-exports. `tests/conftest.py` patches `get_conn` on both modules, so the tests still reach the real call path.
- The monkeypatch targets in `tests/web/test_role_admin.py` (`repository.delete_role`, `service.validate_role`) match how `admin_routes.py` calls them, so the role-delete and validation tests still exercise the guarded code. The test diff only rewrites the `db.` prefix to `repository.` and changes imports. No assertion is removed or weakened, and the collected count is unchanged (1844).
- The log lines in `admin_routes.py` (user created, password reset) still log only the username and role names, never passwords or hashes. They are unchanged from main.
