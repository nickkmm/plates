# Spanish 404 page

> Issue #107: Show a Spanish 404 page instead of Flask's default

## Summary
- Add one app-wide 404 handler in `app/web/app.py`, built like the existing `_csrf_error` handler.
- Add a standalone `not_found.html` template styled like `csrf_error.html`, with the heading "Página no encontrada", a fixed message and one "Volver" button.
- The button goes to `/`. A visitor without a session on a `/declaracion/...` path goes back to `/declaracion` instead.
- The status stays 404. The page needs no database and no signed-in user.
- Add tests and update `docs/WEB.md`. Other error codes stay as they are.

## Context
- `app/web/app.py` creates the app and registers the blueprints. Its only app-wide error handler is `_csrf_error` (`@app.errorhandler(CSRFError)`). That handler picks `back_url` (`"/declaracion"` when `request.path == "/declaracion"`, otherwise `url_for("index")`). It renders `csrf_error.html` straight from `app.jinja_env.get_template(...).render(...)`, which skips the `_inject_permissions` context processor and so never touches the database. It returns the body with `make_response(body, 400)` and sets `Cache-Control: no-store`. The module docstring describes the CSRF page.
- `app/web/templates/csrf_error.html` is a standalone card page with no `_nav.html` and its own `<style>`: INGSE brand, `h1`, a message box and an `a.back` button labelled "Volver". `no_permissions.html` is the 403 page and includes `_nav.html`. It isn't the model here, because the nav needs the context processor and the database.
- Today every unknown URL and every `abort(404)` falls through to Werkzeug's English "Not Found" page. Those `abort(404)` calls are in `app/web/blueprints/declarations.py` (two of them pass a message: `abort(404, "La declaración no existe.")` and `abort(404, "El PDF solo está disponible para declaraciones aprobadas.")`), `production.py`, `users.py` (`_get_user_or_404`, `_get_role_or_404`), `ingest.py` and `app/web/uploads.py`.
- The public form is `app/web/blueprints/public_declaration.py` (`GET/POST /declaracion`, no login). It has its own `RequestEntityTooLarge` handler, which stays. Staff routes live under `/declaraciones...`, which also starts with the text `/declaracion`.
- `app/web/auth.py` keeps the signed-in user's id in `session["user_id"]` (see `_session_is_stale`). Reading `session` doesn't query the database.
- Tests: `tests/web/conftest.py` provides the `client` (signed out) and `authenticated_client` (admin) fixtures. `tests/web/test_csrf.py` is the model for asserting on a standalone error page. `tests/web/test_declarations.py` already requests missing declarations and checks for 404.
- Docs: `docs/WEB.md` lists every template in its templates table (the `csrf_error.html` row) and describes the CSRF page under "CSRF protection".

## Requirements
1. Add a new template, `app/web/templates/not_found.html`. It is a standalone page (`<html lang="es">`, no `_nav.html`, no `can(...)`, no `csrf_token()`) and copies the card layout and `<style>` of `csrf_error.html`. It has:
   - title "Plates — Página no encontrada"
   - the "INGSE" brand
   - `<h1>Página no encontrada</h1>`
   - the fixed message "La página que buscas no existe o ya no está disponible."
   - one `<a class="back" href="{{ back_url }}">Volver al inicio</a>` button

   The only variable it uses is `back_url`.
2. In `app/web/app.py`, register `@app.errorhandler(404)` (a function such as `_not_found`) next to `_csrf_error`, following the same pattern:
   - render with `app.jinja_env.get_template("not_found.html").render(back_url=...)`
   - return `make_response(body, 404)`
   - set `Cache-Control: no-store`
   - don't query the database and don't call `auth.current_user()` / `auth.has_permission()`.
3. `back_url` is `"/declaracion"` when all of these hold:
   - the request path is exactly `/declaracion` or starts with `/declaracion/` (not `/declaraciones...`)
   - the session has no `"user_id"`

   Otherwise `back_url` is `url_for("index")` (`/`).
