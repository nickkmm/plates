VERDICT: PASS

## Summary
- Every POST form has the hidden `csrf_token`: the `method="post"` and `csrf_token()` counts match in every template.
- The `ingest_pdf.html` XHR sends `X-CSRFToken`, and the form's hidden field still reaches the server through `FormData`.
- The new `csrf_error.html` is a standalone Spanish card: "La sesión expiró, recarga la página e intenta de nuevo." It returns 400 with `Cache-Control: no-store`, never calls `can()` or the database, and links back to `/declaracion` or `/`. The link works for logged-out users, who land on login.
- The logout button is a POST form in the nav, so the visible flow is unchanged. GET `/logout` now answers 405.
- `/declaracion` does the CSRF check by hand after raising its body cap, and `csrf_token` is excluded from the stored data.

## Findings
None.

## Notes
- Review was done from the diff and templates; the dev server and browser were not run.
- The error page's link text is "Volver", but it goes to `/` or `/declaracion`, not the previous page. A customer on `/declaracion` who lands there will have to refill the form. This is acceptable given the "recarga la página" message.
