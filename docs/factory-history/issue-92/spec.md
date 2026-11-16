# App-wide CSRF protection with a token on every form

> Issue #92: Add CSRF tokens to every form and state-changing request

## Summary
- Turn on Flask-WTF `CSRFProtect` for the whole app, so every POST/PUT/PATCH/DELETE without a valid token is rejected with a 400.
- Add the hidden token to all 23 posting forms in 15 templates, and send it as an `X-CSRFToken` header from the one XHR (`ingest_pdf.html`).
- Make `/logout` POST-only. No other route changes data on a GET.
- A missing or invalid token shows a Spanish page ("La sesión expiró...") with status 400. The public `/declaracion` form checks the token by hand, after it raises its own 40 MB body cap.
- New tests check that a request without a token is rejected and one with a token is accepted, for login and one form per blueprint. `docs/WEB.md` gets a CSRF section.

## Context
- `app/web/app.py` creates `app`, sets `app.secret_key` from `SECRET_KEY`, registers 11 blueprints and sets `SESSION_COOKIE_SAMESITE="Lax"`, `PERMANENT_SESSION_LIFETIME=12h`, `MAX_CONTENT_LENGTH=12 MB` and `MAX_FORM_MEMORY_SIZE=2 MB`. It has no CSRF protection, and no template renders a token. Its module docstring lists every route (`auth_routes.py -- GET/POST /login, GET/POST /logout`).
- Routes that change data are all `@bp.post` or `methods=["GET","POST"]`. The routes are `auth_routes.py` (`/login`, `/logout`), `customers.py`, `declarations.py` (approve, reject, notarized, notarized/undo), `ingest.py` (`/ingest`, `/ingest/pdf`), `inventory.py`, `pricing.py`, `public_declaration.py` (`/declaracion`), `records.py` (save, search-pdf, delete), `review.py` (update, resolve, delete) and `users.py` (`/usuarios/nuevo`, `/usuarios/<id>`, `/usuarios/<id>/password`). `cash_summary.py` is GET-only. The one GET route that changes state is `/logout` (`@bp.route("/logout", methods=["GET", "POST"])` in `app/web/blueprints/auth_routes.py`). The GET handlers of `/ingest/pdf` and the `/usuarios` routes only render.
- The posting forms (`method="post"`) in `app/web/templates/` are:
  - `login.html` (1)
  - `_nav.html` (logout, 1)
  - `_orders_table.html` (records delete, 1)
  - `customer_detail.html` (1)
  - `declaration.html` (`#declForm`, 1)
  - `declaration_detail.html` (4: notarized/undo, notarized, approve, reject)
  - `ingest.html` (1)
  - `ingest_pdf.html` (`#pdfForm`, 1)
  - `inventory.html` (1)
  - `pricing.html` (one per row)
  - `records.html` (search-pdf, 1)
  - `record_detail.html` (1)
  - `review.html` (3 per row: update, resolve, delete)
  - `user_form.html` (3: edit, password, new)

  `users.html` has no posting form today; it only links to `user_form.html`. `cash_summary.html` and `declarations.html` use GET forms, which need no token.
