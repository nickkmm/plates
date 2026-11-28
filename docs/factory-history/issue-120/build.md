STATUS: READY

# Build: Jinja pages authenticate from the JWT access-token cookie

## Summary
- Every Flask page now checks the user and permissions from the `access_token` JWT cookie through the new `app/shared/auth.py`, with no database read; `app/auth/decorators.py` is deleted.
- An expired but genuine token redirects through the new `GET /api/auth/refresh/continue` and comes back; anything else goes to `/login`; `/api` gets a JSON 401.
- Jinja `/login` and `/logout` share `app/auth/login.py` with `/api/auth/*`; logout ends at the new `GET /api/auth/logout/continue`, which revokes the refresh token. The Flask session only holds the CSRF token.
- The `access_token` cookie now lives 30 days (the JWT still expires after 5 minutes), and a small script in `_nav.html` refreshes the token before a POST form or the `/ingest/pdf` upload is sent.
- Round 1: first build. Draft PR #199.

## Changes
- `app/shared/auth.py` (new): `current_user()` (decodes the cookie once per request, cached on `flask.g`), `signed_in_user()` (the same, typed non-None, for views behind a guard), `has_permission`, `authenticated`, `permission_required` (sets `required_permissions` on the wrapper), `is_safe_next` (moved unchanged), and the redirect rules of requirement 2. Imports only `app.shared` and Flask.
- `app/shared/tokens.py`: `ACCESS_COOKIE` / `REFRESH_COOKIE` names.
- `app/auth/login.py` (new): `ROLE_ADMIN`, `load_users`, `load_roles`, `authenticate`, `verify_credentials` (moved), `SignedIn`, `sign_in`, `refresh`, `sign_out`, `set_token_cookies`, `clear_access_cookie`, `clear_token_cookies`. Both cookies get `Max-Age=REFRESH_TOKEN_LIFETIME`.
- `app/auth/api.py`: refactored onto `login.py` (JSON contract unchanged apart from the cookie Max-Age); `me` uses `current_user()`; new `refresh_continue` and `logout_continue` GET endpoints.
- `app/auth/routes.py`: GET `/login` redirects a valid token to `landing_url()`; POST `/login` uses `sign_in` and sets the cookies, landing by the new permissions; POST `/logout` clears the access cookie and session and redirects to `/api/auth/logout/continue`.
- `app/web/navigation.py`: `landing_url(permissions=None)` moved here; the optional argument is how POST /login lands by the new permissions.
- `app/web/app.py`: imports from `app.shared.auth`; `PERMANENT_SESSION_LIFETIME` removed; CSRF comment updated; `_not_found` uses `current_user() is None`; the context processor also exposes `token_seconds_left`.
- Every blueprint (`admin_routes`, `customers`, `inventory`, `pricing`, `production`, `cash_summary`, `orders/routes/*`, `declarations/routes`): import from `app.shared.auth`; session reads replaced by `signed_in_user().user_id` / `.username`. Codes unchanged.
- `app/schema_migrate.py`: imports the backfill parsers from `app.auth.login`. Docstrings in `app/auth/__init__.py`, `permissions.py`, `repository/*`, `orders/service.py`, `orders/repository/orders.py` reworded.
- `app/web/templates/_nav.html`: `data-token-seconds` on the nav and the `platesEnsureFreshToken` script (document-level submit listener for `form[method=post]` except `.logout`; only intercepts when under 30 s remain; keeps the submitter's name/value). `ingest_pdf.html`: the XHR awaits it before `send`.
- Tests: `tests/web/_helpers.py` (`log_in`, `set_access_token`, `expire_access_token`), `conftest.py` fixtures use `log_in`; `test_auth.py` rewritten onto the cookie; `test_permissions.py`, `test_users.py`, `test_role_admin.py`, `test_login.py`, `test_csrf.py`, `test_public_routes.py`, `tests/api/test_auth_api.py` adapted; new `tests/web/test_token_flow.py`; comment fixes in other tests.
- Docs: `docs/WEB.md`, `docs/ARCHITECTURE.md`, `docs/DOMAINS.md`, `docs/DATABASE.md`, `CLAUDE.md`.

## Acceptance criteria
1. [x] No session user reads, no permanent session: both `rg` commands print nothing.
2. [x] `app/auth/decorators.py` deleted; `rg -n "app\.auth\.decorators|auth/decorators|import decorators" app tests docs CLAUDE.md README.md -g '!docs/factory-history/**'` prints nothing.
3. [x] `app/shared/auth.py` defines the five functions; the forbidden-import `rg` prints nothing.
4. [x] `tests/web/test_token_flow.py::TestExpiredTokenRefreshes::test_records_goes_through_refresh_and_comes_back` passes (302 to `/api/auth/refresh/continue?next=/records`, 302 to `/records` setting both cookies, 200, then a direct 200).
5. [x] `TestRefreshContinueStaysOnSite` (4 evil `next` values, with and without a refresh cookie) passes.
6. [x] `TestEveryGuardedRoute::test_refuses_without_a_token_and_without_its_code` passes, asserting at least 50 route/method pairs.
7. [x] `TestJinjaLogin` passes: same cookie attributes as the API (`HttpOnly`, `SameSite=Strict`, `Max-Age=2592000`, paths), no `user`/`user_id` in the session, `/api/auth/me` 200; the three failures give identical 401 bodies and no `access_token`. The page's CSRF token is masked before the bodies are compared: Flask-WTF signs it with a timestamp, so two responses a second apart can differ in that one field.
8. [x] `TestJinjaLogout` passes (CSRF on): ends on `/login` via `/logout` and `/api/auth/logout/continue`, both cookies cleared, `revoked_at` set, `GET /records` then goes to `/login`. Also `test_csrf.py::TestLogout::test_post_with_token_logs_out`.
9. [x] `test_me_with_an_expired_token_is_still_a_json_401` and `test_refresh_continue_is_never_cached` pass.
10. [x] `test_auth.py::TestPermissionRequired::test_never_reads_the_database` (and `test_token_flow.py::TestNoDatabaseOnGuardedPages`) pass with `db.get_conn` patched to raise.
11. [x] `_nav.html` contains `platesEnsureFreshToken` and `fetch("/api/auth/refresh"` with `X-Requested-With`; `ingest_pdf.html` calls it before `xhr.send`. Manual checks: `agent-browser` isn't installed here (no Node on PATH), so I ran the app from the worktree on port 5350 against `plates_test_25` and used curl with a cookie jar:
    - Signed in at 01:57:32, then at 02:05:11 `GET /records` with redirects followed ended at `/records` with 200 after 2 redirects (through refresh/continue) and no sign-in prompt. A second GET answered 200 directly.
    - Session B loaded `/ingest` at 01:59:22. At 02:05 a bare `POST /ingest` was redirected to `/api/auth/refresh/continue?next=/`, which is what the script is there to prevent. Doing what the script does (`POST /api/auth/refresh` with `X-Requested-With`, 200) and then posting the same form with the page's CSRF token gave 200 "Registrado", and order 9102 was saved with `created_by=1`.
    - I checked the script itself in headless Edge (`msedge --headless --dump-dom`) with a stubbed `fetch`. With 10 s left, clicking a named submit button made one `POST /api/auth/refresh` (`X-Requested-With: XMLHttpRequest`, `same-origin`) and then submitted the form with the button's name and value. A later `platesEnsureFreshToken()` call did not fetch again. With 300 s left there was no fetch and the native submit went ahead.
12. [x] `docs/WEB.md` names `app/shared/auth.py`, both `continue` endpoints and says "within 5 minutes"; `docs/ARCHITECTURE.md` names `app.shared.auth`.
13. [x] Every existing test in `test_permissions.py`, `test_roles.py`, `test_login.py`, `test_users.py`, `test_role_admin.py` and `test_public_routes.py` still exists under its name and passes. The "next request" ones now expire the token and refresh, or sign in again where `/usuarios` revoked the refresh tokens.

## Validation
- `cd <worktree> && bash factory/checks.sh` (main checkout's script, with `TEST_POSTGRES_DB=plates_test_25`, `TEST_REQUIRE_DB=1`): passed. Ruff and mypy are clean and 1930 tests passed.
- `.venv/Scripts/python -m ruff check app/` and `mypy app/`: passed.
- Manual curl and headless-Edge checks above: passed.

## Notes for reviewers
- `signed_in_user()` is a small addition the spec doesn't name. The spec says `current_user().username`, but `current_user()` returns `Optional`, and mypy (`check_untyped_defs`) rejects that at about 20 call sites. The helper returns the same claims typed non-None and raises `RuntimeError` if a guard is ever missing.
- On a redirect to `/login`, `_refuse` still clears the Flask session, as the old code did. This drops a stale pre-token session cookie (`test_login.py::TestStaleSessionPredatingRoles` stays as it was). It is never done on the refresh path.
- The freshness script only intercepts a submit when the token is about to expire. Otherwise the native submit runs untouched, so existing `confirm()` handlers and submit buttons behave as before.

## Log
### Round 1
- Setup: created `.venv` and installed `requirements-dev.txt`.
- Implemented requirements 1 to 23 in four commits (code, freshness script, tests, docs), pushed, and opened draft PR #199.
- The first full run had 14 failures, all in tests that asserted the old session rule. I adapted them as requirements 20 to 22 say. mypy then rejected werkzeug-vs-flask `Response` types in `api.py`, so I made the cookie helpers generic and annotated the redirect endpoints with werkzeug's `Response`. After that the gate passed.
- The gate hook then failed `test_failures_are_identical_and_set_no_cookie`, an intermittent failure: the login page's CSRF token is signed with a timestamp, so it differs when the three responses straddle a second boundary. The test now strips the `csrf_token` value before comparing; everything else must still be byte-identical. I re-ran `checks.sh`: it passed, with 1930 tests.
