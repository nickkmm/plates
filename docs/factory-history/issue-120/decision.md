ESCALATE

## Summary
- Issue #120: every Jinja page now authenticates from the `access_token` JWT cookie through the new `app/shared/auth.py`, with no database read. `app/auth/decorators.py` is deleted, and login, refresh and logout live in the shared `app/auth/login.py`.
- An expired but genuine token redirects through the new `GET /api/auth/refresh/continue`. Logout revokes the refresh token at `GET /api/auth/logout/continue`. The `access_token` cookie now lives 30 days, while the JWT inside it still expires after 5 minutes.
- Round 2 applied your PR feedback: both token cookies are now `SameSite=Lax` instead of `Strict`, so a link opened from outside the app (for example WhatsApp) keeps the user signed in. Tests and docs were updated to match.
- Round 2 gate: PASS on f510072, which is the branch HEAD (ruff, mypy, 1930 tests). All four reviews (code, security, UI, UX) are PASS with no findings.
- Escalated because the change replaces login, sessions and route permissions for every page. The project rules require a person to check that before merging.

## Why
The work meets the spec and stays inside intake's Scope. The SameSite change goes against the spec's Out of scope list, but you asked for it in feedback 1, so that is fine. The gate and every review pass, and my spot-checks agree with build.md. However, the change rewrites authentication, sessions, permissions and the CSRF cookie posture, and a merge deploys it straight to production, so the project rules say a person must check it first.

## Acceptance criteria
1. No session user reads and no permanent session: met. I ran both `rg` commands in the worktree myself and neither found anything.
2. `app/auth/decorators.py` deleted, with no references left: met. I checked that the file is gone. build.md reports the reference `rg` as empty.
3. `app/shared/auth.py` defines the five functions and imports no domain package: met. This is in build.md and the code review.
4. An expired token on `/records` goes through refresh/continue and comes back: met. `TestExpiredTokenRefreshes` passes in the gate, and build.md also checked it by hand with curl.
5. refresh/continue never redirects off-site: met. `TestRefreshContinueStaysOnSite` covers 4 hostile `next` values, with and without a refresh cookie. The security review confirmed it.
6. Route walk (no token gives `/login` or a 401, a missing code gives a 403, at least 50 route and method pairs): met. The test passes in the gate.
7. Jinja `POST /login` sets the same cookies as the API, and its three failures look identical: met. `TestJinjaLogin` passes and now asserts `SameSite=Lax`. The CSRF token is masked before the bodies are compared, and build.md explains why.
8. Logout revokes the refresh token and clears both cookies: met. `TestJinjaLogout` and `test_csrf.py::TestLogout` pass.
9. `/api/auth/me` still gives a JSON 401 for an expired token, and refresh/continue sends `no-store`: met. Both tests pass.
10. Guarded views make no database read: met. `test_never_reads_the_database` passes with `get_conn` patched to raise.
11. The freshness script and the `ingest_pdf.html` hook: met in code. The manual checks used curl and headless Edge with a stubbed `fetch`, not a real browser session.
12. Docs: met. `docs/WEB.md` and `docs/ARCHITECTURE.md` are updated. I checked that `WEB.md` and `MIGRATION_PLAN.md` now say `SameSite=Lax`, and that no stale "Strict" is left in `app`, `tests` or `docs`.
13. Existing permission, role, login, user and public-route tests still exist and pass: met. They pass in the gate, adapted as the spec allows.

## For the human
Recommendation: don't merge yet. Merge once you have checked the points below. The gate, every review and my spot-checks found nothing wrong. Your check is needed because the change rewrites login, sessions and route permissions for the whole app, and merging deploys it to production.

What to check:
- **SameSite=Lax trade-off (your round-2 request).** `Lax` sends the token cookies on a cross-site top-level GET. A link on another site to `/api/auth/logout/continue` can therefore sign a user out. A cross-site GET to `/api/auth/refresh/continue` rotates the user's tokens but only redirects on-site. Cross-site POSTs still carry no cookie, and Flask-WTF and `X-Requested-With` are unchanged. The security review rated this as low impact. It also noted that every GET route must stay read-only from now on. Confirm you accept this.
- **5-minute window.** Deactivating a user, changing their role or editing a role's permissions now takes effect within 5 minutes (the access-token lifetime), instead of on the next request. Role-permission edits still don't revoke refresh tokens. Confirm this is acceptable in production.
- **Cookie lifetime.** `access_token` now has `Max-Age` 30 days. The JWT inside it is still only valid for 5 minutes.
- **Manual check in a real browser.** Do these before merging:
  - Sign in, leave `/ingest` open for more than 5 minutes, then submit the form and the PDF upload.
  - Open `/records` after 5 minutes.
  - Open an app link from outside the app (for example WhatsApp) while signed in.
  - Sign out.
  - On a page left open for more than 5 minutes, try any fetch-based POST. The freshness script only covers `form[method=post]` and the PDF upload (UX review note).
- **Deploy effect.** Everyone signed in through the old Flask session will have to sign in again after the deploy.

Files to look at:
- `app/shared/auth.py`
- `app/auth/login.py` (the cookie helpers, `samesite="Lax"`)
- `app/auth/api.py` (`refresh_continue`, `logout_continue`)
- `app/auth/routes.py`
- `app/web/app.py`
- `app/web/templates/_nav.html`
- `app/web/templates/ingest_pdf.html`
- `docs/WEB.md`
- `docs/MIGRATION_PLAN.md`
- `tests/web/test_token_flow.py`
