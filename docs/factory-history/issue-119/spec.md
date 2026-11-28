# JSON auth API with httpOnly cookies and shared /api plumbing

> Issue #119: Add the auth API: login, refresh, logout and me with httpOnly cookies

## Summary
- Add `/api/auth` (login, refresh, logout, me) in a new blueprint `app/auth/api.py`, with Pydantic models in `app/auth/schemas.py`. It uses the JWT and refresh-token modules from #118, which no route calls yet.
- Add shared `/api` plumbing in `app/shared/api.py`: JSON error bodies for every `/api/` error, the `X-Requested-With` check, and a Flask-WTF exemption for the API blueprint only.
- The refresh cookie is scoped to `/api/auth` instead of `/api/auth/refresh`, because logout must be able to read it and revoke it. The migration plan is updated to match.
- Saving a role change or a deactivation in `/usuarios`, or resetting a password there, revokes the user's refresh tokens in the same transaction.
- Out of scope: switching the Jinja pages to tokens, rate limiting, and OpenAPI or frontend generation.

## Context
- **Tokens (from #118, unused so far):**
  - `app/shared/tokens.py`: `issue_access_token(user_id, username, perms, now)` and `decode_access_token(token, now)` raise `ExpiredTokenError` or `InvalidTokenError`. It also defines `ACCESS_TOKEN_LIFETIME` (5 min) and `REFRESH_TOKEN_LIFETIME` (30 days). The claims are `sub`, `username`, `perms`, `iat` and `exp`, with no roles.
  - `app/auth/refresh_tokens.py`: `issue(conn, user_id)`, `rotate(conn, raw) -> (user_id, new_raw) | None` (reuse revokes the family), `revoke(conn, raw)` and `revoke_user(conn, user_id)`. None of them commits.
  - `app/shared/timezones.py::utc_now()` is the `now` to pass.
- **Login today:**
  - `app/auth/routes.py::login_post` calls `app.auth.decorators.authenticate`, which returns `None` for an unknown user, a wrong password or an inactive user.
  - It logs `"login: user=%s roles=%s"` and answers "Usuario o contraseña incorrectos." with a 401.
  - Effective permissions come from `app/auth/permissions.py::effective_permissions(user.roles, repository.get_user_permissions(conn, user.id))`, where admin expands to every code.
  - `repository.get_user_by_id` and `repository.get_user_by_username` are in `app/auth/repository/users.py`.
- **App wiring (`app/web/app.py`):**
  - Blueprints are registered here, along with `csrf.init_app(app)` (`app/web/csrf.py`).
  - `_COOKIE_SECURE` is read from `COOKIE_SECURE`.
  - `@app.errorhandler(CSRFError)` renders `csrf_error.html`.
  - `@app.errorhandler(404)` renders `not_found.html` for every 404 today.
- **Revocation points (`app/auth/admin_routes.py`):**
  - `user_edit` (POST `/usuarios/<id>`) calls `repository.set_user_roles` and `repository.update_user`, then `conn.commit()`.
  - `user_password` (POST `/usuarios/<id>/password`) calls `repository.set_user_password`, then commits.
  - `app/auth/service.py` holds only validation and has no write functions. The writes and their transaction are in `admin_routes.py`, so the revocation goes there, as intake says.
- **Tests:**
  - `tests/conftest.py` provides `clean_db`, `db_conn`, `test_credentials` (admin), `produccion_credentials` and `punto_venta_credentials`.
  - `tests/web/conftest.py` has the `client` fixture. It is not visible to a new `tests/api/` package, which needs its own `conftest.py`.
  - `tests/web/test_users.py::_fresh_client` shows the two-client pattern.
  - `tests/web/test_not_found.py` pins the HTML 404.
  - `tests/web/test_public_routes.py` pins the public allowlist.
  - `tests/core/db/test_refresh_tokens.py` tests the token module.
- **Docs:**
  - `docs/WEB.md` has the "Routes", "CSRF protection" and "Public routes" sections.
  - `docs/MIGRATION_PLAN.md` has the "Decisions" table (refresh cookie path) and "Phase 2".
  - `CLAUDE.md` has the CSRF bullet.
- **Dependencies:** `pydantic` and `PyJWT` are already in `requirements.txt`. Nothing new is needed.

## Requirements

### Shared /api plumbing (`app/shared/api.py`, new; it must not import any domain package)
1. Provide `is_api_request()`, which is true when `request.path` is `/api` or starts with `/api/`.
2. Provide `error_response(status, code, message)`, which returns a JSON response `{"error": {"code": ..., "message": ...}}` with that status and `Cache-Control: no-store`. Define the error body as Pydantic models (`ErrorDetail` and `ErrorResponse`) in this module.
3. Provide `init_api(app)`, called from `app/web/app.py`. It registers error handlers for 400, 401, 403, 404, 405, 409, 429 and 500:
   - Under `/api/`, each handler answers JSON in the error shape with a fixed English `code` and a Spanish `message` (tú form), and never shows HTML or the `abort()` description.
   - Use these codes: 400 `bad_request`, 401 `not_authenticated`, 403 `forbidden`, 404 `not_found`, 405 `method_not_allowed`, 409 `conflict`, 429 `rate_limited`, 500 `internal_error`.
   - Outside `/api/`, behaviour stays exactly as today. The 404 still renders `not_found.html`: the existing `_not_found` in `app/web/app.py` delegates to the JSON response when `is_api_request()` is true. The other codes return the original exception, which is Werkzeug's default.
   - Handle 500 by registering on `500`/`InternalServerError`, not on bare `Exception`, so Sentry and Flask's exception logging still see unhandled errors.
4. `init_api(app)` also registers a `before_request` hook. For POST, PUT, PATCH and DELETE under `/api/` without the header `X-Requested-With: XMLHttpRequest`, it answers `403` `{"error": {"code": "missing_requested_with", "message": ...}}` before the view runs. This includes login, refresh and logout. GET is not checked.
5. Flask-WTF CSRF is exempt only for the API blueprint (`csrf.exempt(api_bp)` in `app/web/app.py`). The Jinja routes keep `CSRFProtect` unchanged, and `tests/web/test_csrf.py` still passes.
6. The cookie `Secure` flag follows the same `COOKIE_SECURE` rule as the session cookie. Read the flag in one place (for example, store `_COOKIE_SECURE` in `app.config["COOKIE_SECURE"]`) and don't parse the env var twice.

### Auth API (`app/auth/api.py` blueprint `auth_api`, `url_prefix="/api/auth"`; models in `app/auth/schemas.py`)
7. Write these Pydantic models in `app/auth/schemas.py`: `LoginRequest` (`username: str`, `password: str`), `UserOut` (`id`, `username`, `roles: list[str]`), `LoginResponse` (`user: UserOut`, `permissions: list[str]`), `MeUser` (`id`, `username`) and `MeResponse` (`user: MeUser`, `permissions: list[str]`). Don't register any of them with OpenAPI tooling. Routes stay thin: they parse with the model and return `model.model_dump()` as JSON.
8. **POST /api/auth/login:**
   - **Bad body:** a body that is not a JSON object, or a missing or non-string field, gives `400` in the error shape with code `invalid_body`.
   - **Credentials:** strip `username` as `/login` does, then call `app.auth.decorators.authenticate`. An unknown user, a wrong password and an inactive user all get the same `401` with this body, byte for byte: `{"error": {"code": "invalid_credentials", "message": "Usuario o contraseña incorrectos."}}`. These failures set no cookies.
   - **Success:** issue a refresh token with `refresh_tokens.issue` and commit. Answer `200` with `LoginResponse`, where the permissions are the effective permissions, sorted.
   - **Cookies on success:**
     - `access_token`: `issue_access_token` with the same permissions; HttpOnly, `SameSite=Strict`, `Max-Age=300`, `Path=/`, and `Secure` when `COOKIE_SECURE` is on.
     - `refresh_token`: the raw value; HttpOnly, `SameSite=Strict`, `Max-Age=2592000`, `Path=/api/auth`, and `Secure` when `COOKIE_SECURE` is on.
   - Derive both max-ages from `ACCESS_TOKEN_LIFETIME` and `REFRESH_TOKEN_LIFETIME`.
   - **Log:** `current_app.logger.info("login: user=%s roles=%s", ...)`, the same as `/login`.
   - It does not touch the Flask session.
9. **POST /api/auth/refresh:**
   - Read the `refresh_token` cookie and call `refresh_tokens.rotate`.
   - Then load the user and their **current** roles and permissions from the database.
   - On success, commit and answer `200` with `LoginResponse` (current data). Set a new `refresh_token` cookie and a new `access_token` cookie with the attributes of requirement 8.
   - These cases answer `401` `not_authenticated` and clear both cookies:
     - a missing cookie;
     - an unknown, expired or reused token (`rotate` returns None; commit so that the family revocation on reuse persists);
     - a user who no longer exists or is inactive. Call `revoke_user` for that user and commit.
   - To clear a cookie, set it to expire immediately with the same name, path and attributes.
10. **POST /api/auth/logout:** if a `refresh_token` cookie is present, call `refresh_tokens.revoke` and commit. An unknown token is a no-op. Always clear both cookies and answer `204` with an empty body. No access token is needed.
11. **GET /api/auth/me:**
    - Read only the `access_token` cookie and call `decode_access_token(token, utc_now())`. There is no database read.
    - It answers `200` with `MeResponse`: `{"user": {"id", "username"}, "permissions": [...]}`.
    - A missing, invalid or expired token answers `401` `not_authenticated` in the error shape.
    - Put the cookie-reading helper in `app/auth/api.py` or `app/shared/api.py`, not a general `@permission_required`, which is the next task.
12. Register the blueprint in `app/web/app.py` next to the others, call `init_api(app)` there, and add the new routes to the route list in its module docstring.
13. Add `/api/auth/login`, `/api/auth/refresh` and `/api/auth/logout` to `PUBLIC_RULES` in `tests/web/test_public_routes.py`, because they are public by design, like `/login` and `/logout`. `/api/auth/me` stays out and refuses anonymous requests with 401.

### Revocation (`app/auth/admin_routes.py`)
14. In `user_edit` POST, when validation passes, call `refresh_tokens.revoke_user(conn, user_id)` inside the same `with db.get_conn()` block, before `conn.commit()`, when either of these is true:
    - the role set changed (`set(roles) != set(user.roles)`);
    - `active` goes from true to false.
15. In `user_password`, call `refresh_tokens.revoke_user(conn, user_id)` in the same transaction as `set_user_password`, before the commit.

### Tests (`tests/api/`, new package with `__init__.py` and `conftest.py`)
16. Add a `client` fixture in `tests/api/conftest.py` (Flask test client, `TESTING=True`, CSRF left **on**, to prove the exemption) and a helper that sends `X-Requested-With: XMLHttpRequest`. Database tests use `clean_db`/`db_conn` and the credential fixtures from `tests/conftest.py`.
17. Cover issue acceptance criteria 1 to 6 (see Acceptance criteria), plus:
    - a 400 `invalid_body` on login;
    - a 401 on `/me` without a cookie and with an expired token (build it with `issue_access_token` and a past `now`);
    - a 401 on refresh with no cookie and with an expired token (insert it with `repository.insert_refresh_token` and a past `expires_at`);
    - `Secure` on both cookies when the `COOKIE_SECURE` config is on and absent when it is off;
    - a 405 JSON for `GET /api/auth/login`;
    - `/me` permissions reflecting a role change only after refresh.

### Docs
18. `docs/WEB.md`:
    - Add a new section "JSON API (`/api`)": the four endpoints with their bodies and status codes, both cookies with their attributes, the error shape and its codes, the `X-Requested-With` rule, the CSRF exemption for the API blueprint, and the revocation triggers.
    - Update "CSRF protection" to say `/api` is exempt and uses the header instead.
    - Add the three public API routes to "Public routes".
    - Update the 404 bullet: JSON under `/api/`.
19. `docs/MIGRATION_PLAN.md` "Decisions":
    - Change the refresh cookie path to `/api/auth`, with the reason (logout must receive it to revoke it).
    - Note that the access cookie is on `/` during the migration and narrows to `/api` in phase 6.
20. `CLAUDE.md`: extend the CSRF bullet to say that `/api` requests are exempt from the token and must send `X-Requested-With: XMLHttpRequest`. Point to `docs/WEB.md`.

## Acceptance criteria
1. **Login sets both cookies and `/me` reads them.** A test in `tests/api/` logs in as the admin test user (POST `/api/auth/login` with the header). It asserts:
   - status 200 and a JSON body with `user.id`, `user.username`, `user.roles` and a sorted `permissions` list equal to every catalog code;
   - `Set-Cookie` for `access_token` with `HttpOnly`, `SameSite=Strict`, `Max-Age=300` and `Path=/`;
   - `Set-Cookie` for `refresh_token` with `HttpOnly`, `SameSite=Strict`, `Max-Age=2592000` and `Path=/api/auth`;
   - that `GET /api/auth/me` then answers 200 with the same id, username and permissions.

   Run `pytest tests/api -k login`.
2. **The three login failures are identical.** A test posts a wrong password, an unknown username and the credentials of a deactivated user. All three answer 401 with byte-identical `resp.data`, equal to `{"error": {"code": "invalid_credentials", "message": "Usuario o contraseña incorrectos."}}` once parsed, and none sets a cookie.
3. **Refresh rotates and detects reuse.** A test logs in, then POSTs `/api/auth/refresh`. It answers 200 with a new `refresh_token` value, different from the old one, and a new `access_token`. The test then sends the **old** refresh token again: that answers 401 `not_authenticated` and clears both cookies (`Max-Age=0` or an expired date). After that, refreshing with the **new** token also answers 401.
4. **Logout needs the header.** A test POSTs `/api/auth/logout` without `X-Requested-With` and gets 403 with JSON `error.code == "missing_requested_with"`. Content-Type is `application/json`. With the header, logout answers 204, clears both cookies, and a refresh with the logged-out token then answers 401.
5. **`/usuarios` changes revoke refresh tokens.** One test per trigger: a user logs in through the API, then an admin (a separate `_fresh_client`-style client, logged in via `/login`) does one of the following through `/usuarios`. After each, that user's next `POST /api/auth/refresh` answers 401.
   - deactivate them (POST `/usuarios/<id>` without `active`);
   - change their roles (POST `/usuarios/<id>`);
   - reset their password (POST `/usuarios/<id>/password`).
6. **JSON 404 under `/api`, HTML 404 elsewhere.** `GET /api/nope` answers 404 with JSON `error.code == "not_found"` and no HTML. `GET /no-existe` still answers `not_found.html`: the existing `tests/web/test_not_found.py` passes unchanged.
7. **Other API errors are JSON.** `GET /api/auth/login` answers 405 JSON `method_not_allowed`. `GET /api/auth/me` without a cookie answers 401 JSON `not_authenticated`. Login with a non-JSON body answers 400 JSON `invalid_body`.
8. **CSRF is exempt only for the API.** The `tests/api` client runs with CSRF enabled, and login with the header still succeeds. `tests/web/test_csrf.py` passes unchanged: a Jinja POST without a token still gets 400 `csrf_error.html`.
9. **`Secure` follows `COOKIE_SECURE`.** A test turns the flag on and checks that both login cookies carry `Secure`. With the flag off, neither does.
10. **Public routes.** `tests/web/test_public_routes.py` lists the three new public API rules and passes. `/api/auth/me` answers 401 to an anonymous request.
11. **Docs.** `docs/WEB.md` has a section on the JSON API covering the endpoints, cookies, error shape, `X-Requested-With` and revocation. `docs/MIGRATION_PLAN.md` "Decisions" gives the refresh cookie path as `/api/auth`. The CSRF bullet in `CLAUDE.md` mentions the `/api` header rule.
12. **PR description.** It lists the improvements over the issue and their reasons, or says there were none. At least the refresh cookie path change and the `me` response without roles must be listed.

## Out of scope
- Switching the Jinja pages or `app/auth/decorators.py` to the access token, retiring the Flask session, or adding a token-based `@permission_required` in `app/shared/auth.py` (next task).
- Rate limiting the login, or any 429 producer: the handler exists, but nothing raises 429 yet.
- `flask-openapi3`, `APIBlueprint`, `openapi.json`, `app.openapi_export`, `frontend/` and `npm run gen:api` (phase 3).
- Adding `roles` to the JWT claims, or any other change to `app/shared/tokens.py` or `app/auth/refresh_tokens.py` behaviour.
- New migrations or schema changes. `refresh_tokens` already exists (0019).
- Revoking tokens on role permission edits (`/usuarios/roles/<name>`). That is not one of the triggers in the issue, and the access token expires within 5 minutes anyway.
- Changing the Jinja `/login` and `/logout` behaviour, or the HTML error pages.
- Any change to `factory/checks.sh` or `factory/config.json`.

## Assumptions
- **The refresh cookie path is `/api/auth`, not `/api/auth/refresh`.** Logout must receive the cookie to revoke it, and a single cookie is simpler than two. The extra endpoints that see it (login and me) are same-origin and never log it.
- **`/me` returns `{user: {id, username}, permissions}` without roles.** The access token carries no roles, `me` must not read the database, and the front end builds its menu from permissions.
- **Revocation happens in `admin_routes.py`, in the transaction of the write.** `app/auth/service.py` has no write functions today, and moving the writes there would be a refactor beyond scope.
- **`user_edit` revokes only when the roles actually change or the user is deactivated.** Saving the form unchanged, or reactivating a user, should not log the user out.
- **The API error messages are new Spanish copy.** For example, 401 "Tu sesión expiró. Inicia sesión de nuevo." and `missing_requested_with` "No se pudo procesar la solicitud. Recarga la página e intenta de nuevo." The builder picks the rest following the tú rule. The issue fixes only the login message.
- **Login, refresh and logout are added to the public-route allowlist.** They are public by design, like `/login` and `/logout`. Listing them keeps that exposure an explicit, reviewed decision.
- **A refresh for an inactive or missing user calls `revoke_user` and commits.** `rotate` has already issued a successor that must not stay valid.
