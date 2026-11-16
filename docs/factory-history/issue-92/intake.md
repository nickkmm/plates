DECISION: ACCEPT
RISK: high
SURFACES: ui, security
RESCOPED: no

## Summary
- Accepted as written: turn on CSRF protection for the whole app and add a token to every form that posts.
- The current checkout has about 20 POST forms across 14 templates, one XHR upload (`ingest_pdf.html`), and one GET route that changes state (`/logout`).
- Rated high risk: it touches auth and every state-changing route, and a missed form would break a staff workflow in production.
- Two points for the spec: the public `/declaracion` form, and the users-admin templates from #65.

## Scope
Build:
- App-wide CSRF protection (for example Flask-WTF `CSRFProtect`, added to `requirements.txt`). Every POST/PUT/PATCH/DELETE without a valid token gets a 400 and changes nothing. Tokens are tied to the session, which is signed with the existing `SECRET_KEY`.
- A hidden token field in every posting form under `app/web/templates/`: `login.html`, `_nav.html` (logout), `_orders_table.html`, `customer_detail.html`, `declaration.html`, `declaration_detail.html` (4 forms), `ingest.html`, `ingest_pdf.html`, `inventory.html`, `pricing.html`, `records.html` (the PDF search form), `record_detail.html` and `review.html` (3 forms). Also the users-admin templates (`users.html`, `user_form.html`) and any other posting form that exists on `origin/main` when the spec is written.
- JavaScript requests that change data send the token in the `X-CSRFToken` header. Today that is the XHR upload in `ingest_pdf.html`, which posts the form's `FormData`, so the hidden field may already cover it. The spec should confirm this and also add the header.
- Make `/logout` POST-only. The `_nav.html` form already posts, so only the GET method goes away. No other GET route changes data: every other route is `@bp.get` and only reads.
- A missing or invalid token shows a Spanish error page, for example "La sesión expiró, recarga la página e intenta de nuevo.", with a link back, and still returns status 400 instead of a raw error.
- Keep `SESSION_COOKIE_SAMESITE="Lax"`.
- Tests: for login and at least one form per blueprint, a request without a token is rejected and a request with a valid token is accepted. The existing tests may turn CSRF off in the test config, but only where they are not testing CSRF itself.
- A section in `docs/WEB.md`, written in Spanish, saying that every new form and JS request must carry the token.

Watch-outs for the spec:
- The public `/declaracion` form is filled in by anonymous customers who may take a long time (photos, uploads). It already has its own `form_token` and Turnstile checks (`app/web/antispam.py`). It must also carry the CSRF token, but the token's time limit must not reject a slow customer: for example, no separate time limit beyond the session's. The existing 413 handler in `public_declaration.py` must keep working.
- The new error page must not break `login.html` for logged-out users.

Left out: the production board (#66) and the role-management screens (#67). Those are built later on top of this.

## Why
The app has no CSRF protection today. `app/web/app.py` only sets `SESSION_COOKIE_SAMESITE="Lax"`, no template renders a token, and `/logout` accepts GET (`app/web/blueprints/auth_routes.py`). The forms and the one XHR are easy to list, so this fits in one PR. It is rated high risk because a missed form would reject a real staff action on the next deploy.
