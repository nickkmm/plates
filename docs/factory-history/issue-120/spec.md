# Jinja pages authenticate from the JWT access-token cookie

> Issue #120: Make the Jinja pages use the JWT cookie: one login for Flask and Next

## Summary
- Every Flask page now checks the user and their permissions from the `access_token` JWT cookie, through a new `app/shared/auth.py`, with no database read. `app/auth/decorators.py` is deleted.
- When a genuine access token has expired, the page redirects through a new `GET /api/auth/refresh/continue`, which rotates the tokens and sends the user back. Anyone else goes to `/login`. `/api` requests get a JSON 401.
- Jinja `/login` and `/logout` use the same login, refresh and logout code as `/api/auth/*`. Logout ends at a new `GET /api/auth/logout/continue`, because `/logout` can't see the refresh cookie. The Flask session only stores the CSRF token now.
- Two changes not in the issue keep the flow working. The `access_token` cookie now lives 30 days, but the JWT inside it still expires after 5 minutes, so the server can tell that a refresh may work. A small script refreshes the token before a POST form is sent, so a form filled in over more than 5 minutes isn't lost.
- Docs (`WEB.md`, `ARCHITECTURE.md`, `DOMAINS.md`, `DATABASE.md`, `CLAUDE.md`) now describe the token login and the new 5-minute window for deactivation and role changes.

## Context
- **Today's auth module**: `app/auth/decorators.py` holds:
  - `permission_required`, `authenticated`, `current_user` and `has_permission`. They read `session["user_id"]` and load the user, their roles and their permissions from the database on every request, cached on `flask.g`.
  - `authenticate` and `verify_credentials` (database login).
  - `is_safe_next` (the open-redirect guard) and `landing_url` (the first `NAV_ITEMS` entry the user may open).
  - `load_users`, `load_roles` and `ROLE_ADMIN`, which only `app/schema_migrate.py` (backfill) uses.
- **Who imports it**: `app/web/app.py`, `app/auth/routes.py`, `app/auth/api.py`, `app/auth/admin_routes.py`, `app/customers/routes.py`, `app/inventory/routes.py`, `app/pricing/routes.py`, `app/production/routes.py` (also `has_permission`), `app/cash_summary/routes.py`, `app/orders/routes/{ingest,records,review}.py`, `app/declarations/routes.py` and `app/schema_migrate.py`.
- **Direct session reads** (this is what `rg -n 'session\["user|session.get\("user' app` finds):
  - `app/auth/routes.py`: sets and reads `user` and `user_id`.
  - `app/auth/admin_routes.py`: `assigned_by` and `actor_id`, and the `by=` log lines.
  - `app/pricing/routes.py`: `updated_by=session["user"]`, which stores a username.
  - `app/production/routes.py`: `move(conn, order_id, session["user_id"], ...)` and a log line.
  - `app/orders/routes/ingest.py`: `created_by` and log lines.
  - `app/orders/routes/records.py`: a log line.
  - `app/declarations/routes.py`: `user_id` for review and notarized, and log lines.
  - `app/web/app.py`: `_not_found` checks `"user_id" not in session`.
  - Docstrings mentioning `session["user_id"]` are in `app/orders/service.py`, `app/orders/repository/orders.py` and `app/auth/repository/users.py`.
- **Templates**: they use only `can(code)` and `nav_items`, both from the `_inject_permissions` context processor in `app/web/app.py`. `_nav.html` holds the logout form (`POST /logout`) and is included by every staff page. `ingest_pdf.html` sends a CSRF-carrying `XMLHttpRequest` POST.
- **Token pieces from #119**:
  - `app/shared/tokens.py`: `issue_access_token`, `decode_access_token` (raises `ExpiredTokenError` for a genuine but expired token and `InvalidTokenError` for anything else) and `AccessClaims(user_id, username, perms, ...)`. `ACCESS_TOKEN_LIFETIME` is 5 minutes and `REFRESH_TOKEN_LIFETIME` is 30 days.
  - `app/auth/refresh_tokens.py`: `issue`, `rotate` (reuse revokes the family), `revoke` and `revoke_user`.
  - `app/auth/api.py`: `POST /api/auth/login|refresh|logout`, `GET /api/auth/me` and the cookie helpers (`_set_cookie`, `_clear_cookies`, `_signed_in`).
  - Cookies: `access_token` (`Path=/`, `Max-Age=300`) and `refresh_token` (`Path=/api/auth`, 30 days). Both are `HttpOnly` and `SameSite=Strict`, and `Secure` when `COOKIE_SECURE` is on.
  - `app/shared/api.py`: `is_api_request`, `http_error_response`, and the `X-Requested-With` check on non-GET `/api` requests.
  - `admin_routes.py` already revokes refresh tokens when a user's roles change, when they are deactivated or when their password is reset.
