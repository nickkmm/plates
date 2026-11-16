STATUS: READY

# Build: App-wide CSRF protection with a token on every form

## Summary
- Flask-WTF `CSRFProtect` (pinned `Flask-WTF==1.3.0`) now covers the whole app. A POST without a valid token gets 400 and the Spanish page `csrf_error.html`.
- All 23 posting forms carry the hidden `csrf_token`. The `/ingest/pdf` XHR also sends `X-CSRFToken`.
- `/declaracion` checks the token by hand after raising its 40 MB cap, and `/logout` is POST-only.
- The new `tests/web/test_csrf.py` (22 tests) tests CSRF turned on. The other tests turn it off only in their fixtures. The docs are updated.
- Gate passes (1724 passed). Draft PR #93 is open.

## Changes
- `requirements.txt`: adds `Flask-WTF==1.3.0`, with a comment.
- `pyproject.toml`: adds `flask_wtf.*` to the mypy `ignore_missing_imports` overrides.
- `app/web/csrf.py` (new): the shared `csrf = CSRFProtect()` instance.
- `app/web/app.py`:
  - calls `csrf.init_app(app)` after `secret_key`, and sets `WTF_CSRF_TIME_LIMIT=None`;
  - adds a `CSRFError` handler that renders `csrf_error.html` with 400 and `Cache-Control: no-store`. The back link goes to `/declaracion` or `/`, and the page skips the context processors, so there is no DB or `can()` call;
  - updates the docstring.
- `app/web/blueprints/auth_routes.py`: `/logout` is now `@bp.post`.
- `app/web/blueprints/public_declaration.py`:
  - `@csrf.exempt`, plus a manual `csrf.protect()` right after `request.max_content_length = MAX_REQUEST_BYTES`, guarded by `WTF_CSRF_ENABLED` like the automatic check;
  - `csrf_token` added to `_EXCLUDED_RAW_KEYS`.
- `app/web/templates/csrf_error.html` (new): standalone Spanish card page in the `login.html` style.
- Templates with the hidden token as the first child of every `method="post"` form: `login`, `_nav`, `_orders_table`, `customer_detail`, `declaration`, `declaration_detail` (4), `ingest`, `ingest_pdf` (plus the `X-CSRFToken` header on the XHR), `inventory`, `pricing`, `records`, `record_detail`, `review` (3) and `user_form` (3).
- `tests/web/conftest.py`, `tests/web/test_users.py`: the `client` fixture and `_fresh_client` set `WTF_CSRF_ENABLED=False` and restore it on teardown.
- `tests/web/test_csrf.py` (new): tests with CSRF on for login, logout (405 on GET), customers, declarations, ingest (plus PDF header-only and form-field), inventory, pricing, records, review, users and `/declaracion` (no token, token, 13 MB not 413, over 40 MB still the friendly 413), plus the template scan.
- `docs/WEB.md`:
  - new "Protección CSRF" section;
  - `/logout` listed as POST;
  - a CSRF line in the security list;
  - a `csrf_error.html` row in the Templates table.
- `AGENTS.md`: CSRF line under Project.

## Acceptance criteria
1. [x] Pinned Flask-WTF: `grep -i flask-wtf requirements.txt` gives `Flask-WTF==1.3.0`.
2. [x] Login without a token returns 400 with the Spanish message, no "Salir", and `/records` still redirects to login: `TestLogin::test_without_token_is_rejected_and_logs_nobody_in` passes. A manual HTTP POST to the dev server also returned 400, with the message, `Cache-Control: no-store`, a link to `/` and no "Salir".
3. [x] Login with a token returns 302 and logs in: `TestLogin::test_with_token_logs_in` passes.
4. [x] One route per blueprint returns 400 and changes nothing without a token, and behaves as today with one: `TestCustomers`, `TestDeclarations`, `TestIngest`, `TestInventory`, `TestPricing`, `TestRecords`, `TestReview`, `TestUsers`, `TestLogout` and `TestPublicDeclaration` in `test_csrf.py` all pass.
5. [x] Header-only token on `/ingest/pdf` is accepted: `test_pdf_with_the_token_only_in_the_header_is_accepted` passes. `grep -n X-CSRFToken ingest_pdf.html` gives line 77, `xhr.setRequestHeader("X-CSRFToken", form.elements["csrf_token"].value);`.
6. [x] Every posting form carries the token: a loop over `app/web/templates/*.html` comparing `grep -c 'method="post"'` with `grep -c 'csrf_token()'` found no mismatches, and `test_every_posting_form_carries_the_token` passes.
7. [x] Logout is POST-only: `GET /logout` returns 405 and keeps the session, and `POST /logout` with a token logs out (`TestLogout`, plus `test_login.py` using POST). `auth_routes.py:50` reads `@bp.post("/logout")`. A manual GET on the dev server returned 405.
8. [x] `/declaracion` with CSRF on: 13 MB with a token is not 413, over 40 MB gets the friendly 413 ("demasiado grandes"), and no token gives 400 with nothing stored (`TestPublicDeclaration`). A manual POST without a token returned 400 with the back link `/declaracion`.
9. [x] Settings: `app.py:119` has `SESSION_COOKIE_SAMESITE="Lax"` and `app.py:129` has `WTF_CSRF_TIME_LIMIT=None`.
10. [~] Manual browser walk: done in part.
    - `agent-browser` is not installed on this machine, so I used scripted HTTP requests against the dev server on port 5190 instead:
      - `/login` and `/declaracion` render the token;
      - a POST without a token or with a bad token returns 400 with the Spanish page and the right back link;
      - `GET /logout` returns 405;
      - a login POST with a valid token passed the CSRF check and reached the route.
    - I could not log in as staff. The local dev DB `plates` is not migrated: the route answered 500 with `relation "user_roles" does not exist`, which is not caused by this branch. Creating a temporary dev user was also denied by the permission system.
    - The staff form submissions are covered by `test_csrf.py`, with tokens read from the rendered pages, and by the full suite. Please do the browser walk by hand before merging.
11. [x] Docs: `docs/WEB.md` has the "Protección CSRF" section (line 149), with the `csrf_token()` snippet and the `X-CSRFToken` rule, and the route table lists `| POST | /logout |`. `AGENTS.md` lines 24-26 state the token rule.

## Validation
- `bash factory/checks.sh`, with `POSTGRES_USER`/`POSTGRES_PASSWORD` from the main `.env`, `TEST_POSTGRES_DB=plates_test_9` and `TEST_REQUIRE_DB=1` (the gate's environment): PASS.
  - ruff: clean.
  - mypy: no issues in 49 files.
  - pytest: 1724 passed in 603 s.
  - The shell and factory script checks are OK.
- `pytest tests/web/test_public_declaration.py tests/web/test_csrf.py`: 120 passed.
- Template token-count loop: no mismatches.
- Manual HTTP checks against the dev server on port 5190: see criterion 10. The server was stopped afterwards.

## Log
### Round 1
- The previous builder left four commits: protection, tokens, tests and docs. I checked `git log origin/main..HEAD` against the spec, and every requirement is covered. No code changes were needed this session.
- Why the earlier test run looked stuck: my first run exported the whole main `.env` (Turnstile keys, `UPLOADS_DIR`, and so on). That slowed the `/declaracion` tests to about 60 s each.
  - I stopped that run, killing only this worktree's pytest process.
  - I reran with only the variables the gate passes. It finished in 10 minutes and passed.
- Pushed `factory/009-add-csrf-tokens-to` and opened draft PR #93 (https://github.com/nickkmm/plates/pull/93). The PR body has no "Generated with" line, per CLAUDE.md.
