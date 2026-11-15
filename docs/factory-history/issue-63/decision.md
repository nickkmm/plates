ESCALATE

## Summary
- Issue #63 is built in PR #83. Each protected route now checks a permission of the user's role, read from the DB on every request, in place of the hard-coded role sets.
- Round 2 passed: `GATE: PASS`, and the code, security, UI and UX reviews are all `VERDICT: PASS` with no findings. The only round-2 fix added a heading to the no-permissions page.
- The change stays inside intake's Scope. It adds no migration, env var, Docker, CI or dependency change, and `docs/` was updated to match.
- I'm escalating because the change rewrites login, session and route-permission checks for every route. Project rules require a person to check that before merge.
- The gate ran without Postgres, so the DB-backed role and permission tests were skipped there (990 skipped). Only the builder's local run (729 passed) shows they pass.

## Why
The spec's criteria are met as far as I can verify, and the route mapping I checked matches the issue exactly. However, this change replaces the authentication and authorization layer on every protected route, and every push to `main` deploys to production. The project rules say a person must check any change to login, sessions, user roles or route permissions before it merges. The gate also didn't run the tests that prove each role keeps the same access, so that evidence comes only from the builder's report.

## Acceptance criteria
1. Same access for admin, point_of_sale and production: met per build.md (729 passed with Postgres). I confirmed that `git diff origin/main...HEAD -- tests/web/test_roles.py` is empty, so the file is unchanged. The gate skipped these tests because it had no DB.
2. A deactivated user is redirected to `/login`: met. `TestDeactivation` in `tests/web/test_permissions.py`, and `_check_session()` in `app/web/auth.py` clears the session when the user is missing or inactive.
3. A role change takes effect on the next request: met. `TestRoleChange` covers it. The user is reloaded from the DB on each request through `current_user()`, which caches on `flask.g` only for that request.
4. A custom role with only `inventory.manage`: met per build.md (`TestCustomRole`: route access, nav links and landing on `/`).
5. A role with no permissions gets 403 on `/` with the exact Spanish text: met. `TestNoPermissions` covers it, and the `index()` code path renders `no_permissions.html` with 403.
6. Admin keeps everything with no `role_permissions` rows: met per build.md (`TestAdminAlwaysHasEveryPermission`). It relies on `permissions.effective_permissions`.
7. No old decorators or role-set names left in `app/`: met. I ran the grep myself and it printed nothing.
8. `FULL_ACCESS_ROLES`/`POS_ROLES` removed, and `load_users`/`load_roles`/`ROLES`/`ROLE_ADMIN` kept: met. The diff shows the removals, and build.md lists the four kept symbols with line numbers.
9. `NAV_ITEMS` has 8 entries in the spec's order: met. I read `app/web/navigation.py` and confirmed the order: Registrar, Cuadre, Declaraciones, Revisión, Inventario, Precios, Clientes, Registros.
10. `docs/WEB.md` is updated: met per build.md's grep. The Spanish `## Roles y permisos` section was rewritten.
11. `/login` and `/declaracion` render with no session: met. The existing tests cover them, and the builder checked both on a dev server.

Spot-check: I listed every route decorator in `app/web/blueprints/`. Each protected route carries the `@permission_required` code the issue's mapping gives it. Writes are never gated on `records.view`: `/records/<id>/save` requires `orders.capture` and `/records/delete` requires `records.delete`. Only `/login`, `/logout`, `/healthz` and `/declaracion` are open.

## For the human
Recommendation: don't merge yet

The code looks correct and complete, and I found no defect. It is held back only because the project rules require a person to check any change to login, sessions, user roles or route permissions, and merging deploys straight to production. Before merging:

1. **Run the DB-backed tests once against Postgres.** The gate skipped them because it had no DB, and the security review asks for the same run. Run `pytest tests/web/test_roles.py tests/web/test_auth.py tests/web/test_permissions.py`, or all of `tests/web`. These tests are the main evidence that admin, point_of_sale and production keep exactly the access they had.
2. **Read the new access-control core:** `current_user()`, `has_permission()`, `_check_session()`, `authenticated` and `permission_required()` in `app/web/auth.py`, and `index()` with `_inject_permissions()` in `app/web/app.py`.
3. **Check that the production DB has the seeded `role_permissions` rows from #62** (migration `0015_roles_and_permissions.sql`). After this merge, a non-admin role with no rows loses all access. In particular, any legacy non-system role that #62 backfilled with no permissions will get the 403 "Tu usuario no tiene permisos asignados" page and lose `/records`. The spec says this is intended, but confirm no real staff account depends on such a role.
4. **Optionally, after deploy:** log in once as admin, point_of_sale and production, and check the nav and landing page in a browser. No reviewer could check the logged-in pages visually.

Files to look at:
- `app/web/auth.py`
- `app/web/app.py`
- `app/web/navigation.py`
- `app/web/blueprints/*.py` (decorator swaps only, including `cash_summary.py`; the cuadre totals logic is untouched)
- `app/web/templates/_nav.html`
- `app/web/templates/no_permissions.html`
- `tests/web/test_auth.py`
- `tests/web/test_permissions.py`
- `docs/WEB.md`