- JavaScript that sends data: only `ingest_pdf.html`. It sends an `XMLHttpRequest` POST to `form.action` with `new FormData(form)` and then writes the response into the page with `document.write`. No template uses `fetch`.
- `/declaracion` (`app/web/blueprints/public_declaration.py`) is public. `declaration_submit` sets `request.max_content_length = MAX_REQUEST_BYTES` (40 MB) **before** it first reads `request.form`. Then it checks the honeypot, its own signed `form_token` (`app/web/antispam.py`) and Turnstile. `@bp.errorhandler(RequestEntityTooLarge)` re-renders the form with a 413. An app-wide `before_request` CSRF check would read `request.form` under the global 12 MB cap before the route raises it. That would reject valid declarations between 12 and 40 MB, so this route has to check the token by hand.
- `_page()` in `public_declaration.py` adds the no-store and anti-framing headers to the public pages. `login.html` and `declaration_unavailable.html` are standalone pages without `_nav.html`. There is no `base.html`: each template has its own `<style>`.
- Tests: `tests/web/conftest.py` has the `client` fixture (`TESTING=True`) and the `authenticated_client` / `produccion_client` / `punto_venta_client` fixtures, which log in by posting to `/login`. `tests/web/test_users.py::_fresh_client` builds its own clients. `tests/web/test_public_routes.py` sends every method of every non-public rule anonymously and expects 302/401/403. `tests/web/test_login.py::test_logout_clears_session` posts to `/logout`. `tests/web/test_public_declaration.py::TestSizeLimits` covers the 413 page and the 4 MB `/ingest/pdf` upload.
- Docs: `docs/WEB.md` has the route table (the `/logout` row says `GET/POST`), a security section (session cookie, around line 140), "Rutas públicas" and the "Templates" table. `pyproject.toml` lists the mypy `ignore_missing_imports` overrides for untyped packages.

## Requirements
1. Add `Flask-WTF` to `requirements.txt`, pinned to an exact version like the other entries (latest 1.x release compatible with `flask==3.1.3`), with a one-line comment saying it is used for CSRF protection.
2. Create a `CSRFProtect()` instance in a new module `app/web/csrf.py`, so blueprints can import it without importing `app.web.app`. Initialize it in `app/web/app.py` with `csrf.init_app(app)`, after `app.secret_key` is set. Set `WTF_CSRF_TIME_LIMIT=None` in the existing `app.config.update(...)`, so a token lives as long as the session (12 h for staff, the browser session for anonymous visitors) and a slow customer on `/declaracion` is never rejected for taking a long time. Keep `SESSION_COOKIE_SAMESITE="Lax"` and every other existing setting unchanged. Update the module docstring (logout line, a CSRF mention).
3. Every POST/PUT/PATCH/DELETE to any route is rejected with status 400 when the token is missing or invalid, and the route body never runs, so nothing changes. The only exception is `/declaracion` (requirement 7), which gives the same result through a manual check.
4. Register an app-level handler for `flask_wtf.csrf.CSRFError` in `app/web/app.py`. It renders a new standalone template `app/web/templates/csrf_error.html` with status 400:
   - The page shows "La sesión expiró, recarga la página e intenta de nuevo."
   - It has a link back: to `/declaracion` when the failed request was to that path, otherwise to `/` (which sends a logged-out user to `/login`).
   - It does **not** include `_nav.html` and calls neither `can()` nor the database, so it works for logged-out visitors.
   - It sets `Cache-Control: no-store`.
   - It is written in Spanish, with the same simple card styling as `login.html`.
