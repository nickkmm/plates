VERDICT: PASS

## Summary
- Reviewed the diff, spec and build notes; the change is one standalone 404 page plus an app-wide handler.
- Copy is short, plain Spanish: heading "Página no encontrada", one message, one "Volver al inicio" button.
- Signed-out visitors on /declaracion/... go back to the public form; everyone else goes to /. No dead ends.
- Page reuses the csrf_error card (viewport meta, fluid card width, max 360px), so it works at phone width. It is a real link, with lang="es".
- The page does not echo the URL or the abort() message. I did not drive a browser; the build notes record curl checks and the tests cover each path.

## Findings
None.

## Notes
- Signed-in staff who hit a 404 see "Volver al inicio" going to /, which is the usual landing; fine.
- The message sits in a red error box, which is slightly alarming for a 404, but it matches the CSRF page. Taste only.
