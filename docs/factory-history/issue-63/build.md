STATUS: READY

# Build: Permission-based route access, checked on every request

## Summary
- Every protected route now uses `@permission_required("<code>")` (and `/` uses `@authenticated`). On each request the code reads the user and their role's `role_permissions` from the DB and caches them on `flask.g`.
- If a user is deactivated, the next request clears their session and redirects to `/login`. A role change applies on the next request. Admin always has every permission.
- The nav comes from the new `NAV_ITEMS` list in `app/web/navigation.py`, filtered by permission. `landing_url()` picks the first permitted entry, and a user with no permissions gets a Spanish 403 page on `/`.
- `FULL_ACCESS_ROLES`, `POS_ROLES`, `login_required`, `consulta_allowed` and `pos_allowed` are gone. Templates use `can('<code>')` instead.
- Round 2: the no-permissions page now has an `<h1>Sin permisos</h1>` heading, and its message is in normal ink. Docs (`WEB.md`, `DATABASE.md`, `CORE.md`) are updated. New DB-backed tests are in `test_auth.py` and the new `test_permissions.py`. Draft PR: https://github.com/nickkmm/plates/pull/83

## Changes
- `app/web/auth.py`:
  - Added `current_user()`, which loads the user and `effective_permissions` in one `db.get_conn()` block and caches them on `g`.
  - Added `has_permission(code)`, the shared `_check_session()`, `authenticated` and `permission_required(*codes)`.
  - `landing_url()` now walks `NAV_ITEMS` and falls back to `url_for("index")`.
  - Removed the old role sets and decorators, and updated the module docstring.
- `app/web/navigation.py` (new): `NAV_ITEMS`, with the 8 entries in the order the spec gives, plus the "later tasks append here" comment.
- `app/web/app.py`:
  - The `_inject_permissions` context processor exposes `can` and `nav_items`. It only queries the DB when the session has a `user_id`.
  - `/` uses `@authenticated`. It redirects to the landing entry, or renders `no_permissions.html` with a 403 when there is none.
  - Updated the module docstring.
- Blueprints:
  - `ingest.py` (3 routes): `orders.capture`.
  - `cash_summary.py` (2 routes): `cash_summary.view`.
  - `declarations.py` (10 routes): `declarations.review`.
  - `review.py` (4 routes): `duplicates.review`.
  - `inventory.py` (2 routes): `inventory.manage`.
  - `pricing.py` (2 routes): `pricing.manage`.
  - `customers.py` (3 routes): `customers.manage`.
  - `records.py`: `records.view` on the 3 read routes, `orders.capture` on `/save`, `records.delete` on `/delete`.
  - Updated the docstrings that named the old decorators, including `auth_routes.py`.
- Templates:
  - `_nav.html` loops over `nav_items`; the markup, `<style>` and logout form are unchanged.
  - `no_permissions.html` (new) includes `_nav.html`. It has an `<h1>Sin permisos</h1>` (1.4rem, like `records.html`) and shows the required text as a normal paragraph in `#222` ink. Body max-width is 1040px.
  - `declaration_detail.html` uses `can('customers.manage')` for `is_admin`.
  - The Clientes link in `ingest.html` uses `can('customers.manage')`.
  - `records.html` and `_orders_table.html` use `can('records.delete')`.
  - `record_detail.html` uses `can('orders.capture')` for `can_edit`.
- `app/core/permissions.py` and `app/core/db/roles.py`: docstrings no longer say that routes ignore these tables.
- Tests:
  - `tests/web/test_auth.py`: `TestFullAccessRoles`, `TestPosRoles`, `TestPosAllowed` and the `pos_app`/`pos_client` fixtures are replaced by DB-backed `TestPermissionRequired`, `TestAuthenticated` and `TestHasPermission`. They run on a standalone app with the real seeded users.
  - `tests/web/test_permissions.py` (new): covers ACs 2 to 6.
  - `tests/web/conftest.py`: docstring wording only.
  - `tests/web/test_roles.py` is untouched.
- Docs:
  - `docs/WEB.md`: rewrote `## Roles y permisos (auth.py)`, filled the Auth column of every route row, updated the `_nav.html` and `record_detail.html` rows and the `/` row, and added a `no_permissions.html` row.
  - `docs/DATABASE.md`: the `users.role` row.
  - `docs/CORE.md`: the permissions and `db/roles.py` sections no longer say that "routes don't use it yet".

