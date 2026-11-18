STATUS: READY

# Build: Spanish 404 page

## Summary
- Every 404 (unknown URL or `abort(404)`) now answers a standalone Spanish page, `not_found.html`, with status 404 and `Cache-Control: no-store`.
- The "Volver al inicio" button goes to `/`, or to `/declaracion` for a visitor without a session under `/declaracion` / `/declaracion/...`.
- The page never shows the URL or the `abort()` message, and needs no database or signed-in user; no blueprint code changed.
- New tests in `tests/web/test_not_found.py`; one existing test that asserted the PDF abort message now asserts the 404 page.
- Draft PR: https://github.com/nickkmm/plates/pull/108

## Changes
- `app/web/templates/not_found.html` (new): copy of the `csrf_error.html` card and style; title "Plates — Página no encontrada", INGSE brand, `<h1>Página no encontrada</h1>`, message "La página que buscas no existe o ya no está disponible.", `<a class="back" href="{{ back_url }}">Volver al inicio</a>`. Only variable: `back_url`.
- `app/web/app.py`: `@app.errorhandler(404) _not_found` next to `_csrf_error`; picks `back_url` from the path and `"user_id" not in session`, renders via `app.jinja_env.get_template("not_found.html")`, `make_response(body, 404)`, `Cache-Control: no-store`. Imports `session` from flask. Module docstring gains one sentence on the 404 page.
- `docs/WEB.md`: `not_found.html` row after `csrf_error.html` in the templates table; bullet after the CSRF bullet in the auth/security list.
- `tests/web/test_not_found.py` (new): all cases of requirement 8, plus signed-in `/declaracion/no-existe` links to `/`.
- `tests/web/test_declarations.py`: `test_pending_with_loss_is_404_with_a_clear_message` asserted the `abort(404, "El PDF solo está disponible...")` text, which requirement 4 says is no longer shown; renamed to `test_pending_with_loss_is_404_with_the_not_found_page` and it now asserts "Página no encontrada".

## Acceptance criteria
1. [x] Template exists with "Página no encontrada" and "Volver al inicio", no `_nav.html` include, only variable `back_url`: read the file after writing it.
2. [x] `app/web/app.py` has `@app.errorhandler(404)` rendering `not_found.html` through `app.jinja_env.get_template`, returning 404 with `Cache-Control: no-store`: read the file.
3. [x] `POSTGRES_PASSWORD=... TEST_POSTGRES_DB=plates_test_16 .venv/Scripts/python -m pytest tests/web/test_not_found.py -q`: 7 passed (DB up, no skips).
4. [x] App run from the worktree on port 5260 against local Postgres (`plates_test_16`, user `testuser`), checked with `curl -si`:
   - signed out `/no-existe`: `HTTP/1.1 404 NOT FOUND`, `Cache-Control: no-store`, INGSE card with "Página no encontrada" and `<a class="back" href="/">Volver al inicio</a>`.
   - signed out `/declaracion/xyz`: 404, same page, `href="/declaracion"`.
   - signed in (login 302 -> /ingest) `/no-existe` and `/declaracion/xyz`: 404, same page, `href="/"`.
   - signed in `/declaraciones/999999/pdf` and `/declaraciones/999999/file/signature`: 404, same page, `href="/"`.
   - Note: signed in `/declaraciones/999999` answers `302 -> /declaraciones` (the detail route redirects to the list for a missing id; it does not `abort(404)`). That is existing blueprint behaviour the spec forbids changing (requirement 5, out of scope "Changing which routes return 404"), so the two `abort(404)` declaration routes above were checked instead.
5. [x] `git diff origin/main -- app/web/blueprints app/web/uploads.py`: empty.
6. [x] `docs/WEB.md` has the `not_found.html` templates row and the 404 bullet in the auth/security list.

## Validation
- `bash factory/checks.sh` without DB env: passed (DB tests skipped).
- `bash factory/checks.sh` with `POSTGRES_PASSWORD`/`POSTGRES_USER` from `.env` and `TEST_POSTGRES_DB=plates_test_16`: first run failed 1 test (`TestPdf::test_pending_with_loss_is_404_with_a_clear_message`, expected the abort message); after updating it, passed: 1843 passed.
- `pytest tests/web/test_not_found.py`: 7 passed.
- Manual curl checks on port 5260 as above; dev server stopped afterwards.

## Log
### Round 1
- Set up `.venv` in the worktree, added the template, handler, docstring, docs and tests.
- Updated the one existing test that asserted a now-hidden `abort(404)` message, as requirement 4 implies.
- Ran checks with the job database, checked pages with curl, committed in two commits, pushed and opened draft PR #108.
