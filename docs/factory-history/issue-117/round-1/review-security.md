VERDICT: PASS

## Summary
- The branch moves the declarations code (staff routes, public form, anti-spam, service, PDF, SQL) into `app/declarations/` and deletes `app/core/` and `app/web/blueprints/`. It does not change any behavior.
- I diffed each moved file against its original. Only import lines, the `db.` -> `declarations_repository.` call prefixes, docstrings and comments changed. No decorator, permission check, SQL string, upload limit or anti-spam rule changed.
- The public `/declaracion` route still has `@csrf.exempt`, `request.max_content_length = MAX_REQUEST_BYTES`, the honeypot, the signed token, the fail-closed Turnstile check and the per-IP limit.
- No secrets, `.env`, `data/` or `*.xlsx` files are committed, and no dependency is added.

## Findings
None.

## Notes
- `@csrf.exempt` in `app/declarations/public_routes.py:298`: Flask-WTF keys the exemption on the view's module and name. It computes that key from the same function object both when exempting and when checking a request, so the new module path doesn't change anything. The build's `tests/web/test_csrf.py` and `test_public_declaration.py` runs pass. The `url_map` dump (rule, endpoint, methods) is byte-identical to `origin/main`.
- The `get_conn` test patch now applies only on `app.shared.db`. Every app caller uses `from app.shared import db` and `db.get_conn()`, so tests still go through the patched connection. Production behavior is unchanged.
- Pre-existing, not made worse: `client_ip` trusts `X-Real-IP`, which is safe only while the app port stays bound to localhost behind nginx (documented in `app/declarations/antispam.py`).