## Acceptance criteria
1. [x] Same access per role: `.venv/Scripts/python -m pytest tests/web` with Postgres → 729 passed, 0 skipped. `git diff origin/main -- tests/web/test_roles.py | wc -l` → 0, so the file is unchanged.
2. [x] Deactivation logs the user out: `test_permissions.py::TestDeactivation` sets `active=False` via `db.update_user`. The next `/ingest` gives 302 to `/login`, and the session has no `user`/`user_id`. Passes. `test_auth.py` also covers it for both decorators.
3. [x] Role change applies on the next request: `TestRoleChange` shows production gets 403 on `/ingest`, then after `update_user` sets `point_of_sale` the same client gets 200. Passes.
4. [x] Custom `inventory.manage`-only role: `TestCustomRole` checks:
   - `/inventory` gives 200 and `/records` gives 403.
   - The HTML has `href="/inventory"` and `Inventario`, and none of the other 7 hrefs.
   - `/` redirects to `/inventory`.
   Passes.
5. [x] Role with no permissions: `TestNoPermissions` gets 403 on `/`. The body has the exact Spanish text and `Salir`. Passes.
6. [x] Admin with no `role_permissions` rows: `TestAdminAlwaysHasEveryPermission` deletes the admin rows. All 8 screens still give 200. Passes.
7. [x] `grep -rn "pos_allowed\|consulta_allowed\|login_required\|full_access_roles\|pos_roles" app/` prints nothing.
8. [x] `grep -n "FULL_ACCESS_ROLES\|POS_ROLES" app/web/auth.py` prints nothing. The `load_users`/`load_roles`/`^ROLES`/`^ROLE_ADMIN` grep finds all 4 (lines 46, 61, 64, 89).
9. [x] `app/web/navigation.py` defines `NAV_ITEMS` with the 8 entries in the order from Requirement 7.
10. [x] `grep -n "pos_allowed\|login_required\|consulta_allowed\|full_access_roles\|pos_roles" docs/WEB.md docs/DATABASE.md` prints nothing. The `## Roles y permisos` section mentions `permission_required`, `role_permissions`, and that admin always has every permission.
11. [x] Pages with no session: `test_login.py` and `test_public_declaration.py` pass in the run above. Against a dev server on port 5140 (local DB), with no session:
    - `GET /login` gives 200 and the page shows "Iniciar sesión".
    - `GET /declaracion` gives 200.
    - `/` and `/inventory` redirect to `/login?next=...`.

    I couldn't check the logged-in pages in a browser: `agent-browser` isn't installed, and the local `plates` DB has no users. The test client covers the logged-in nav and the 403 page (ACs 4 and 5).

## Validation
- Round 2: `bash factory/checks.sh` (gate-style) exits 0 (590 passed, 990 skipped). `pytest tests/web/test_permissions.py tests/web/test_login.py` with Postgres: 30 passed.
- `bash factory/checks.sh` (the way the gate runs it, without a DB password): ruff, mypy, 590 passed and 990 skipped, shell and factory checks. Exit 0, pass.
- `bash factory/checks.sh` with `POSTGRES_PASSWORD` set (shared local `plates_test`): 1579 passed, 1 failed. The failure is `tests/test_schema_migrate.py::test_every_migration_file_is_recorded_as_applied`. The shared test DB records `0016_production_status.sql`, which another concurrent factory job's branch applied; this branch has no such migration. It's an environment issue, not caused by this change, and CI uses a fresh DB.
- `.venv/Scripts/python -m pytest tests/web` with Postgres: 729 passed.
- `ruff check app/ tests/web/test_auth.py tests/web/test_permissions.py`: pass. `mypy app/`: pass.
- Greps for ACs 7, 8, 10 and 12: as expected (see above).

## Log
### Round 1
- Set up `.venv` in the worktree from `requirements-dev.txt`.
- Implemented the spec in 3 commits:
  - `Check route access by role permissions on every request`
  - `Test per-request permission checks`
  - `Document permission-based route access`
- Pushed `factory/004-check-route-access-by` and opened draft PR #83. Following the repo's CLAUDE.md, the PR body has no AI-attribution line.
- Left `app/migrations/0015_roles_and_permissions.sql` untouched, even though its comment says nothing reads these tables yet (the spec puts applied migrations out of scope).

### Round 2
- Code, security and UX reviews: PASS, no findings.
- UI review, finding 1 (`no_permissions.html` had no `<h1>`, and its message was in `.muted` grey): added `<h1>Sin permisos</h1>` with the `records.html` rule (`h1 { font-size: 1.4rem; }`). The message is now a plain `<p>` in the default `#222` ink, with the exact text kept. I also applied the review's note to match the 1040px body width. Commit `Add a heading to the no-permissions page`, pushed to PR #83.
- I didn't change the code review's optional note (the `landing_url() == url_for("index")` comparison): the behaviour is correct, and the reviewer didn't ask for a change.
