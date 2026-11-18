VERDICT: PASS

## Summary
- Adds one app-wide `@app.errorhandler(404)` (`_not_found`) next to `_csrf_error`, rendering a standalone Spanish `not_found.html` with status 404 and `Cache-Control: no-store`.
- The back link is `/declaracion` for a signed-out visitor under `/declaracion` or `/declaracion/...`, and `/` otherwise, matching spec requirement 3.
- The handler reads only `request.path` and `session`: no database, no `can(...)`, no context processor, and the URL or `abort()` message is never rendered.
- The template's card and `<style>` are identical to `csrf_error.html`; the docs row and bullet, the docstring sentence and `tests/web/test_not_found.py` cover requirements 6 to 8.
- The one changed existing test (`test_pending_with_loss_is_404_...`) now checks the new page instead of the hidden abort message, which requirement 4 implies. It still asserts 404 and that no PDF call was made.

## Findings
None.

## Notes
- `tests/web/test_declarations.py`: the renamed test keeps its 404 and `calls == []` assertions, so the behaviour check is intact; only the message check moved to the new page title.
- `app/web/app.py` docstring addition is two lines but one sentence, as the spec asks.
- The button copy is "Volver al inicio" (spec-given), while the CSRF page uses "Volver"; this is intentional per the spec.
