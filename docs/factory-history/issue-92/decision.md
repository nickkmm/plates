ESCALATE

## Summary
- Turns on Flask-WTF `CSRFProtect` for the whole app. The 23 posting forms carry the token, the `/ingest/pdf` XHR also sends `X-CSRFToken`, and `/logout` now accepts only POST.
- `/declaracion` is exempt from the automatic check and calls `csrf.protect()` itself, after it raises its 40 MB body cap. A bad token on any form shows a Spanish 400 page, `csrf_error.html`.
- Round 1 passed: `GATE: PASS` with 1724 tests, and all four reviews (code, security, UI, UX) say PASS with no findings.
- I'm escalating because the change touches login, sessions and the public declaration form, and it adds a runtime dependency (`Flask-WTF==1.3.0`). Pushing to `main` deploys it to production.
- Criterion 10, the manual browser walk, is only partly done. Nobody could log in as staff because the local dev database isn't migrated.

## Why
The code fits the spec and stays inside intake's Scope. I checked it myself: the token counts match in every template, and the `/declaracion` and `/logout` code is correct. But under this project's rules, any change to login, sessions, the public declaration form or the dependencies needs a person to check it before merging. A missed or broken token would block real staff actions on the first deploy, and the logged-in staff forms have not been submitted in a real browser.

## Acceptance criteria
1. Pinned Flask-WTF: met. `requirements.txt` has `Flask-WTF==1.3.0` with a comment.
2. Login without a token gets 400 with the Spanish page and logs nobody in: met. `TestLogin` in `test_csrf.py` covers it, and the build and UI review saw the same over HTTP.
3. Login with a token returns 302 and logs in: met (`test_csrf.py`).
4. One route per blueprint returns 400 without a token and works as before with one: met. There are test classes for all 10 blueprints, and the gate passed.
5. A header-only token on `/ingest/pdf` is accepted: met. There is a test for it, and `setRequestHeader("X-CSRFToken", ...)` is in `ingest_pdf.html`.
6. Every posting form carries the token: met. I re-ran the per-template `method="post"` versus `csrf_token()` count myself and found no mismatch. The template-scan test also passes.
7. `/logout` is POST-only: met. The diff shows `@bp.post("/logout")`, `GET` returns 405 in the tests, and the build saw 405 by hand too.
8. `/declaracion` with CSRF on: met. In the diff, `csrf.protect()` runs right after `request.max_content_length = MAX_REQUEST_BYTES`. Tests cover a 13 MB body (no 413), a body over 40 MB (the friendly 413) and a missing token (400, nothing stored).
9. `SAMESITE="Lax"` is kept and `WTF_CSRF_TIME_LIMIT=None` is set: met (`app/web/app.py`).
10. Manual browser walk through every staff form: partly met. Only anonymous HTTP checks were done. Staff login failed on the unmigrated dev database, and `agent-browser` is not installed.
11. Docs: met. `docs/WEB.md` has the "Protección CSRF" section, `/logout` is listed as POST, and there is a `csrf_error.html` row. `AGENTS.md` has the CSRF line.

## For the human
Recommendation: don't merge yet

Reasons:
- It touches login (`/login`, `/logout`), sessions (anonymous visitors now get a signed session cookie that holds the CSRF token) and the public declaration form (`/declaracion`). This project requires a person to check all of these before merging.
- It adds a runtime dependency, `Flask-WTF==1.3.0`, which the production image installs on the next deploy. Confirm you accept it.
- Acceptance criterion 10 was not done as written. No staff form was submitted in a browser while logged in.

What to check before merging:
1. Run the branch locally against a migrated database and log in as staff. Submit each form once: register an order, upload a pedido PDF, save and delete a record, the review actions (update, resolve, delete), inventory, prices, customer, approve or reject a declaration and the notarized toggle, create or edit a user and change a password, then log out. None of them should return 400.
2. In dev tools, delete the `csrf_token` input before submitting one form. You should get the Spanish page "La sesión expiró..." and its "Volver" link should work. Look at it once at phone width.
3. Submit a public `/declaracion` with photos while Turnstile is on.

Files to look at:
- `app/web/app.py` (`csrf.init_app`, `WTF_CSRF_TIME_LIMIT`, the `CSRFError` handler)
- `app/web/blueprints/public_declaration.py` (`@csrf.exempt` and the manual `csrf.protect()`)
- `app/web/blueprints/auth_routes.py` (`/logout` is POST-only)
- `app/web/templates/csrf_error.html` and `app/web/templates/ingest_pdf.html`
- `requirements.txt`
- `tests/web/test_csrf.py`

Note from the security review: Flask-WTF's Referer check (`WTF_CSRF_SSL_STRICT`) probably never runs behind the TLS proxy, because the app has no `ProxyFix`. The token check still applies to every POST. That gap existed before this change and is out of scope.