4. The page never shows the requested URL, query string, the `abort()` description or any other request data. The two `abort(404, "...")` calls in `declarations.py` stay in place, but their message is not shown.
5. The handler covers unknown URLs and every existing `abort(404)` in the blueprints and `uploads.py`. Change no blueprint code.
6. Add the new page to the module docstring of `app/web/app.py` (one sentence, next to the CSRF one).
7. Update `docs/WEB.md`:
   - add a `not_found.html` row to the templates table, next to `csrf_error.html`: a 404 page for unknown URLs and `abort(404)`, no nav, no login, no database, quoting its message and saying where it links
   - add a short bullet in the auth/security list near the CSRF bullet, saying that every 404 answers this page with status 404 and `Cache-Control: no-store`.
8. Add tests in a new file, `tests/web/test_not_found.py`:
   - signed out (`client`): `GET /no-existe` returns 404, the body contains "Página no encontrada", the body does not contain "Not Found", and the link points to `/`
   - signed out: `GET /declaracion/no-existe` returns 404 with the page, and its link points to `/declaracion`
   - signed in (`authenticated_client`): `GET /no-existe` returns 404 with the page, and its link points to `/`
   - signed in: a missing declaration (for example `GET /declaraciones/999999/pdf`, an `abort(404, "...")` route, and `GET /declaraciones/999999/file/signature`) returns 404 with the page and no "La declaración no existe."
   - the response for an unknown URL carries `Cache-Control: no-store`
   - the page does not echo the request: `GET /no-existe-<script>` does not contain `<script>`.

## Acceptance criteria
1. `app/web/templates/not_found.html` exists. It contains "Página no encontrada" and "Volver al inicio", has no `{% include "_nav.html" %}`, and its only template variable is `back_url`. Verify by reading the file.
2. `app/web/app.py` has an `@app.errorhandler(404)` handler that renders `not_found.html` through `app.jinja_env.get_template` and returns status 404 with `Cache-Control: no-store`. Verify by reading the file.
3. `pytest tests/web/test_not_found.py` passes, with `POSTGRES_PASSWORD` and `TEST_POSTGRES_DB=plates_test_16` set and the local db up. It covers every case in requirement 8.
4. Run the app from the worktree on the job's port (see CLAUDE.md, "Checking a page") and check these by hand:
   - Signed out, http://localhost:<port>/no-existe shows the INGSE card with "Página no encontrada" and a "Volver al inicio" button to `/`. Use `curl -i` to see the 404 status.
   - Signed out, `/declaracion/xyz` shows the same page, with the button going to `/declaracion`.
   - Signed in, `/no-existe` and `/declaraciones/999999` show the same page with status 404, and the button goes to `/`.
5. `git diff origin/main -- app/web/blueprints app/web/uploads.py` is empty.
6. `docs/WEB.md` has a `not_found.html` row in the templates table, and its auth/security list mentions the 404 page.

## Out of scope
- Handlers or pages for other codes (403, 405, 413, 500, ...), and any change to the existing CSRF handler or the `RequestEntityTooLarge` handler in `public_declaration.py`.
- Showing the per-call `abort(404, "...")` messages, or editing or removing those calls.
- A shared `base.html` or a refactor of `csrf_error.html` into a shared layout.
- Showing the nav, the user's name or permission-aware links on the 404 page.
- Logging, Sentry or analytics changes for 404s.
- Changing which routes return 404.

## Assumptions
- The template is named `not_found.html` and the handler is registered on the code `404`, not on `NotFound`. Both catch `abort(404)` and unknown routes, so this follows the closest repo convention.
- The button text is "Volver al inicio" and the message is "La página que buscas no existe o ya no está disponible." The issue asks for "a short message" and a link home, and this is short UI copy in Spanish.
- A visitor counts as "without a session" when `session` has no `"user_id"`. That is the same key `auth._session_is_stale` checks, and reading it touches no database.
- The `/declaracion` back link matches `/declaracion` and `/declaracion/...` only, so staff paths such as `/declaraciones/999` still go to `/`.
- The page gets `Cache-Control: no-store` like the CSRF page, so a 404 for an id that is created later is never cached.
