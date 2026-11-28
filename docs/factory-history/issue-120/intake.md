DECISION: ACCEPT
RISK: high
SURFACES: ui, security
RESCOPED: no

## Summary
- Accepted as written: the Jinja pages switch from the Flask session to the JWT access-token cookie that #119 added, so Flask pages and future Next pages share one login.
- Its dependency #119 is merged: `app/shared/tokens.py`, `app/auth/api.py` (login/refresh/logout/me and their cookies) and `refresh_tokens` are on `main`.
- It is one PR. Most of it is the same small change repeated across about 15 blueprints (67 decorator uses and the `session["user"]`/`session["user_id"]` reads). The hard part is the redirect-through-refresh flow.
- Rated high risk because it replaces authentication for every page and changes how fast deactivation and role changes take effect.

## Scope
Build what the issue specifies:
- A new `app/shared/auth.py` with `permission_required(*codes)`, `authenticated`, `current_user()` and `has_permission`. They read only the access-token claims (id, username, permissions) and never the database. Delete `app/auth/decorators.py`. Move the helpers other code still needs (`authenticate`, `is_safe_next`, `landing_url`, and `load_users`/`load_roles` for the backfill) somewhere that keeps `app/shared/` free of domain imports.
- Every blueprint, template helper and nav check uses the new module. `created_by`, `assigned_by`, `actor_id`, `updated_by` and the log lines take the user from the token claims. `rg -n "session\[\"user|session.get\(\"user" app` must return nothing.
- An HTML request without a valid access token is redirected to `GET /api/auth/refresh/continue?next=<safe path>` when a refresh may be possible, or to `/login?next=...` otherwise. Note: the refresh cookie lives on path `/api/auth`, so a Flask page can't see it. The spec must choose how "a refresh cookie may exist" is detected (for example, an expired but genuine access cookie is present). That endpoint rotates the tokens and redirects to `next` (checked with `is_safe_next`), or to `/login?next=...` when the refresh fails. `/api` requests get a JSON 401, never a redirect. A valid token without the permission still gets 403.
- `POST /login` (Jinja) uses the same login logic as `/api/auth/login` and sets the same cookies. Inactive users, unknown users and wrong passwords stay indistinguishable. `POST /logout` revokes the refresh token and clears both cookies. After this, the Flask session only holds the Flask-WTF CSRF token: no user data, and `PERMANENT_SESSION_LIFETIME` is no longer used for identity.
- Docs: `docs/WEB.md` (auth section, plus the new 5-minute window for deactivation and role changes) and `docs/ARCHITECTURE.md`.
- Tests: the login helpers in `tests/web/_helpers.py` move to the cookies. All existing permission tests keep passing. Add the tests from acceptance criteria 2 to 4: an expired access cookie plus a valid refresh cookie lands back on `/records` with new cookies; `next=https://evil.example` never redirects off-site; every protected route answers 403 without its permission and sends an anonymous user to `/login`.

Out of scope (as the issue says): the Next.js login page, narrowing the access cookie's path, and removing Flask-WTF.

## Why
`app/auth/decorators.py` loads the user and their permissions from the database on every request, using `session["user_id"]`. The session is also read directly in `admin_routes.py`, `declarations/routes.py`, `orders/routes/ingest.py`, `records.py`, `pricing/routes.py` and `production/routes.py`. The token pieces this needs already exist in `app/shared/tokens.py` and `app/auth/api.py`, so the work is a well-defined swap that `docs/MIGRATION_PLAN.md` (Phase 2, Decisions) settles: one PR, but a security-sensitive one.

## Questions
None.
