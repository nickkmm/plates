VERDICT: PASS

## Summary
- Flask-WTF `CSRFProtect` is now on app-wide. Every POST without a valid session-bound token gets a 400 before the route runs.
- All 23 posting forms carry the token (the template count check finds no mismatch), and the only XHR (`ingest_pdf.html`) also sends `X-CSRFToken`.
- `/declaracion` is exempt from the automatic check but calls `csrf.protect()` by hand, after raising its 40 MB cap and before any parsing, storage, Turnstile or rate-limit work.
- `/logout` is POST-only now, so a cross-site GET can no longer log a user out.
- No secrets or business data are committed. Only one new dependency was added, and it is the standard, maintained Flask extension.

## Findings
None.

## Notes
- `app/web/blueprints/public_declaration.py:309`: the manual check is skipped when `WTF_CSRF_ENABLED` is false. That matches how the automatic check behaves. Nothing in `app/` sets this flag, so the Flask-WTF default (`True`) applies in production. Only the test fixtures (`tests/web/conftest.py`, `tests/web/test_users.py`) turn it off, and they restore the earlier value afterwards. Keep it that way: never set it from an env var.
- `app/web/app.py`: the `CSRFError` handler renders `csrf_error.html` straight from the Jinja environment. It does not reflect any request data: `back_url` is either the fixed `/declaracion` or `url_for("index")`. It sends `Cache-Control: no-store` and never touches the DB, `can()` or the exception text, so no internals leak. Flask-WTF raises `CSRFError` as a 400 `HTTPException`, which the Sentry Flask integration does not report by default, so form data does not reach Sentry.
- Anonymous visitors to `/login` and `/declaracion` now get a session cookie that holds the CSRF token. The cookie is signed with `SECRET_KEY` and holds no personal data. `/declaracion` pages are already `no-store`.
- The `WTF_CSRF_SSL_STRICT` Referer check only runs when `request.is_secure`. Without `ProxyFix` behind the TLS proxy it will most likely not run, but the token check still applies. This is pre-existing and out of scope. It is noted only so nobody counts on the Referer check.
- Pre-existing, not changed by this branch: login has no rate limit. The spec puts login throttling out of scope.
- The Flask-WTF version `1.3.0` could not be checked from this shell (`pip show` printed nothing). The build reports that the gate installed it and ran 1724 tests against it.
