VERDICT: PASS

## Summary
- The hard-coded role sets are replaced by a per-request permission check. `current_user()` reads the user and `role_permissions` from the DB on every request and caches them on `flask.g`.
- All 30 protected routes carry exactly one `@permission_required(code)` (and `/` carries `@authenticated`). Every decorator sits directly under its route decorator. `/login`, `/logout`, `/healthz` and `/declaracion` stay open, as before.
- Under the 0015 seed, each role keeps the same access. Precios, Clientes, Revisión, Inventario and record delete stay admin-only, and no write route accepts `records.view`.
- Security improves: a deactivated user loses access on their next request, and a role change applies on the next request, where before both waited for the session to expire.
- No new dependencies, secrets, raw SQL or logging of personal data were added.

## Findings
None.

## Notes
- `app/web/auth.py` `_check_session()` runs the stale-session check first, then the DB-backed active check, and clears the session on failure through `_redirect_to_login()`. `has_permission()` fails closed when there is no user or the user is inactive. `effective_permissions()` drops codes that aren't in the catalog. All of this is correct.
- The context processor (`app/web/app.py` `_inject_permissions`) calls `has_permission` on every rendered page, including `/login` and `/declaracion`. It only queries the DB when the session has a `user_id`, so the public pages make no new DB calls and expose no new data.
- `has_permission()` doesn't check `_session_is_stale()` (whether the session has `user` and `role`). It only affects UI gating on open pages, and the session cookie is signed, so this isn't exploitable.
- Pre-existing, not changed by this branch: `POST /login` logs `user=%s role=%s`. That is a staff username, not customer personal data, so it's acceptable.
- Every protected request now makes one extra DB round-trip. That's a performance point, not a security one. It also means a DB outage now fails closed (an error) and never fails open.
