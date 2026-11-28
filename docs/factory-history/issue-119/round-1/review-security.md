VERDICT: PASS

## Summary
- Adds the JSON auth API (`/api/auth` login, refresh, logout, me) with httpOnly, SameSite=Strict token cookies, plus shared `/api` error and `X-Requested-With` plumbing.
- Login reuses `authenticate`; all three failures return the same 401 body and set no cookies. Refresh reloads current roles and the active flag, and revokes on an inactive or missing user.
- CSRF exemption is scoped to the `auth_api` blueprint only. Its replacement, the `X-Requested-With` check on every state-changing `/api/` request, is sound because no CORS is configured.
- Role changes, deactivation and password resets in `/usuarios` now revoke refresh tokens in the same transaction, which tightens access.
- No new dependencies, no SQL changes, no secrets or business data committed.

## Findings
None.

## Notes
- `app/auth/api.py` login: there is no rate limit on `POST /api/auth/login`. Jinja `/login` has none either, so the new endpoint doesn't make brute force any easier. The spec leaves this out of scope, but it should be done before the API is exposed more widely (the 429 handler already exists).
- `app/auth/decorators.py::authenticate` (unchanged): an unknown or inactive username skips `check_password_hash`, so response timing can reveal which usernames exist. This already happens on `/login`, and the API inherits it. A dummy hash check would close it.
- After a revocation, the access token stays valid for up to 5 minutes (`/me` never reads the database). This is a documented design choice, and today nothing but `/me` trusts the access token. The next task, a token-based `permission_required`, should keep this window in mind for deactivations.
- The access cookie has `Path=/`, so the browser sends it to Jinja routes and `/declaracion` too. It is httpOnly and SameSite=Strict and nothing logs it, so it's acceptable during the migration as the plan says. Don't log request cookies in Sentry (`send_default_pii`) while this holds.
- The login log line records the staff username and roles, the same as `/login`. No customer personal data is logged, and the refresh token is never logged.
- The 500 handler is registered on `InternalServerError`, not `Exception`, so Sentry and Flask's exception logging still see unhandled errors, and the JSON body never exposes the exception text.
