ESCALATE

## Summary
- Adds one app-wide `@app.errorhandler(404)` in `app/web/app.py` and a standalone Spanish `not_found.html` (INGSE card copied from `csrf_error.html`, "Página no encontrada", "Volver al inicio").
- Back link goes to `/declaracion` for a signed-out visitor under `/declaracion` or `/declaracion/...`, and to `/` otherwise. Status stays 404 with `Cache-Control: no-store`.
- No blueprint code changed. `docs/WEB.md` is updated, there are 7 new tests, and one existing test now checks for the new page instead of the hidden `abort()` message.
- Gate PASS (1843 tests). Code, UI and UX reviews all PASS with no findings.
- Escalated because a person always merges here. One hand-check in the spec could not be done as written (see criterion 4).

## Why
The change is small and stays inside intake's scope. It touches no money, auth, schema, migrations, env vars or deploy config, and it adds no dependency. The handler only reads `request.path` and `session["user_id"]` and renders a fixed template, so it never echoes request data. One hand-check in acceptance criterion 4 (signed-in `/declaraciones/999999` shows the 404 page) can't pass without changing blueprint code, which the spec forbids. The builder checked two other missing-declaration routes that do `abort(404)` instead. That is a mistake in the spec, not in the code, but strictly it means not every criterion is met as written.

## Acceptance criteria
1. Template exists with "Página no encontrada" and "Volver al inicio", no `_nav.html` include, and `back_url` as its only variable: met. I read it in the diff.
2. `@app.errorhandler(404)` renders `not_found.html` through `app.jinja_env.get_template`, returns 404 and sets `Cache-Control: no-store`: met. I read it in the diff.
3. `pytest tests/web/test_not_found.py` passes and covers every case in requirement 8: met. 7 passed per build.md, and the full suite passed at the gate (1843). The tests cover signed in and signed out, `/declaracion/...`, the two `abort(404)` declaration routes, no-store, and no echo of `<script>`.
4. Hand checks with curl: met for signed-out `/no-existe`, signed-out `/declaracion/xyz` and signed-in `/no-existe`. **Not met as written** for signed-in `/declaraciones/999999`. I checked it myself: `_render_detail` in `app/web/blueprints/declarations.py` (around line 294) redirects a missing id to the list (302) and never calls `abort(404)`. The builder curl-checked `/declaraciones/999999/pdf` and `/declaraciones/999999/file/signature` instead, and both returned 404 with the page.
5. `git diff origin/main -- app/web/blueprints app/web/uploads.py` is empty: met. I ran it myself and got 0 bytes.
6. `docs/WEB.md` has the `not_found.html` row and the 404 bullet: met. I read it in the diff.

## For the human
Recommendation: merge

- The only gap is criterion 4's `/declaraciones/999999` check. That route redirects to the list on purpose (existing behaviour), and the spec forbids changing which routes return 404. Decide whether a redirect is fine there. If you would rather it showed the 404 page, that is a separate small issue.
- One existing test changed: `tests/web/test_declarations.py`, `test_pending_with_loss_is_404_with_the_not_found_page`. Staff who open the PDF of a pending declaration with a loss now see the generic "Página no encontrada" instead of "El PDF solo está disponible para declaraciones aprobadas.". The spec chose this (the page shows one fixed message), but it loses a useful hint for staff. Confirm you're fine with that.
- Taste, raised by the UI and UX reviewers: the 404 message sits in the red error box copied from the CSRF page.
- The public declaration form itself is untouched. Only the 404 page's back link is chosen for `/declaracion/...` paths.
- Files to look at: `app/web/app.py` (`_not_found`), `app/web/templates/not_found.html`, `tests/web/test_not_found.py`, `tests/web/test_declarations.py`, `docs/WEB.md`.