5. Add `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">` as the first child of every posting form listed in Context. That covers all forms in `login.html`, `_nav.html`, `_orders_table.html`, `customer_detail.html`, `declaration.html`, `declaration_detail.html` (all 4), `ingest.html`, `ingest_pdf.html`, `inventory.html`, `pricing.html` (every row), `records.html` (search-pdf), `record_detail.html`, `review.html` (all 3 per row) and `user_form.html` (all 3). Also add it to any other `method="post"` form on the branch. When done, `grep -c 'method="post"'` and `grep -c 'csrf_token()'` must match in every template.
6. In `ingest_pdf.html`, the XHR also sends the token as a header: `xhr.setRequestHeader("X-CSRFToken", ...)`, with the value read from the form's hidden `csrf_token` input, before `xhr.send`. The hidden field already reaches the server through `FormData`, so the header is the second layer.
7. `/declaracion`: exempt `declaration_submit` from the automatic check (`@csrf.exempt`). Inside the route, right after `request.max_content_length = MAX_REQUEST_BYTES` and before the honeypot check, call `csrf.protect()`. A bad token raises `CSRFError` and gets the page from requirement 4, with status 400 and no files saved. The existing `form_token`, Turnstile, rate limit and 413 handler keep working unchanged. Leave `"csrf_token"` out of `submitted_data` by adding it to `_EXCLUDED_RAW_KEYS`.
8. `/logout` becomes POST-only (`@bp.post("/logout")`). A GET to `/logout` answers 405 and leaves the session as it is. No other route changes data on a GET.
9. Tests that are not about CSRF keep running with CSRF turned off, and the switch lives only in test code. The `client` fixture in `tests/web/conftest.py`, and `_fresh_client` in `tests/web/test_users.py`, set `WTF_CSRF_ENABLED=False` and restore the earlier value on teardown. The rest of the existing suite passes unchanged except where a test hits `GET /logout`.
10. New test module `tests/web/test_csrf.py`. It uses a fixture that sets `WTF_CSRF_ENABLED=True` and restores it on teardown, and reads the token from a rendered page (regex on `name="csrf_token" value="..."`). It covers:
    - `/login`: a POST without a token gets a 400 with the Spanish message and no session user. A POST with a token logs in (302).
    - For at least one posting route of each blueprint that has one (`customers`, `declarations`, `ingest`, `inventory`, `pricing`, `records`, `review`, `users`, `auth_routes` logout, `public_declaration`): without a token, a 400 and the database row (or session) unchanged. With a valid token, the same status as today (the existing success test for that route shows what to expect).
    - `/ingest/pdf`: a POST that carries the token only in the `X-CSRFToken` header (no form field) is accepted, so it does not get a 400.
    - `/declaracion` (with the existing `stub`/`enabled` fixtures from `test_public_declaration.py`, or equivalent): without a token, a 400 and no stored declaration or files. With a token, the same success as today. With CSRF on, a token and a body between 12 MB and 40 MB of junk is **not** rejected with 413 (the route-level cap still applies). With CSRF on, an over-40 MB body still gets the friendly 413.
    - `GET /logout` returns 405 and the user stays logged in.
    - A test that scans `app/web/templates/*.html` and asserts that every `<form` with `method="post"` contains `csrf_token()`.
11. Update `tests/web/test_login.py` and any other test that uses `GET /logout`, so it uses POST.
12. If Flask-WTF ships no type information, add `flask_wtf.*` to the existing `ignore_missing_imports` override list in `pyproject.toml`. Only do it if mypy complains.
13. Docs, in Spanish:
    - `docs/WEB.md`: a new section "Protección CSRF". It says that `CSRFProtect` covers the whole app, that every new `method="post"` form must start with `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`, and that every JS request that changes data must send the `X-CSRFToken` header. It names the error page and the 400 status, explains why `/declaracion` checks by hand (the 40 MB cap), says the tokens have no time limit beyond the session's, and says that tests turn CSRF off only in the fixtures, with `tests/web/test_csrf.py` testing it on.
    - In the route table, change `/logout` to `POST`.
    - Add a CSRF line to the security list next to the cookie line.
    - Add a `csrf_error.html` row to the Templates table.
    - In `AGENTS.md`, add one English line under Project saying that every posting form and JS request must carry the CSRF token (see `docs/WEB.md`).

