ESCALATE

## Summary
- Issue #120: every Jinja page now authenticates from the `access_token` JWT cookie through the new `app/shared/auth.py`, with no database read. `app/auth/decorators.py` is deleted.
- An expired but genuine token redirects through the new `GET /api/auth/refresh/continue`. Jinja `/login` and `/logout` share `app/auth/login.py` with `/api/auth`, and logout revokes the refresh token at `GET /api/auth/logout/continue`.
- The `access_token` cookie now lives 30 days, while the JWT inside it still expires after 5 minutes. A script in `_nav.html` refreshes the token before a POST form is sent.
- Round 1 gate: PASS (ruff, mypy, 1930 tests). All four reviews (code, security, UI, UX) are PASS with no findings.
- Escalated because it replaces login, sessions and route permissions for every page. This project requires a person to check that before merging.

## Why
The work matches the spec and stays inside intake's Scope. The gate and every review pass, and my spot-checks agree with build.md. However, the change touches authentication, sessions, permissions and the cookie lifetime, and a merge deploys it straight to production. The project rules say not to merge a change like this until a person has checked it.

## Acceptance criteria
1. No session user reads and no permanent session: met. I ran both `rg` commands in the worktree myself and they print nothing.
2. `app/auth/decorators.py` deleted, with no references left: met. I checked that the file is gone. build.md reports the reference `rg` as empty.
3. `app/shared/auth.py` defines the five functions and has no domain imports: met. I read the file and ran the forbidden-import `rg` (empty). It imports only `app.shared.*`, Flask and werkzeug.
4. An expired token on `/records` round-trips through refresh/continue and back: met. `test_token_flow.py::TestExpiredTokenRefreshes` passes in the gate, and build.md also checked it by hand with curl.
5. refresh/continue never redirects off-site: met. `TestRefreshContinueStaysOnSite` passes. I read `refresh_continue`: it checks `next` with `is_safe_next` and falls back to `/` or a bare `/login`.
6. The route walk (403 without the code, `/login` or 401 for anonymous, at least 50 route and method pairs): met. The test passes in the gate.
7. Jinja `POST /login` sets the same cookies as the API, and its failures are indistinguishable: met. `TestJinjaLogin` passes. The CSRF token is masked before the bodies are compared, and build.md explains why.
8. Logout revokes the refresh token and clears both cookies: met. `TestJinjaLogout` and `test_csrf.py::TestLogout` pass.
9. `/api/auth/me` still gives a JSON 401 for an expired token, and refresh/continue sends `no-store`: met. Both tests pass.
10. Guarded views make no database read: met. `test_never_reads_the_database` passes with `get_conn` patched to raise.
11. The freshness script and the `ingest_pdf.html` hook: met. Both are present per build.md and the code review. The manual checks used curl and headless Edge, not a real browser session.
12. Docs (`WEB.md`, `ARCHITECTURE.md`): met. Both are updated per build.md and the code review.
13. Existing permission, role, login, user and public-route tests still exist and pass: met. They pass in the gate, adapted as the spec allows.

## For the human
Recommendation: don't merge yet. Merge once you have checked the points below. The gate, every review and my spot-checks found nothing wrong. Your check is needed because the change rewrites login, sessions and route permissions for the whole app, and merging deploys it to production.

What to check:
- **Accepted security trade-off.** Deactivating a user, changing their role or editing a role's permissions now takes effect within 5 minutes (the access-token lifetime), instead of on the next request. This was decided in docs/MIGRATION_PLAN.md. Role-permission edits still don't revoke refresh tokens, so they apply at the next refresh. Confirm this is acceptable for production.
- **Cookie lifetime.** `access_token` now has `Max-Age` 30 days, though the JWT still expires after 5 minutes. This is how a page can tell that a refresh may work (spec Assumption 1).
- **GET endpoints that change state.** `/api/auth/refresh/continue` and `/api/auth/logout/continue` are GETs. `SameSite=Strict` keeps the refresh cookie off cross-site requests, but a cross-site link to logout/continue can still sign a user out. The security review rated this low impact.
- **Signed-in users see `/login` from outside links.** Opening the app from an external link (for example WhatsApp) shows `/login` even to a signed-in user, because of `SameSite=Strict` (spec Assumption 7). This is a product call you may want to revisit.
- **Manual check in a real browser.** The bot's manual checks used curl and headless Edge only. Before merging:
  - Sign in, leave `/ingest` open for more than 5 minutes, then submit the form and the PDF upload.
  - Open `/records` after 5 minutes.
  - Sign out.
  - Also try any fetch-based POST on a page left open for more than 5 minutes. The freshness script only covers `form[method=post]` and the PDF upload (UX review note).
- **Deploy effect.** Everyone signed in through the old Flask session will have to sign in again after the deploy.

Files to look at:
- `app/shared/auth.py`
- `app/auth/login.py`
- `app/auth/api.py` (`refresh_continue`, `logout_continue`)
- `app/auth/routes.py`
- `app/web/app.py`
- `app/web/templates/_nav.html`
- `app/web/templates/ingest_pdf.html`
- `app/auth/admin_routes.py`
- `docs/WEB.md`
- `tests/web/test_token_flow.py`
- `tests/web/test_auth.py`