- **The cookie-path problem**: `refresh_token` is on `/api/auth`, so a Flask page request never carries it, and neither does `POST /logout`. The browser drops `access_token` after its 300 s `Max-Age`. So a page can't tell "this user's access token expired" apart from "this visitor never signed in" (see Assumptions 1 and 2).
- **Session config**: `app/web/app.py` sets `PERMANENT_SESSION_LIFETIME=timedelta(hours=12)` and `/login` sets `session.permanent = True`. Flask-WTF stores its CSRF token in the session (`WTF_CSRF_TIME_LIMIT=None`).
- **Tests**:
  - The login fixtures (`authenticated_client`, `produccion_client`, `punto_venta_client`) live in `tests/web/conftest.py` and log in with `POST /login`. `tests/web/_helpers.py` holds the shared helpers.
  - Session-based tests that must change:
    - `tests/web/test_auth.py`: `guarded_app`, `_log_in_as` and the `session_transaction` tests.
    - `tests/web/test_permissions.py`: deactivation and role change "on the next request", and the old cookie with `role`.
    - `tests/web/test_csrf.py`: `TestLogin` and `TestLogout` assert on `session["user"]`.
    - `tests/web/test_pricing.py`: a comment that mentions `session["user"]`.
  - `tests/web/test_public_routes.py`: `PUBLIC_RULES`.
  - `tests/api/test_auth_api.py`: asserts `Max-Age=300` on `access_token`. `tests/api/_helpers.py` provides `api_post` and `api_login`.
- **Docs**:
  - `docs/WEB.md`: "Roles and permissions (`app/auth/decorators.py`)", "CSRF protection", "JSON API (`/api`)", "Public routes" and the route table.
  - `docs/ARCHITECTURE.md`: lines 41-44.
  - `docs/DOMAINS.md` (lines 18, 545-554, 582, 590) and `docs/DATABASE.md` (lines 14, 341-343, 416) refer to `decorators.py` and `session["user_id"]`.
  - `CLAUDE.md`'s `app/auth/` bullet says "session check and `permission_required`".
  - `docs/MIGRATION_PLAN.md`: Phase 2 and "Decisions".

## Requirements

### Shared auth module
1. Add `app/shared/auth.py`. It imports no domain package and does not read the database. It provides:
   - `current_user() -> AccessClaims | None`: the verified claims of the request's `access_token` cookie, or `None` when the cookie is missing, invalid or expired. It is decoded once per request and cached on `flask.g`, using `decode_access_token(token, utc_now())`.
   - `has_permission(code) -> bool`: `code in current_user().perms`, and `False` without a valid token. It never raises.
   - `authenticated` (any valid token) and `permission_required(*codes)` (a valid token holding at least one of `codes`, otherwise `abort(403)`). The wrapper of `permission_required` exposes the codes as the attribute `required_permissions` (a tuple), so tests can enumerate the protected routes.
   - `is_safe_next`, moved here unchanged.
   - The cookie names `ACCESS_COOKIE = "access_token"` and `REFRESH_COOKIE = "refresh_token"`. Define them in `app/shared/tokens.py` or in `app/shared/auth.py`, and import them from there in `app/auth/`, so the names exist in one place.
