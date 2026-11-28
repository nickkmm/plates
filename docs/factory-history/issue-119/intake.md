DECISION: ACCEPT
RISK: high
SURFACES: security
RESCOPED: yes

## Summary
- Accepted: the JSON auth API (login, refresh, logout, me) with httpOnly cookies, the shared `/api` plumbing, and refresh-token revocation on user changes.
- It builds on #118 (merged): `app/shared/tokens.py` and `app/auth/refresh_tokens.py` are already there, unused by any route so far.
- Rated high risk because it is authentication. The Jinja session login stays as it is.
- Small rescope: the OpenAPI contract and client regeneration is left out, since phase 3 has no `flask-openapi3` or `frontend/` yet. Both cookies follow `COOKIE_SECURE`.

## Scope
Build, under `/api/auth` (new blueprint, Pydantic request/response models written now, not registered with any OpenAPI tooling):

- **POST /api/auth/login** `{username, password}`: checks credentials the same way the current `/login` does (`authenticate` in `app/auth/decorators.py`). On success it answers `200` with `{user: {id, username, roles}, permissions: [...]}` (effective permissions, admin expanded as today), sets the `access_token` cookie (httpOnly, `SameSite=Strict`, max-age 5 min, path `/`) and the `refresh_token` cookie (httpOnly, Strict, max-age 30 days, path `/api/auth/refresh`), and logs `login: user=... roles=...`. Unknown user, wrong password and inactive user all get the same `401` body `{"error": {"code": "invalid_credentials", "message": "Usuario o contraseña incorrectos."}}`. A body that is not valid JSON or is missing a field gets `400` in the error shape.
- **POST /api/auth/refresh**: rotates the refresh cookie (`refresh_tokens.rotate`) and issues a new access cookie with the user's **current** roles and permissions, read from the database. A missing, unknown, expired or reused token, or a user who no longer exists or is inactive, gets `401` and both cookies cleared. Reuse revokes the whole family, as #118 already does.
- **POST /api/auth/logout**: revokes the presented refresh token if there is one, clears both cookies, answers `204`.
  - Note for the spec writer: the refresh cookie's path is `/api/auth/refresh`, so the browser won't send it to `/api/auth/logout`. The spec must make logout able to revoke the token, for example by also scoping the cookie to logout, and must document the choice.
- **GET /api/auth/me**: reads the access cookie only, with no database read. It returns the user and their permissions, or `401` in the error shape when the cookie is missing, invalid or expired. Roles are in the response only if the spec adds them to the token claims. Otherwise `me` returns `{id, username}` plus the permissions, and the spec says so.
- **Shared `/api` plumbing in `app/shared/`**:
  - Every error under `/api/` (400, 401, 403, 404, 405, 409, 429, 500) is answered as JSON in the error shape, never HTML. Unknown non-API URLs still render `not_found.html`.
  - State-changing `/api` requests (POST/PUT/PATCH/DELETE, login included) without `X-Requested-With: XMLHttpRequest` get `403` in the error shape.
  - Flask-WTF CSRF is exempt for `/api` blueprints only. Every Jinja form keeps its token.
  - Both cookies are `Secure` when `COOKIE_SECURE=1`, following the existing rule in `app/web/app.py`. This keeps local HTTP dev working, and production sets the flag.
- **Revocation**: deactivating a user, changing their roles, or setting their password through `/usuarios` revokes all their refresh tokens (`refresh_tokens.revoke_user`) in the same transaction as the change.
- **Tests** in `tests/api/` cover acceptance criteria 1 to 6 of the issue. `factory/checks.sh` passes. `docs/` gets updated with the endpoints, cookies, error shape and the `/api` CSRF rule.

Left out:
- Switching the Jinja pages to the access token, and the token-based `@permission_required` in `app/shared/auth.py` for other routes (next task). `me` only needs a small helper that reads the access cookie.
- Rate limiting the login.
- OpenAPI registration, `openapi.json` and the `npm run gen:api` client (phase 3). Neither `flask-openapi3` nor `frontend/` exists yet.

## Why
The issue is phase 2 of `docs/MIGRATION_PLAN.md`, and its dependency #118 is merged. `app/shared/tokens.py` (issue and decode the access JWT) and `app/auth/refresh_tokens.py` (issue, rotate with reuse detection, revoke, `revoke_user`) are in place, and the issue says "No route uses this module yet". That leaves one coherent PR: routes, shared error and CSRF plumbing, and revocation hooks in `app/auth/admin_routes.py`, where `set_user_roles`, `update_user` and `set_user_password` are called. It is high risk because it adds a second login path and changes CSRF handling. The plumbing must stay limited to `/api/` so the Jinja pages and their `CSRFProtect` behaviour don't change.
