STATUS: READY

# Build: Refresh tokens and JWT access tokens (back end only)

## Summary
- Adds the `refresh_tokens` table (migration `0019` + `init.sql`), storing only SHA-256 hashes.
- Adds `app/shared/tokens.py`: 5-minute HS256 JWT access tokens with typed errors, keyed by a dedicated `JWT_SECRET`.
- Adds the refresh-token service (`issue`, `rotate` with `FOR UPDATE` and family reuse detection, `revoke`, `revoke_user`) and its repository.
- The app refuses to start without `JWT_SECRET`: it must be added to `ENV_FILE_B64` before merge (stated at the top of PR #197).
- Nothing calls the new code yet; draft PR https://github.com/nickkmm/plates/pull/197.

## Changes
- `app/migrations/0019_refresh_tokens.sql`: new idempotent migration creating `refresh_tokens` and its two indexes.
- `init.sql`: same table and indexes after `user_roles`; `('0019_refresh_tokens.sql')` appended to the `schema_migrations` insert.
- `requirements.txt`: `PyJWT==2.15.1` with a runtime-dependency comment.
- `app/shared/tokens.py`: lifetimes, `AccessClaims`, `TokenError`/`ExpiredTokenError`/`InvalidTokenError`, `issue_access_token`, `decode_access_token` (HS256 only, all five claims required and type-checked, expiry against `now`, PyJWT errors translated, `JWT_SECRET` read per call, `RuntimeError` when missing/empty).
- `app/web/app.py`: `JWT_SECRET` startup check next to `SECRET_KEY`.
- `app/auth/repository/refresh_tokens.py`: `RefreshTokenRecord`, `insert_refresh_token`, `get_refresh_token_for_update`, `revoke_refresh_token`, `revoke_refresh_token_family`, `revoke_user_refresh_tokens` (hash-only SQL; revoking skips already revoked rows).
- `app/auth/repository/__init__.py`: docstring lists the module; functions re-exported and in `__all__`.
- `app/auth/refresh_tokens.py`: the service; docstring explains the row-lock serialization.
- `pyproject.toml`: `app.auth.refresh_tokens` added to the strict mypy override.
- `tests/conftest.py`: forces `JWT_SECRET` (72 chars, differs from `SECRET_KEY`), docstring updated, `refresh_tokens` in `TABLES`.
- `tests/test_schema_migrate.py`: `refresh_tokens` in `_APP_TABLES`; new `TestRefreshTokens.test_0019_refresh_tokens_sql_is_safe_to_apply_twice_directly`.
- `tests/core/test_tokens.py`, `tests/core/db/test_refresh_tokens.py`: new tests per Requirement 10.
- `docs/DATABASE.md` (ER line + entity, `### refresh_tokens` section), `docs/DOMAINS.md` (sections for the three modules), `docs/LOCAL_DEVELOPMENT.md` (env row + conftest note), `.env.example` (`JWT_SECRET=` block), `README.md` (setup comment).

## Acceptance criteria
1. [x] Migration with exact columns/constraints/indexes: file read; `pytest tests -k token` (includes the new 0019 test) and the full suite (which includes `test_migrated_old_schema_matches_fresh_init_sql` and `test_fresh_init_sql_records_every_migration_and_applies_none`) pass.
2. [x] `rg -n "refresh_tokens" init.sql`: `CREATE TABLE refresh_tokens` (l.213), both indexes, `('0019_refresh_tokens.sql')` (l.351).
3. [x] `rg` shows `refresh_tokens` in `TABLES` (conftest.py:151) and `_APP_TABLES` (test_schema_migrate.py:108).
4. [x] `rg -n "^PyJWT==" requirements.txt` -> `19:PyJWT==2.15.1` (one line).
5. [x] `TEST_POSTGRES_DB=plates_test_23 TEST_REQUIRE_DB=1 .venv/Scripts/python -m pytest -q tests -k token -rs` -> 72 passed, none skipped.
6. [x] `rg -n "token_hash" app`: only `_hash(raw)` is passed as `token_hash`; SQL writes/compares only `token_hash`. `rg -n "raw" app/auth/repository/refresh_tokens.py` -> no match.
7. [x] `rg -n "FOR UPDATE"` -> line 68, in `get_refresh_token_for_update`, the lookup `rotate` uses.
8. [x] `env -u JWT_SECRET SECRET_KEY=x .venv/Scripts/python -c "import app.web.app"` -> `RuntimeError: JWT_SECRET env var is required. Generate one with: python -c "import secrets; print(secrets.token_hex(32))"`; with `JWT_SECRET=y` -> imports fine.
9. [x] The `rg` for the lifetimes shows definitions only at app/shared/tokens.py:27-28; app/auth/refresh_tokens.py imports `REFRESH_TOKEN_LIFETIME`.
10. [x] `rg -n "flask|psycopg|app\.web|app\.auth" app/shared/tokens.py` -> no output.
11. [x] `rg -c "JWT_SECRET"` matches in all six files.
12. [x] `rg -n "refresh_tokens|shared/tokens" docs/DATABASE.md docs/DOMAINS.md`: `### refresh_tokens` section, ER line, DOMAINS sections for both modules.
13. [x] `rg -n "refresh_tokens|shared.tokens|shared import tokens" app/web app/auth/routes.py app/auth/admin_routes.py app/auth/decorators.py` -> no output (exit 1).

## Validation
- `bash factory/checks.sh` with the main checkout's DB credentials, `TEST_POSTGRES_DB=plates_test_23`, `TEST_REQUIRE_DB=1`: exit 0 (ruff, mypy 64 files clean, 1883 passed).
- `ruff check` on the two new test files: passed.
- No page criteria (back end only), so no dev server or browser.

## Log
### Round 1
- Set up `.venv` in the worktree and installed requirements-dev (PyJWT 2.15.1 included).
- Built migration, init.sql, tokens module, startup check, repository, service, tests and docs per the spec, in four commits; pushed and opened draft PR #197 against `main` (the prompt's base read `origin/undefined`; used `base_branch: main` from factory/config.json).
- Kept the `app/web/app.py` comment free of the module path so criterion 13's `rg` prints nothing.
- Made the test `JWT_SECRET` 64+ characters to silence PyJWT's short-HMAC-key warning in the HS512 test.