2. When a request has no valid token, `authenticated` and `permission_required` answer as follows:
   - An `/api` request (`is_api_request()`) gets `http_error_response(401)`, never a redirect.
   - When the `access_token` cookie holds a genuine but expired token (`ExpiredTokenError`), the request is redirected (302) to `url_for("auth_api.refresh_continue", next=<target>)`.
   - Otherwise (no cookie, or `InvalidTokenError`), it is redirected (302) to `url_for("auth_routes.login", next=<target>)`.
   - `<target>` is `request.full_path` without a trailing `?` (as today) for GET and HEAD. For any other method it is `/`, because a redirect turns into a GET and would hit a POST-only URL with a 405.
   - Endpoints are referenced by name (`url_for`), never imported.
3. Delete `app/auth/decorators.py`. Move what is left of it:
   - `authenticate`, `verify_credentials`, `load_users`, `load_roles` and `ROLE_ADMIN` go to a new `app/auth/login.py` (see requirement 6). `app/schema_migrate.py` imports them from there.
   - `landing_url()` goes to `app/web/navigation.py`. It is unchanged, except that it uses `app.shared.auth.has_permission`.

### Every blueprint, template helper and log line
4. Every `@permission_required` and `@authenticated` in `app/` imports from `app.shared.auth`. The same applies to `has_permission` in `app/production/routes.py` and to the context processor and `index` in `app/web/app.py`. Every route keeps exactly the codes it has today.
5. Every `session["user"]` and `session["user_id"]` read is replaced with `current_user().username` and `current_user().user_id`. This covers `created_by`, `assigned_by`, `actor_id`, the `move(...)` user, the declarations' `user_id`, pricing's `updated_by` (still the username) and every `by=`/user log line. `_not_found` in `app/web/app.py` uses `current_user() is None` instead of `"user_id" not in session`. The docstrings that mention `session["user_id"]` are reworded to "the access token's user id".

### Login, refresh and logout
6. `app/auth/login.py` holds the sign-in logic both login paths share. `app/auth/api.py` is refactored onto it, and its JSON contract (status codes, bodies, `Cache-Control: no-store`) stays unchanged apart from requirement 7:
   - `authenticate`.
   - A function that authenticates, computes the effective permissions, issues a refresh token and commits, then logs `login: user=... roles=...`. It returns the user, the permissions and the raw refresh token, or `None` on failure.
   - A function that rotates a raw refresh token and reloads the user. It revokes all the user's tokens when the user is missing or inactive, and returns the same result or `None`.
   - A function that revokes one raw refresh token.
   - `set_token_cookies(response, ...)` and `clear_token_cookies(response)`, moved out of `api.py`.
7. The `access_token` cookie's `Max-Age` becomes `REFRESH_TOKEN_LIFETIME` (2592000 s), the same as `refresh_token`. The JWT inside still expires after `ACCESS_TOKEN_LIFETIME` (5 minutes, its `exp`). The cookie's path stays `/`.
8. New `GET /api/auth/refresh/continue?next=<path>` on the `auth_api` blueprint (endpoint `auth_api.refresh_continue`). It is public, its docstring says it exists only while Jinja pages exist (phase 6 removes it), and it carries `Cache-Control: no-store` like the rest of the blueprint.
   - When the refresh succeeds, it answers 302 to `next` (or to `/` when `next` fails `is_safe_next`) and sets both fresh cookies.
   - When there is no refresh cookie, or the refresh fails, it clears both cookies and answers 302 to `/login?next=<next>`. The `next` is only included when `is_safe_next(next)` holds.
   - It never redirects off-site.
