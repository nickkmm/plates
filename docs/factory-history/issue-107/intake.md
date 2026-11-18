DECISION: ACCEPT
RISK: low
SURFACES: ui
RESCOPED: no

## Summary
- Accepted: a Spanish 404 page replacing Flask's default English "Not Found" page.
- One app-wide 404 handler, styled like `csrf_error.html`, still returning HTTP 404.
- Works without a session (public `/declaracion` visitors) and for signed-in staff.
- Other error codes (500, 403, ...) stay out of scope, as the issue asks.

## Scope
Build:
- An app-wide handler for 404 responses. It covers unknown URLs and every existing `abort(404)` in the blueprints (orders, declarations, production, users, uploads, ingest).
- A standalone Spanish page in the same style as `csrf_error.html`: the INGSE brand, a heading such as "Página no encontrada", a short message saying the page doesn't exist, and one button back.
- The button goes to the home page (`/`). A visitor without a session who is under the public declaration form (`/declaracion...`) goes back to `/declaracion` instead, the same way the CSRF handler picks its link.
- The page renders without touching the database and without needing a signed-in user, the same way the CSRF handler renders straight from the Jinja environment. It must not echo the requested URL or any user input.
- The response keeps status 404.
- Tests: an unknown URL returns 404 with the Spanish page, signed in and signed out, and an existing `abort(404)` route (for example a missing declaration) returns the same page with 404.

Left out:
- Other error codes (500, 403, 413, ...) and the existing CSRF and upload-size handlers. They stay as they are.
- Showing the per-call messages passed to `abort(404, "...")` (two in `declarations.py`). The page shows one fixed message.

## Why
Today `app/web/app.py` registers only a `CSRFError` handler (plus a `RequestEntityTooLarge` handler on the public blueprint), so every `abort(404)` and unknown URL falls through to Werkzeug's English default. A matching standalone template and handler pattern already exists (`csrf_error.html` and `_csrf_error`), so this is one small handler, one template and tests, with no schema, money or permission change.
