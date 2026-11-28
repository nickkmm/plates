ESCALATE

## Summary
- Adds the back-end base for JWT authentication. A new `refresh_tokens` table (migration `0019` plus `init.sql`) stores only SHA-256 hashes. `app/shared/tokens.py` issues and checks 5-minute HS256 access tokens, and a refresh-token service rotates tokens under `FOR UPDATE` and revokes the whole family when a token is reused.
- No route, page or login flow uses the new code yet. I checked this: the `rg` for criterion 13 prints nothing.
- Round 1 gate: `GATE: PASS` (ruff, mypy, 1883 tests). The code and security reviews both say `VERDICT: PASS` with no findings.
- I'm escalating because the change touches auth, adds a migration, a dependency (PyJWT) and a required env var (`JWT_SECRET`). If `JWT_SECRET` is missing from production, the app won't start after the deploy.

## Why
The work matches the spec and stays inside intake's Scope. Every acceptance criterion is met, and the parts I checked myself hold up. Even so, the project rules say a person must check this before merging: it adds a migration that runs on deploy, a new required environment variable, a new runtime dependency, and code in auth and sessions.

## Acceptance criteria
1. The migration has exactly the columns, constraints and indexes the spec asks for: met. I read `0019_refresh_tokens.sql`: it uses `IF NOT EXISTS` and has the FK with `ON DELETE CASCADE`, a unique `token_hash` and both indexes. The schema tests pass in the gate.
2. `init.sql` has the table and the `0019` version row: met (build.md `rg`; the init-vs-migrations test passes).
3. `refresh_tokens` is in `TABLES` and `_APP_TABLES`: met (build.md `rg`; it is in the diff).
4. PyJWT is pinned exactly once: met. I checked: `PyJWT==2.15.1`, with a comment saying why it is a runtime dependency.
5. `pytest -k token` passes with none skipped: met (build.md reports 72 passed and none skipped against `plates_test_23`; the gate's full suite also passes).
6. Only hashes are written and compared: met. I read the repository: its SQL only uses `token_hash`, and the service passes `_hash(raw)`.
7. `FOR UPDATE` is on the lookup `rotate` uses: met. I checked `get_refresh_token_for_update`.
8. The app refuses to start without `JWT_SECRET`: met. I read the check in `app/web/app.py`, and build.md shows the `RuntimeError`.
9. The lifetimes are defined only in `app/shared/tokens.py`: met (build.md `rg`; the service imports `REFRESH_TOKEN_LIFETIME`).
10. `app/shared/tokens.py` is pure (no Flask, database or app imports): met (build.md `rg`; reviewers agree).
11. `JWT_SECRET` appears in all six files: met (build.md `rg`; it is in the diff).
12. The DATABASE.md and DOMAINS.md sections exist: met (build.md `rg`; it is in the diff).
13. No route, template or decorator uses the new code: met. I ran the `rg` myself and it printed nothing.

## For the human
Recommendation: don't merge yet

The work is sound and complete. The project rules say a person must check it before merging, for these reasons:

1. **New required env var `JWT_SECRET`.** Add it to the `ENV_FILE_B64` GitHub secret before you merge. Generate it with `python -c "import secrets; print(secrets.token_hex(32))"` and don't reuse `SECRET_KEY`. Without it, `app/web/app.py` raises at import and the production deploy leaves the app down. The PR body says this at the top.
2. **Migration `app/migrations/0019_refresh_tokens.sql`.** It runs on deploy. It is additive only: one new table and two indexes, idempotent, with no change to existing tables.
3. **New dependency `PyJWT==2.15.1`** in `requirements.txt`.
4. **Auth code.** Check the rotation and reuse logic in `app/auth/refresh_tokens.py` and the SQL in `app/auth/repository/refresh_tokens.py`. Check token decoding in `app/shared/tokens.py`: it accepts only HS256, requires all five claims, and checks expiry against the `now` it is given.

Things the reviewers flagged that don't block the merge, which you might add as follow-ups:
- Nothing enforces a minimum length for `JWT_SECRET`.
- An `iat` in the future is not rejected.
- `sub.isdigit()` would be tighter as `isdecimal()`.
- When the endpoints task lands, it should rate-limit login and refresh and call `revoke_user` when a user is deactivated or their password or role changes.

Files to look at: `app/web/app.py`, `app/migrations/0019_refresh_tokens.sql`, `init.sql`, `requirements.txt`, `.env.example`, `app/shared/tokens.py`, `app/auth/refresh_tokens.py`, `app/auth/repository/refresh_tokens.py`.