9. New `GET /api/auth/logout/continue` (endpoint `auth_api.logout_continue`), public and Jinja-only like requirement 8. It revokes the `refresh_token` cookie's token if there is one, clears both cookies and answers 302 to `/login`.
10. Jinja `GET /login`: a user with a valid token is redirected to `landing_url()`. Otherwise the form is shown, as today, with a safe `next`.
11. Jinja `POST /login` calls the shared sign-in function from requirement 6. On success it sets the same two cookies as `/api/auth/login` and redirects to the safe `next` or to `landing_url()`. `landing_url()` is computed from the new permissions, because the request itself has no token yet. On failure it shows today's 401 page with "Usuario o contraseña incorrectos.", identical for an unknown user, a wrong password and an inactive user, and sets no cookie. It writes nothing to the session.
12. Jinja `POST /logout` (still POST-only and still CSRF-protected) clears the `access_token` cookie and the Flask session, then answers 302 to `/api/auth/logout/continue`. The refresh token is revoked there, because the browser sends the refresh cookie only to `/api/auth`.
13. The Flask session holds no user data:
    - Remove `PERMANENT_SESSION_LIFETIME` from `app/web/app.py`, and nothing sets `session.permanent`.
    - The session cookie settings (`HttpOnly`, `Lax`, `Secure`) stay as they are, for Flask-WTF.
    - Update the `WTF_CSRF_TIME_LIMIT` comment: the CSRF token now lasts for the browser session.

### Token freshness on pages that post
14. `_nav.html` exposes the seconds left on the current access token (from `current_user().expires_at`), for example as a `data-` attribute. It also gets one small inline script that does the following:
    - It defines `window.platesEnsureFreshToken()`. When fewer than 30 seconds remain, this calls `POST /api/auth/refresh` with `X-Requested-With: XMLHttpRequest` and `credentials: "same-origin"`. On 200 it resets the deadline to 300 seconds from now. It always resolves and never rejects.
    - It intercepts `submit` on every `form[method=post]` of the page, except the logout form, waits for that promise, then submits the form.
    - The XHR in `ingest_pdf.html` awaits the same function before sending.
    - When the refresh fails, the submission goes ahead anyway, and the server's redirect to `/login` applies.
    - The script uses no new dependency and keeps the existing CSRF fields and headers.

### Docs
15. `docs/WEB.md`:
    - Rename "Roles and permissions (`app/auth/decorators.py`)" to point at `app/shared/auth.py`, and rewrite its "Check on every request" and "Login" bullets:
      - Permissions come from the token claims, with no database read.
      - The redirect rules of requirement 2.
      - The two `continue` endpoints.
      - The session only holds the CSRF token.
    - State the behavior change: a deactivated user, a role change or a role's new permissions take effect within 5 minutes (the access token's lifetime), at the next refresh, instead of on the next request. Arriving from an external link (for example WhatsApp) shows `/login`, because `SameSite=Strict` cookies aren't sent on cross-site navigation.
    - Document the new cookie `Max-Age` and the two endpoints in "JSON API (`/api`)", and drop "the Jinja pages still log in through `/login`" and "It never touches the Flask session" where they are no longer accurate.
    - Add the two endpoints to "Public routes" and the route table.
    - Document the freshness script (requirement 14).
    - Update the "CSRF protection" token-lifetime bullet.
16. `docs/ARCHITECTURE.md` (lines 41-44): blueprints import `app.shared.auth.permission_required`, and access comes from the JWT cookie.
17. `docs/DOMAINS.md`, `docs/DATABASE.md`, the `app/web/app.py` module docstring, `app/auth/permissions.py`, `app/auth/admin_routes.py`, `app/auth/routes.py` and the `app/auth/repository/` docstrings no longer name `decorators.py` or the session user. `CLAUDE.md`'s `app/auth/` bullet says login and the users and roles admin live there. Its `app/shared/` bullet lists `auth.py` (`permission_required` from the access-token cookie).

### Tests
18. `tests/web/_helpers.py` gains:
    - `log_in(client, username, password)`: `POST /login`, which leaves both cookies on the client.
    - `set_access_token(client, user_id, username, perms, now=None)`: issues a token with `issue_access_token` and sets it with `client.set_cookie("access_token", ..., path="/")`. A `now` in the past gives an expired token.

    The fixtures in `tests/web/conftest.py` use `log_in`.
