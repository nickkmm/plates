ESCALATE

## Summary
- Adds the JSON auth API under `/api/auth` (login, refresh, logout, me), which sets httpOnly, SameSite=Strict token cookies. It is built on the JWT and refresh-token modules from #118.
- Adds shared `/api` plumbing in `app/shared/api.py`: JSON errors, the `X-Requested-With` check, and a CSRF exemption for the API blueprint only.
- `/usuarios` now revokes a user's refresh tokens when their roles change, when they are deactivated, or when their password is reset.
- Gate round 1 passed (1911 tests), and the code and security reviews both say PASS with no findings. The change stays inside the intake scope.
- It still needs a person: it touches login, sessions, user roles and CSRF handling, which this project always sends to a human.

## Why
Every spec criterion is met, with tests behind each one, and the gate and both reviews passed. I checked the revocation code and the cookie attributes in the diff myself, and both match the spec. Even so, this PR adds a second login path, changes CSRF handling for `/api`, and changes what `/usuarios` does on a role, active or password change. The project rules say a person must check any change to login, sessions, roles or route permissions before it merges.

## Acceptance criteria
1. Login sets both cookies and `/me` reads them: met. `TestLogin` checks the body and both Set-Cookie headers. In `app/auth/api.py` I saw access on Path=/ with Max-Age from ACCESS_TOKEN_LIFETIME and refresh on Path=/api/auth, both HttpOnly and SameSite=Strict.
2. The three login failures are identical: met. `authenticate` returns None in all three cases and the code sends one 401 body for all of them, which the test checks byte for byte.
3. Refresh rotates and detects reuse: met. On reuse the refresh route commits after `rotate` returns None, so the family revocation is kept. Covered by `TestRefresh::test_rotates_and_detects_reuse`.
4. Logout needs the header: met. The before_request hook answers 403 `missing_requested_with` without it. With it, logout revokes the token and answers 204, and the tests cover both cases.
5. `/usuarios` changes revoke refresh tokens: met. I checked `app/auth/admin_routes.py` myself. `revoke_user` runs before `conn.commit()` when the role set changes or active goes from true to false, and on every password reset. There is one test per trigger, plus one showing that saving the form unchanged revokes nothing.
6. JSON 404 under `/api`, HTML elsewhere: met. `_not_found` returns the JSON error when `is_api_request()` is true, and `tests/web/test_not_found.py` passes unchanged.
7. Other API errors are JSON (405, 401, 400 invalid_body): met. The tests are listed in build.md, and the 500 handler is registered on InternalServerError, so Sentry still sees unhandled errors.
8. CSRF is exempt only for the API: met. The code calls `csrf.exempt(auth_api_bp)` only, the tests run with CSRF on, and `tests/web/test_csrf.py` passes unchanged.
9. `Secure` follows `COOKIE_SECURE`: met. The flag is read once into `app.config["COOKIE_SECURE"]`, and `TestSecureFlag` checks both settings.
10. Public routes: met. `PUBLIC_RULES` lists the three new routes, and `/me` answers 401 to an anonymous request.
11. Docs: met. `docs/WEB.md` has the new "JSON API (`/api`)" section, the Decisions table in `docs/MIGRATION_PLAN.md` gives the refresh path as `/api/auth`, and the CLAUDE.md CSRF bullet now covers `/api`.
12. PR description: met. PR #198 has an "Improvements over the issue" section that lists the refresh cookie path and `me` without roles, with their reasons.

## For the human
Recommendation: merge, after you review it. The project rules require that review because this change touches login, sessions, user roles and CSRF. I found no defects. Please check these points:

- **Second login path and CSRF.** `app/auth/api.py` and `app/shared/api.py`: confirm that you accept the `X-Requested-With` header as the API's only CSRF defence. That only holds while no CORS is configured, so check that nothing in production adds CORS headers. Also check that `csrf.exempt` covers only `auth_api_bp` in `app/web/app.py`.
- **Cookie decisions.** The access cookie is on `Path=/` during the migration, so it is also sent to Jinja pages and `/declaracion`. The refresh cookie path is now `/api/auth`, not `/api/auth/refresh` as the issue said. Confirm both in `docs/MIGRATION_PLAN.md` ("Decisions").
- **Revocation in `/usuarios`.** `app/auth/admin_routes.py` revokes only on an actual role change, a deactivation or a password reset. Editing a role's permissions does not revoke, and an access token stays valid for up to 5 minutes after any revocation.
- **Production cookies.** `COOKIE_SECURE=1` must already be set in production, because the new cookies get the Secure flag only when it is on. This PR adds no environment variable, migration, dependency, or Docker or CI change.
- **Follow-ups from the security review.** Neither blocks this PR:
  - there is no rate limit on `POST /api/auth/login` (the same as `/login`);
  - `authenticate` responds at different speeds for unknown and existing usernames, which can reveal which usernames exist;
  - don't enable Sentry `send_default_pii` while the access cookie is on `/`.
