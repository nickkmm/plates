STATUS: READY

# Build: JSON auth API with httpOnly cookies and shared /api plumbing

## Summary
- Adds `/api/auth` login, refresh, logout and me (`app/auth/api.py`, models in `app/auth/schemas.py`). They set the httpOnly `access_token` (Path=/) and `refresh_token` (Path=/api/auth) cookies.
- Adds shared `/api` plumbing (`app/shared/api.py`): JSON errors under `/api/`, the `X-Requested-With` check, and a CSRF exemption for the API blueprint only.
- In `/usuarios`, a role change, a deactivation or a password reset revokes the user's refresh tokens in the same transaction.
- New `tests/api/` suite (28 tests, CSRF on), plus docs in WEB.md, MIGRATION_PLAN.md and CLAUDE.md. Draft PR #198.

## Changes
- `app/shared/api.py` (new): `is_api_request`, `error_response`, `http_error_response`, the `ErrorDetail`/`ErrorResponse` models, and `init_api(app)`.
  - `init_api` registers handlers for 400/401/403/404/405/409/429 by code and for 500 on `InternalServerError`. Under `/api/` they answer JSON; elsewhere they return the exception unchanged.
  - It also adds a before_request hook: a POST/PUT/PATCH/DELETE under `/api/` without the header gets a 403 `missing_requested_with`.
  - It imports no domain package.
- `app/auth/schemas.py` (new): `LoginRequest` (strict str fields), `UserOut`, `LoginResponse`, `MeUser`, `MeResponse`.
- `app/auth/api.py` (new): blueprint `auth_api` with url_prefix `/api/auth`. The four endpoints follow the spec, and cookie max-ages come from `ACCESS_TOKEN_LIFETIME`/`REFRESH_TOKEN_LIFETIME`.
  - `after_request` sets `Cache-Control: no-store` on every response.
  - Clearing a cookie uses `delete_cookie` with the same path and attributes.
- `app/web/app.py`:
  - registers the blueprint, adds `csrf.exempt(auth_api_bp)` and `init_api(app)`;
  - adds `COOKIE_SECURE` to `app.config`;
  - `_not_found` delegates to the JSON 404 when `is_api_request()`;
  - docstring route list updated.
- `app/auth/admin_routes.py`:
  - `user_edit` calls `refresh_tokens.revoke_user` before the commit when `set(roles) != set(user.roles)` or active goes from true to false;
  - `user_password` revokes in the same transaction as `set_user_password`.
- `app/auth/refresh_tokens.py`: docstring only ("No route uses this module yet" replaced).
- `tests/api/__init__.py`, `conftest.py` (`client` with CSRF on and COOKIE_SECURE off, restored on teardown), `_helpers.py` (`api_post`/`api_login` with `X-Requested-With`), `test_auth_api.py`.
- `tests/web/test_public_routes.py`: `PUBLIC_RULES` now includes the three public API routes.
- `docs/WEB.md`:
  - new "JSON API (`/api`)" section: endpoints, cookies, error codes, the header rule, the CSRF exemption and revocation;
  - Routes rows for the four endpoints;
  - updated CSRF, Public routes and 404 bullets.
- `docs/MIGRATION_PLAN.md`: the Decisions row now gives the refresh cookie path `/api/auth` with the reason, and the access cookie on `/` until phase 6; the phase 6 cleanup now narrows the access cookie to `/api`.
- `CLAUDE.md`: the CSRF bullet now covers the `/api` header rule.

