VERDICT: PASS

## Summary
- Flask-WTF CSRFProtect is wired through a new app/web/csrf.py, with WTF_CSRF_TIME_LIMIT=None and a Spanish 400 error page (csrf_error.html) that skips the DB and `can()`.
- All 23 posting forms carry the token. The per-template `method="post"` and `csrf_token()` counts match. The PDF XHR sends X-CSRFToken.
- /logout is POST-only. /declaracion is exempt from the automatic check and calls csrf.protect() right after the 40 MB cap is raised. `csrf_token` is added to _EXCLUDED_RAW_KEYS.
- Test fixtures turn CSRF off and restore the earlier value. tests/web/test_csrf.py covers each blueprint, the header-only upload, the 13 MB and 40 MB cases, and a template scan. Docs and AGENTS.md are updated. checks.md shows 1724 passed.

## Findings
None.

## Notes
- The `if current_app.config["WTF_CSRF_ENABLED"]` guard before `csrf.protect()` in public_declaration.py is needed, because `protect()` does not check the flag itself. It is commented.