## Acceptance criteria
1. `requirements.txt` has a pinned `Flask-WTF==<version>` line. Check with `grep -i flask-wtf requirements.txt`.
2. With CSRF on (as in `tests/web/test_csrf.py`), `POST /login` with valid credentials and no `csrf_token` returns 400. The body contains "La sesión expiró, recarga la página e intenta de nuevo." and no "Salir" button, and a following `GET /records` still redirects to `/login`. Check with `pytest tests/web/test_csrf.py`.
3. With CSRF on, the same login POST with the token from `GET /login` returns 302 and the session is logged in (test_csrf.py).
4. For one posting route in each blueprint (customers, declarations, ingest, inventory, pricing, records, review, users, logout, `/declaracion`), a POST without a token returns 400 and leaves the targeted row or session unchanged. The same POST with a valid token behaves as it does today (test_csrf.py).
5. `POST /ingest/pdf` with the token only in the `X-CSRFToken` header is not rejected with 400 (test_csrf.py). `grep -n "X-CSRFToken" app/web/templates/ingest_pdf.html` finds a `setRequestHeader` call.
6. Every posting form carries the token. For each file in `app/web/templates/`, `grep -c 'method="post"'` equals `grep -c 'csrf_token()'`, and the template-scan test in test_csrf.py passes.
7. `GET /logout` returns 405 and does not clear the session. `POST /logout` with a token logs out (test_csrf.py, test_login.py). `grep -n "logout" app/web/blueprints/auth_routes.py` shows `@bp.post`.
8. `/declaracion` with CSRF on: a body of about 13 MB with a valid token is not answered with 413. A body over 40 MB still gets the 413 page containing "demasiado grandes". A submission without a token gets a 400 and stores no declaration and no files (test_csrf.py).
9. `grep -n "SESSION_COOKIE_SAMESITE" app/web/app.py` still shows `"Lax"`, and `grep -n "WTF_CSRF_TIME_LIMIT" app/web/app.py` shows `None`.
10. Manual: run the app from the worktree (see AGENTS.md, "Checking a page"), log in, and submit each staff form at least once without a 400: register an order, upload a pedido PDF, save a record, review actions, inventory, prices, customer, approve or reject a declaration, create or edit a user and change a password, then log out. In the browser dev tools, deleting the `csrf_token` input before submitting shows the Spanish error page with a working link.
11. `docs/WEB.md` has a "Protección CSRF" section with the `csrf_token()` snippet and the `X-CSRFToken` rule, and its route table lists `/logout` as `POST`. Check with `grep -n "CSRF" docs/WEB.md`. `AGENTS.md` mentions the CSRF token rule.

## Out of scope
- The production board (#66) and the role-management screens (#67).
- Using WTForms `FlaskForm` classes or rewriting form handling. Only the `CSRFProtect` extension and the `csrf_token()` template global are used.
- Changing the `/declaracion` anti-spam pipeline (`form_token`, Turnstile, rate limit, honeypot) or replacing its `form_token` with the CSRF token.
- Adding a shared `base.html`, refactoring templates, or restyling pages beyond the new error page.
- Changing cookie settings (`SAMESITE`, `SECURE`, lifetime), adding `ProxyFix`, CSP headers, login throttling or password policy.
- Changing `tests/web/test_public_routes.py`'s allowlist or expected statuses (it runs with CSRF off through the `client` fixture).
- Editing `factory/checks.sh` or `factory/config.json` (protected), or anything under `docs/factory-history/`.

## Assumptions
- Flask-WTF `CSRFProtect` is the mechanism. The issue suggests it, and it is the standard Flask extension: it gives a session-bound token signed with `SECRET_KEY` and checks the `X-CSRFToken` header.
- `WTF_CSRF_TIME_LIMIT=None`. Intake asked for no separate time limit beyond the session's, and staff tabs stay open for hours within the 12 h session.
- `/declaracion` is exempt from the automatic check and calls `csrf.protect()` by hand. Without that, the app-wide check would parse the body under the 12 MB global cap and reject valid declarations of up to 40 MB.
- The error page links to `/declaracion` for the public form and to `/` everywhere else. `request.referrer` is not reliable, and a POST URL would answer 405 to a GET.
- An anonymous POST without a token to a protected route now gets 400 instead of the login redirect. CSRF is checked first, which is safe, and `test_public_routes.py` runs with CSRF off.
- Flask-WTF's default `WTF_CSRF_SSL_STRICT` (a same-origin Referer check over HTTPS) is kept. The public pages already send `Referrer-Policy: same-origin`, so same-origin form posts carry a Referer.
- `users.html` gets no token because it has no posting form today. The template-scan test covers it automatically if one is added.