## Acceptance criteria
1. [x] **Login sets both cookies and `/me` reads them.** `TestLogin::test_login_sets_both_cookies_and_me_reads_them` checks the body (id, username, roles, permissions == sorted catalog), the attributes of both Set-Cookie headers, the log line, and `/me` returning the same id, username and permissions. `pytest tests/api -k login` passes.
2. [x] **The three login failures are identical.** `test_the_three_login_failures_are_identical`: wrong password, unknown user and inactive user all get 401 with one unique `resp.data` that parses to the exact body, and no Set-Cookie.
3. [x] **Refresh rotates and detects reuse.** `TestRefresh::test_rotates_and_detects_reuse`: refresh returns 200 with a new refresh value different from the old one and a new access token. Resending the old token gives 401 `not_authenticated` with both cookies cleared (Max-Age=0, empty value), and the new token then also gives 401.
4. [x] **Logout needs the header.** `TestLogout::test_needs_the_header_then_revokes`: without the header, 403 `missing_requested_with` as application/json. With it, 204 with an empty body and both cookies cleared, and the logged-out token then refreshes to 401.
5. [x] **`/usuarios` changes revoke refresh tokens.** `TestRevocationFromUsuarios` covers deactivation, role change and password reset, each done by a separate admin client logged in via `/login` with CSRF tokens. Each one makes the next refresh answer 401. An extra test shows that saving the form unchanged does not revoke.
6. [x] **JSON 404 under `/api`, HTML elsewhere.** `TestApiErrors::test_unknown_api_url_is_json_404` passes, and `tests/web/test_not_found.py` passes unchanged.
7. [x] **Other API errors are JSON.** `test_get_login_is_405_json` (405 `method_not_allowed` with Allow), `TestMe::test_without_a_cookie_is_401` and `test_bad_body_is_400_invalid_body` (form body, JSON list, missing field, non-string field) all pass.
8. [x] **CSRF is exempt only for the API.** The `tests/api` client runs with `WTF_CSRF_ENABLED=True`, and `test_csrf_stays_on_but_the_api_is_exempt` shows API login succeeding while a Jinja `/login` POST without a token gets 400 HTML. `tests/web/test_csrf.py` passes unchanged.
9. [x] **`Secure` follows `COOKIE_SECURE`.** `TestSecureFlag`: both cookies carry Secure with the flag on and neither does with it off.
10. [x] **Public routes.** `tests/web/test_public_routes.py` passes with the three new rules, and `/api/auth/me` answers 401 to an anonymous request (that test and `TestMe`).
11. [x] **Docs.** WEB.md has the "JSON API (`/api`)" section, MIGRATION_PLAN.md Decisions gives the refresh path as `/api/auth`, and the CLAUDE.md CSRF bullet mentions the header.
12. [x] **PR description.** PR #198 has an "Improvements over the issue" section that lists the refresh cookie path and the `me` response without roles, with their reasons.

## Validation
- `.venv/Scripts/python -m ruff check app/`: passed. `mypy app/`: passed.
- `ruff check tests/api tests/web/test_public_routes.py`: passed after fixing one blank-line nit.
- `pytest tests/api` with `TEST_POSTGRES_DB=plates_test_24` and `TEST_REQUIRE_DB=1`: 28 passed.
- `pytest tests/api tests/web/test_not_found.py tests/web/test_csrf.py tests/web/test_public_routes.py tests/web/test_users.py`: passed.
- `bash factory/checks.sh` from the worktree with the gate's DB env: exit 0, 1911 passed.

## Log
### Round 1
- Created the worktree `.venv` and installed requirements-dev.
- Implemented the spec: shared `/api` plumbing, the auth API blueprint and its schemas, the app wiring, revocation in `admin_routes.py`, `tests/api/` and the docs.
- First test run: the revocation tests failed because `/login` clears the session, so the CSRF token read from the login page went stale. Fixed in the test helper, which now reads the token from `/usuarios/<id>` after login.
- Two small additions not in the spec, both listed in the PR:
  - `Cache-Control: no-store` on every `/api/auth` response;
  - the `Allow` header kept on the JSON 405.
- Committed in 5 commits, pushed, and opened draft PR #198. Per CLAUDE.md, the PR body has no "Generated with" line.
- No dev server or browser check: no criterion is a page. Every criterion is covered by tests.