19. `tests/web/test_auth.py` is rewritten onto the cookie, against the `guarded_app`. The `guarded_app` registers stand-in `auth_routes.login`, `auth_api.refresh_continue` and `auth_api.logout_continue` endpoints. The tests cover:
    - No cookie redirects to `/login`.
    - An invalid or forged token redirects to `/login`.
    - An expired genuine token redirects to `/api/auth/refresh/continue?next=...`.
    - A non-GET request with an expired token gets `next=/`.
    - A `/api` path answers a JSON 401.
    - A valid token with the code gets 200, and without it gets 403.
    - With `app.shared.db.get_conn` patched to raise, a guarded view still answers 200: no database read.
    - `has_permission` is `False` with no token.

    The `load_users`, `load_roles`, `authenticate` and `is_safe_next` tests stay, importing from their new modules.
20. `tests/web/test_permissions.py` keeps every scenario, adapted to the new rule:
    - Deactivation: the current token still works until it expires. Once it is expired and a refresh is attempted, the user lands on `/login` with the cookies cleared.
    - Role change: it applies after the access token expires and is refreshed.
    - The old-session-cookie test becomes this: a Flask session holding `user`, `user_id` and `role` and no access cookie is sent to `/login`.
21. `tests/web/test_csrf.py` `TestLogin` and `TestLogout` assert on cookies, not the session:
    - Without a token, a rejected login sets no `access_token`.
    - A login with a token sets it.
    - A logout with a token ends with both cookies cleared and the refresh token revoked.
22. `tests/web/test_public_routes.py` adds `/api/auth/refresh/continue` and `/api/auth/logout/continue` to `PUBLIC_RULES`, with a comment. `tests/api/test_auth_api.py` expects `Max-Age=2592000` on `access_token`, and asserts that the JWT's `exp - iat` is still 300.
23. New tests for acceptance criteria 2 to 4 (requirements 8, 9 and 12, and the route walk). Put them in `tests/web/test_token_flow.py` or a similarly named file.

## Acceptance criteria
1. `rg -n 'session\["user|session.get\("user' app` prints nothing. `rg -n "PERMANENT_SESSION_LIFETIME|session.permanent" app` prints nothing.
2. `app/auth/decorators.py` does not exist. `rg -n "app\.auth\.decorators|auth/decorators|import decorators" app tests docs CLAUDE.md README.md -g '!docs/factory-history/**'` prints nothing.
3. `app/shared/auth.py` exists and defines `permission_required`, `authenticated`, `current_user`, `has_permission` and `is_safe_next`. `rg -n "^from app\.(auth|orders|customers|pricing|inventory|production|cash_summary|declarations|web)|^import app\.(auth|web)" app/shared/auth.py` prints nothing.
4. A test logs in, replaces `access_token` with a token issued 6 minutes ago, then does `GET /records`:
   - The first response is a 302 whose `Location` path is `/api/auth/refresh/continue` with `next=/records`.
   - The next response is a 302 to `/records` that sets new `access_token` and `refresh_token` cookies.
   - Following it answers 200.
   - A second `GET /records` answers 200 without any redirect.
5. A test with a valid refresh cookie calls `GET /api/auth/refresh/continue` with `next` set to each of `https://evil.example`, `//evil.example`, `/\evil.example` and `http:evil.example`. Each answers a 302 whose `Location` has no host other than the app's own and a path starting with a single `/`. The same holds without a refresh cookie, where the redirect goes to `/login` and carries no off-site `next`.
6. A test walks `app.url_map` and visits every rule whose view has `required_permissions` (with `<...>` replaced by `1`), for every method except HEAD and OPTIONS. The test fails if it visits fewer than 50 route and method pairs.
   - With no cookie, the answer is a 302 to the `/login` path for HTML routes, and a 401 for `/api` ones.
   - With a token whose `perms` hold every `PERMISSION_CODES` code except the route's `required_permissions`, the answer is 403.
7. A test does `POST /login` with valid credentials. Both cookies are set with the attributes of `/api/auth/login`, the Flask session holds no `user` or `user_id` key, and `GET /api/auth/me` then answers 200 for that user.
   - An unknown user, a wrong password and an inactive user get byte-identical 401 bodies and no `access_token` cookie.
