# Permission-based route access, checked on every request

> Issue #63: Check route access by role permissions on every request

## Summary
- Every protected route checks a permission code of the logged-in user's role. The role and its permissions are read from the database on each request, in place of the hard-coded `FULL_ACCESS_ROLES` and `POS_ROLES` sets.
- A deactivated user is logged out on the next request, and a role change takes effect on the next request.
- The nav comes from a new `NAV_ITEMS` list, filtered by permission. A user with no permissions gets a Spanish 403 page on `/`.
- Access for `admin`, `point_of_sale` and `production` stays exactly the same, through the `role_permissions` seed from #62.
- `docs/WEB.md` and `docs/DATABASE.md` are updated, and the new tests come from the issue's acceptance criteria 2 to 6.

## Context
- **`app/web/auth.py`** today holds three decorators: `login_required` (admin only), `consulta_allowed` (any logged-in role) and `pos_allowed` (`POS_ROLES` = admin and point_of_sale). They check `session["role"]` against `FULL_ACCESS_ROLES` and `POS_ROLES`.
  - The stale-session rule is `_session_is_stale()`: a session missing `user`, `role` or `user_id`. `_redirect_to_login()` clears the session and redirects with `?next=`.
  - `landing_url()` picks `/ingest` for `POS_ROLES` and `/records` for everyone else.
  - These stay and are still used: `ROLE_ADMIN`, `ROLE_PRODUCCION`, `ROLE_PUNTO_VENTA`, `ROLES`, `load_users`, `load_roles` (used by `app/schema_migrate.py`), `authenticate`, `verify_credentials` and `is_safe_next`.
