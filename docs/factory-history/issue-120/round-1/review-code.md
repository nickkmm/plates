VERDICT: PASS

## Summary
- Pages now authenticate from the access_token JWT cookie through app/shared/auth.py, with no database read. It imports no domain package, and decorators.py is deleted.
- Login, refresh and logout live in app/auth/login.py and are shared by the Jinja routes and /api/auth. The two continue endpoints follow the spec: next goes through is_safe_next, both cookies are cleared on failure, and logout revokes the refresh token.
- The access_token cookie's Max-Age is now 30 days. The _nav.html freshness script and the ingest_pdf.html XHR hook match requirement 14.
- The tests cover the spec: the route walk, the expired-token round trip, no database read, and the open-redirect cases. The docs and CLAUDE.md are updated. The gate passed and I did not re-run it.

## Findings
None.

## Notes
- signed_in_user() is an extra helper the spec doesn't name. It is justified by mypy and fails loudly if a guard is missing.
- _refuse() calls session.clear() when it redirects to /login. That also drops the CSRF token, which is harmless because the login page issues a new one.
- The freshness script only intercepts a submit when fewer than 30 seconds remain, so the existing confirm() handlers are untouched. It uses HTMLFormElement.prototype.submit, which skips validation, but validation has already run by the time the submit event fires.