8. A test logs in, sends `POST /logout` (CSRF on, with a token) and follows the redirects. It ends on `/login` with both cookies cleared, the user's refresh token has `revoked_at` set in `refresh_tokens`, and a following `GET /records` redirects to `/login`.
9. A test calls `GET /api/auth/me` with an expired token: it still answers 401 JSON. `GET /api/auth/refresh/continue` answers with `Cache-Control: no-store`.
10. In a test with `app.shared.db.get_conn` patched to raise, a view guarded by `@permission_required` answers 200 for a token holding its code.
11. `app/web/templates/_nav.html` contains `platesEnsureFreshToken` and a call to `/api/auth/refresh` with `X-Requested-With`. `app/web/templates/ingest_pdf.html` calls `platesEnsureFreshToken` before sending its request.
    - Manual check: run the app on the job's port, sign in, wait more than 5 minutes on `/ingest` and submit the form. It is saved, with no redirect to `/login`.
    - Manual check: open `/records` after 5 minutes. It loads (through the refresh redirect) without asking to sign in.
12. `docs/WEB.md` names `app/shared/auth.py`, `/api/auth/refresh/continue` and `/api/auth/logout/continue`, and says deactivation and role changes take effect "within 5 minutes". `docs/ARCHITECTURE.md` names `app.shared.auth`.
13. Every pre-existing test in `tests/web/test_permissions.py`, `test_roles.py`, `test_login.py`, `test_users.py`, `test_role_admin.py` and `test_public_routes.py` still exists (adapted where requirements 20 to 22 say so) and passes.

## Out of scope
- The Next.js login page and front end (phase 3).
- Narrowing the `access_token` cookie path to `/api`, removing Flask-WTF, the session configuration or the `continue` endpoints (phase 6).
- Changing `SameSite=Strict`, the refresh cookie's path, the token claims, the token lifetimes, or the refresh-token rotation and reuse rules from #119.
- Revoking refresh tokens when a role's permissions are edited (#119 chose not to), and any new revocation trigger.
- A periodic background keep-alive, cross-tab coordination, or any `localStorage` use for tokens.
- Changing which permission code any route requires, or the roles and permissions model.
- Any schema change or migration.

## Assumptions
1. **Detecting "a refresh cookie may exist"**: a page treats a genuine but expired access token as the signal, so the access cookie's `Max-Age` grows to 30 days while the JWT still expires after 5 minutes. With `Max-Age=300` the browser drops the cookie before a page could see it expired. The alternatives were a third marker cookie (one more piece of state) or always bouncing anonymous visitors through refresh, which would break today's anonymous-goes-to-`/login` tests.
2. **Logout ends at `GET /api/auth/logout/continue`**: `POST /logout` never receives the `/api/auth` refresh cookie, so it can't revoke the token. The GET is safe from cross-site triggering because `SameSite=Strict` keeps the cookie off cross-site requests, the same reasoning as the GET refresh/continue the issue asks for.
3. **The freshness script** (requirement 14): without it, any form filled in over more than 5 minutes (common on `/ingest`) would be lost to a redirect, where today's session lasted 12 hours. This is a "base, not a ceiling" improvement within the goal, and it must be listed in the PR description.
4. **A non-GET request with an expired token is redirected with `next=/`**: redirecting back to a POST-only URL would answer 405. The script in requirement 14 makes this case rare.
5. **`current_user()` returns `AccessClaims`** (`user_id`, `username`, `perms`), not a new type: it is exactly the claims the issue lists, and the type already exists.
6. **Module layout**: `authenticate` and the backfill parsers go to `app/auth/login.py`, `landing_url` goes to `app/web/navigation.py`, and the cookie names go to `app/shared/`. This keeps `app/shared/` free of domain and web imports, as `CLAUDE.md` requires.
7. **Arriving from an external link shows `/login` even for a signed-in user**: `SameSite=Strict` was decided in #119 and the plan. It is documented as a known effect, not changed. The product owner may want to revisit it.
8. **"Every existing permission test keeps passing"**: tests that assert the old "on the next request" behavior are adapted to the 5-minute rule the migration plan decided, not kept as they are.