- **`app/core/permissions.py`** (from #62) holds the `PERMISSIONS` catalog, `PERMISSION_CODES`, and `effective_permissions(role, stored)`, which gives admin every code.
- **`app/core/db/roles.py`** has `get_role_permissions(conn, name)`. **`app/core/db/users.py`** has `get_user_by_id(conn, user_id)`, which returns `UserRecord` with `role` and `active`, and `update_user(conn, user_id, *, role, active)`. Both are re-exported from `app.core.db`. Connections come from `with db.get_conn() as conn:`, as in `authenticate()`.
- **Seed data**: `init.sql` and `app/migrations/0015_roles_and_permissions.sql` seed `roles` and `role_permissions`.
  - `point_of_sale` has `orders.capture`, `cash_summary.view`, `declarations.review`, `records.view` and production codes.
  - `production` has `records.view` and production codes.
  - These rows reproduce today's access one to one under the issue's mapping.
- **Routes that use the old decorators** (from `grep -rn "pos_allowed\|login_required\|consulta_allowed" app/`):
  - `app/web/app.py`: `/` uses `consulta_allowed`.
  - `app/web/blueprints/cash_summary.py` (2 routes), `declarations.py` (10 routes) and `ingest.py` (3 routes) use `pos_allowed`.
  - `app/web/blueprints/records.py`: `GET /records`, `GET /records/<id>` and `POST /records/search-pdf` use `consulta_allowed`. `POST /records/<id>/save` uses `pos_allowed`. `POST /records/delete` uses `login_required`.
  - `review.py` (4 routes), `inventory.py` (2 routes), `pricing.py` (2 routes) and `customers.py` (3 routes) use `login_required`.
  - `auth_routes.py`, `public_declaration.py` and `/healthz` are open.
- **`app/web/app.py`**: the context processor `_inject_full_access_roles` exposes `full_access_roles` and `pos_roles` to templates. The module docstring describes the old decorators.
- **`app/web/blueprints/auth_routes.py`**: `GET /login` redirects to `auth.landing_url()` when `session.get("user")` is set. `POST /login` sets `user`, `role` and `user_id` in the session.
- **Templates that check a role**:
  - `_nav.html` uses `pos_roles` for Registrar, Cuadre and Declaraciones, and `full_access_roles` for Revisión, Inventario, Precios and Clientes. Registros is unconditional. The logout form sits at the end.
  - `declaration_detail.html:79` sets `is_admin`, which gates the customer links on lines 183 and 277.
  - `ingest.html:169` gates the Clientes link.
  - `records.html:118` gates the delete explanation text.
  - `_orders_table.html:15` gates the delete button.
  - `record_detail.html:50` sets `can_edit`.
- **Tests**:
  - The `clean_db` fixture in `tests/conftest.py` runs each test inside savepoints and rolls everything back. It reseeds the three test users. It does not truncate `roles` or `role_permissions`, so those keep the `init.sql` seed, and a test may change them safely.
  - `tests/web/conftest.py` provides `client`, `authenticated_client` (admin), `produccion_client` and `punto_venta_client`.
  - `tests/web/test_roles.py` covers access per role.
  - `tests/web/test_auth.py` has `TestFullAccessRoles`, `TestPosRoles` and `TestPosAllowed`, which use a standalone Flask app with a fake session and no DB. They test the symbols this job removes.
- **Docs**:
  - `docs/WEB.md` has a `## Roles y permisos (auth.py)` section, an `Auth` column in the routes table that names the old decorators, and rows in the templates table for `_nav.html` and `record_detail.html` that mention `pos_roles` and `full_access_roles`.
  - `docs/DATABASE.md:313` (the `users.role` row) says live access is still decided by the fixed role sets and mentions `POS_ROLES` and `@pos_allowed`.

## Requirements
1. **`app/web/auth.py`: `current_user()`.**
   - It returns the current `UserRecord`, or `None` when the session has no `user_id` or no user has that id.
   - On the first call in a request it loads the user with `db.get_user_by_id(conn, session["user_id"])`. It loads the permissions with `permissions.effective_permissions(user.role, db.get_role_permissions(conn, user.role))`, in one `db.get_conn()` block.
   - It caches the user and the permission set on `flask.g`. Later calls in the same request don't query again.
2. **`app/web/auth.py`: `authenticated` decorator.** The checks run in this order:
   - (a) If `_session_is_stale()`, return `_redirect_to_login()`.
   - (b) If `current_user()` is `None` or the user is inactive, return `_redirect_to_login()`, which clears the session.
   - (c) If `user.role != session["role"]`, set `session["role"] = user.role`.
   - (d) Call the view.
3. **`app/web/auth.py`: `permission_required(*codes)`.** A decorator factory that runs the same checks as `authenticated`. It then calls `abort(403)` unless the user's permission set holds at least one of `codes`.
4. **`app/web/auth.py`: `has_permission(code) -> bool`.** True only if there is an active current user whose permission set contains `code`. It returns False, and never raises, when there is no session.
5. **`app/web/auth.py`: removals.** Remove `login_required`, `consulta_allowed`, `pos_allowed`, `FULL_ACCESS_ROLES` and `POS_ROLES`. Keep `ROLE_ADMIN`, `ROLE_PRODUCCION`, `ROLE_PUNTO_VENTA`, `ROLES`, `load_users`, `load_roles`, `authenticate`, `verify_credentials` and `is_safe_next`. Update the module docstring and the docstrings that mention removed names.
6. **Route mapping.** Apply exactly this mapping:
   - Every route in `ingest.py`, plus `POST /records/<id>/save`, uses `permission_required("orders.capture")`.
   - Every route in `cash_summary.py` uses `cash_summary.view`.
   - Every route in `declarations.py` uses `declarations.review`.
   - `GET /records`, `GET /records/<id>` and `POST /records/search-pdf` use `records.view`.
   - `POST /records/delete` uses `records.delete`.
   - Every route in `review.py` uses `duplicates.review`.
   - Every route in `inventory.py` uses `inventory.manage`.
   - Every route in `pricing.py` uses `pricing.manage`.
   - Every route in `customers.py` uses `customers.manage`.
   - `/` in `app/web/app.py` uses `authenticated`.
   - `/login`, `/logout`, `/healthz` and `/declaracion` stay undecorated.
   - Every route takes one code, and no route that writes accepts `records.view`.
   - Update blueprint docstrings that name the old decorators.
7. **`app/web/navigation.py` (new).**
   - It holds an ordered `NAV_ITEMS` list of tuples `(endpoint, label, permission, active_endpoints)`. Use today's `_nav.html` order, labels and active-endpoint sets:
     - `("ingest.ingest", "Registrar", "orders.capture", ("ingest.ingest", "ingest.ingest_pdf"))`
     - `("cash_summary.cuadre", "Cuadre", "cash_summary.view", ("cash_summary.cuadre", "cash_summary.cuadre_pdf"))`
     - `("declarations.declarations", "Declaraciones", "declarations.review", ("declarations.declarations", "declarations.declaration_detail"))`
     - `("review.review", "Revisión", "duplicates.review", ("review.review",))`
     - `("inventory.inventory", "Inventario", "inventory.manage", ("inventory.inventory",))`
     - `("pricing.precios", "Precios", "pricing.manage", ("pricing.precios",))`
     - `("customers.customers", "Clientes", "customers.manage", ("customers.customers", "customers.customer_detail"))`
     - `("records.records", "Registros", "records.view", ("records.records", "records.record_detail"))`
   - Add a short comment saying that later tasks append entries here.
8. **`landing_url()`.** It returns `url_for(endpoint)` for the first `NAV_ITEMS` entry whose permission `has_permission()` grants. When there is none, it returns `url_for("index")`.
9. **`/` index.** It redirects to the first permitted nav item. When the user has none, it answers 403 with a small page in a new template, `app/web/templates/no_permissions.html`. The page includes `_nav.html`, so the Salir button is there, and it contains the exact text `Tu usuario no tiene permisos asignados. Pide a un administrador que te asigne un rol.` There must be no redirect loop.
10. **Context processor.** Replace `_inject_full_access_roles` in `app/web/app.py` with one that exposes `can` (equal to `auth.has_permission`) and `nav_items` (`NAV_ITEMS` filtered to the entries the user may open). It must work on pages with no session, such as `login.html` and the public `/declaracion` pages: `can(...)` returns False there and `nav_items` is empty. It must not query the DB unless a session `user_id` is present.
11. **`_nav.html`.** Render `nav_items` in a loop. Keep the same `<nav class="tabs">` markup, the same `<a href=... class="on">` active logic using each item's `active_endpoints`, the `<style>` block, and the logout form unchanged.
12. **Template conditions.** Replace each role check with the `can(...)` check that means the same thing:
    - `declaration_detail.html`: `is_admin` becomes `can('customers.manage')`. The variable may be renamed.
    - `ingest.html:169`: the Clientes link becomes `can('customers.manage')`.
    - `records.html:118` and `_orders_table.html:15`: these become `can('records.delete')`.
    - `record_detail.html:50`: `can_edit` becomes `can('orders.capture')`.
    - After this, `grep -rn "full_access_roles\|pos_roles\|session.get('role')" app/web/templates` prints nothing.
13. **Tests.**
    - All existing tests under `tests/web/` must keep passing, and access per role must not change. `test_roles.py` and the other route tests may change only imports and fixture names.
    - In `tests/web/test_auth.py`, replace `TestFullAccessRoles`, `TestPosRoles` and `TestPosAllowed` (and the `pos_app` / `pos_client` fixtures), which test removed symbols, with DB-backed tests for `permission_required` and `authenticated`. Those tests cover: no session redirects to `/login`; a session missing `role` or `user_id` redirects to `/login`; a user who has the permission gets 200; a user who lacks it gets 403.
    - Add the new tests from Acceptance criteria 2 to 6. They can live in `tests/web/test_roles.py` or a new `tests/web/test_permissions.py`. They use `clean_db` and `db_conn`, and the `db` helpers `update_user`, `create_role`, `create_user` and `generate_password_hash`.
14. **Docs (Spanish prose, English identifiers).**
    - `docs/WEB.md`:
      - Rewrite `## Roles y permisos (auth.py)`. Cover the permission catalog (`app/core/permissions.py`), the seeded roles and their permissions, the per-request check (`current_user` cached on `g`, `authenticated`, `permission_required`, `has_permission`), deactivation and role changes taking effect on the next request, admin always having every permission, `landing_url()` and the 403 page with no permissions, and `NAV_ITEMS` in `app/web/navigation.py`.
      - Update the `Auth` column of every routes-table row to `@permission_required("<code>")` or `@authenticated`.
      - Update the template rows for `_nav.html`, `record_detail.html` and any other row that names `pos_roles` or `full_access_roles`, and add a row for `no_permissions.html`.
    - `docs/DATABASE.md`: update the `users.role` row (around line 313) so it says access is decided per request from `role_permissions`, and drop the references to `POS_ROLES` and `@pos_allowed`.
    - Update the docstrings in `app/core/permissions.py`, `app/core/db/roles.py` and `app/web/app.py`, and any code comment that says the routes don't read these tables yet.

## Acceptance criteria
1. Admin, point_of_sale and production reach exactly the routes they reached before, and are refused exactly the ones they were refused before. Verify with `.venv/Scripts/python -m pytest tests/web` (Postgres running). Everything passes, and `git diff origin/main -- tests/web/test_roles.py` shows only import or fixture-name changes.
2. A logged-in user who is set to `active=false` in the DB (`db.update_user`) is redirected to `/login` on their next request to a protected route, and their session is cleared. Verified by a new test.
3. A logged-in `production` user gets 403 on `GET /ingest`. After `db.update_user` sets their role to `point_of_sale`, the same client gets 200 on `GET /ingest`. Verified by a new test.
4. A new test creates a custom role with only `inventory.manage` (`db.create_role`) and a user with that role, then logs in. The test checks:
   - `GET /inventory` is 200 and `GET /records` is 403.
   - The `/inventory` HTML has `href="/inventory"` and the text `Inventario`, and has none of `href="/ingest"`, `href="/cuadre"`, `href="/declaraciones"`, `href="/review"`, `href="/precios"`, `href="/customers"` or `href="/records"`.
   - `GET /` redirects to `/inventory`.
5. A new test checks that a logged-in user whose role has no permissions gets status 403 on `GET /`, and that the body contains `Tu usuario no tiene permisos asignados. Pide a un administrador que te asigne un rol.`
6. A new test deletes every `role_permissions` row with `role = 'admin'`. The admin client then still gets 200 on `/ingest`, `/cuadre`, `/declaraciones`, `/review`, `/inventory`, `/precios`, `/customers` and `/records`.
7. `grep -rn "pos_allowed\|consulta_allowed\|login_required\|full_access_roles\|pos_roles" app/` prints nothing.
8. `grep -n "FULL_ACCESS_ROLES\|POS_ROLES" app/web/auth.py` prints nothing. `grep -n "def load_users\|def load_roles\|^ROLES\|^ROLE_ADMIN" app/web/auth.py` still finds all four.
9. `app/web/navigation.py` exists and defines `NAV_ITEMS` with the eight entries in the order listed in Requirement 7.
10. `docs/WEB.md` has no `@pos_allowed`, `@login_required` or `@consulta_allowed` left in its routes table. Its `## Roles y permisos` section mentions `permission_required`, `role_permissions` and that admin always has every permission. Verify with `grep -n "pos_allowed\|login_required\|consulta_allowed\|full_access_roles\|pos_roles" docs/WEB.md docs/DATABASE.md`, which prints nothing.
11. `GET /login` renders without error with no session, and so does `GET /declaracion` (or its 503 page), which shows that the context processor works without a user. Covered by the existing `tests/web/test_login.py` and `tests/web/test_public_declaration.py`.

## Out of scope
- Screens to manage users or roles, and any new route (production board, users).
- CSRF tokens, password policy, login throttling.
- New permission codes or changes to the catalog in `app/core/permissions.py`.
- Editing `app/migrations/0015_roles_and_permissions.sql` or any other applied migration, even comments. No new migration is needed.
- A custom 403 page for the general permission-denied case. Only `/` with no permissions gets the Spanish page; other routes keep Flask's default `abort(403)`.
- Showing role labels anywhere in the UI.
- Changing `factory/checks.sh` or `factory/config.json` (protected).

## Assumptions
- `landing_url()` falls back to `url_for("index")` when the user has no permitted nav item, and `/` renders the 403 page. This avoids a redirect loop, and `POST /login` and `GET /login` keep calling `landing_url()` unchanged.
- The "small page" for no permissions is a new `no_permissions.html` that includes `_nav.html`. This keeps the Salir button reachable, because the nav then holds only the logout form.
- The Registros nav link, which shows for every logged-in user today, is gated by `records.view`. All three seeded roles hold it, so nothing visible changes for them.
- `test_auth.py`'s `TestFullAccessRoles`, `TestPosRoles` and `TestPosAllowed` are replaced, not kept. They test symbols the issue says to remove, and the "only imports and fixture names" rule in acceptance criterion 1 applies to the route-access tests.
- A legacy non-system role that #62 backfilled with no permissions loses its read access to `/records` and gets the 403 page on `/`. This is intended, and intake noted it.
- The decorator for a route with no permission is named `authenticated`, as the issue says, and `permission_required` takes one or more codes as positional arguments.
