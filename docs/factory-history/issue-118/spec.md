# Refresh tokens and JWT access tokens (back end only)

> Issue #118: Add refresh tokens and JWT access tokens (no endpoints yet)

## Summary
- Adds a `refresh_tokens` table (migration `0019`, `init.sql`, docs, schema tests) that stores only SHA-256 hashes of opaque refresh tokens.
- Adds `app/shared/tokens.py`, a pure, strictly typed module that issues and verifies 5-minute HS256 JWT access tokens with PyJWT and a dedicated `JWT_SECRET`.
- Adds a refresh-token service in `app/auth/` with its repository. It issues tokens, rotates them under a row lock, detects reuse (revoking the whole family) and revokes tokens.
- The app refuses to start without `JWT_SECRET`, the same way it does for `SECRET_KEY`. The VPS `.env` (GitHub secret `ENV_FILE_B64`) must get it before merge.
- Nothing calls this code yet: no endpoint, page or login flow changes.

## Context
- **Plan.** `docs/MIGRATION_PLAN.md`, "Decisions" (access token: JWT, 5 minutes, HS256, dedicated secret, claims `sub`/`username`/`perms`/`iat`/`exp`; refresh token: opaque, 30 days, stored hashed, rotated, reuse revokes the family) and "Phase 2: JWT authentication". This job builds only the token layer. The endpoints and the revocation hooks come in the next task.
- **Today's auth.** Login is session based: `app/auth/decorators.py` (session signed with `SECRET_KEY`), `app/auth/service.py`, and the SQL in `app/auth/repository/` (`users.py`, `roles.py`, each function re-exported explicitly in `app/auth/repository/__init__.py` with `__all__`). Effective permissions come from `app/auth/permissions.py::effective_permissions(roles, stored)` plus `repository.get_user_permissions`. Repository functions take an open `conn: psycopg.Connection[DictRow]` and never commit or roll back (contract in `app/shared/db.py`).
- **Startup secret check.** `app/web/app.py` (around line 104) reads `SECRET_KEY` at import time and raises `RuntimeError` with the generation command when it is missing. `tests/conftest.py` forces `os.environ["SECRET_KEY"]` before any `from app...` import (line 62).
- **Time.** `app/shared/timezones.py::utc_now()` returns naive UTC. Every `TIMESTAMP` column stores naive UTC.
- **Schema.** `init.sql` holds the full current schema plus the `INSERT INTO schema_migrations` version list, which ends at `0018_user_roles.sql`. Migrations live in `app/migrations/`. `0018_user_roles.sql` shows the idempotent style (header comment, `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`). `docs/DATABASE.md` has an ER diagram and one "### `<table>`" section per table.
- **Schema tests.** In `tests/test_schema_migrate.py`, `_APP_TABLES` (around line 104) lists the app tables. `test_migrated_old_schema_matches_fresh_init_sql` compares init.sql with old schema + all migrations. `test_fresh_init_sql_records_every_migration_and_applies_none` checks the version list. Per-migration "safe to apply twice" tests exist (for example `test_0017_permissions_sql_is_safe_to_apply_twice_directly`). `tests/conftest.py` `TABLES` (line 145) is truncated by `clean_db` before every test.
- **Tests layout.** Pure logic tests go in `tests/core/` (for example `tests/core/test_permissions.py`). DB repository tests go in `tests/core/db/` (for example `tests/core/db/test_users.py`, helpers in `tests/core/db/_helpers.py`).
- **Typing.** `pyproject.toml` `[[tool.mypy.overrides]]` sets `disallow_untyped_defs` for `app.shared.*`, `app.auth.service`, `app.auth.repository.*` and others.
- **Env docs.** `.env.example` (the `SECRET_KEY` block with its generation command), and the env var table in `docs/LOCAL_DEVELOPMENT.md` (around line 117). `docs/DOMAINS.md` documents `app/auth/` modules (sections "`app/auth/repository/users.py`", "`app/auth/permissions.py`", "`app/auth/repository/roles.py`").
- Nothing named `token` or `jwt` exists in `app/` today. `requirements.txt` has no JWT library.

## Requirements
1. **Migration.** New file `app/migrations/0019_refresh_tokens.sql`, with a header comment in the style of `0018`. It is idempotent (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`) and creates:
   `refresh_tokens(id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, token_hash TEXT NOT NULL UNIQUE, family_id UUID NOT NULL, expires_at TIMESTAMP NOT NULL, revoked_at TIMESTAMP, created_at TIMESTAMP NOT NULL DEFAULT now())`, with indexes `refresh_tokens_user_id_idx` on `(user_id)` and `refresh_tokens_family_id_idx` on `(family_id)`. No column ever holds the raw token. No existing migration is edited.
2. **init.sql.** It gets the same table and indexes (same end schema as the migration), and `('0019_refresh_tokens.sql')` is appended to the `INSERT INTO schema_migrations` list.
3. **Schema tests.** `"refresh_tokens"` is added to `TABLES` in `tests/conftest.py` and to `_APP_TABLES` in `tests/test_schema_migrate.py`. A new test `test_0019_refresh_tokens_sql_is_safe_to_apply_twice_directly` applies `0019` twice on the current schema without error. The existing init.sql-vs-migrations invariant test keeps passing.
4. **Dependency.** `PyJWT` is pinned to an exact current 2.x version in `requirements.txt`, with a one-line comment saying why it is a runtime dependency (the existing comments there show the style).
5. **`app/shared/tokens.py`** (pure: no Flask, no database; full type hints; covered by the `app.shared.*` mypy override):
   - Constants in this one module only: `ACCESS_TOKEN_LIFETIME = timedelta(minutes=5)` and `REFRESH_TOKEN_LIFETIME = timedelta(days=30)`. The refresh service imports `REFRESH_TOKEN_LIFETIME` from here and never redefines it.
   - `AccessClaims`: a frozen dataclass with `user_id: int`, `username: str`, `perms: list[str]` (or `tuple[str, ...]`), `issued_at: datetime` and `expires_at: datetime`.
   - Typed errors: a base `TokenError(Exception)` with the subclasses `ExpiredTokenError` and `InvalidTokenError`. The second covers a bad signature, a malformed token, a wrong algorithm, and a missing or ill-typed claim.
   - `issue_access_token(user_id: int, username: str, perms: Iterable[str], now: datetime) -> str`: HS256, claims `sub` = `str(user_id)`, `username`, `perms` = `sorted(set(perms))`, `iat` = `now`, `exp` = `now + ACCESS_TOKEN_LIFETIME`, both as integer Unix seconds.
   - `decode_access_token(token: str, now: datetime) -> AccessClaims`: verifies the signature with `algorithms=["HS256"]` only and requires all five claims. Expiry is checked against the `now` argument, not the wall clock: PyJWT's own `exp` check is turned off and `ExpiredTokenError` is raised when `now >= exp`. All PyJWT exceptions are translated into the typed errors above, so callers never see a `jwt.*` exception.
   - `now` is naive UTC, the same convention as `utc_now()`. An aware datetime is converted to UTC.
   - The secret comes from `os.environ["JWT_SECRET"]`, read at call time (not cached at import). When it is missing or empty, the functions raise `RuntimeError`. They never fall back to `SECRET_KEY`.
6. **Startup check.** `app/web/app.py` refuses to start without `JWT_SECRET`. Next to the `SECRET_KEY` check, a missing or empty `JWT_SECRET` raises `RuntimeError` with the generation command `python -c "import secrets; print(secrets.token_hex(32))"`.
7. **Test environment.** `tests/conftest.py` forces `os.environ["JWT_SECRET"]` to a fixed test value of at least 32 characters, different from the test `SECRET_KEY`, next to the `SECRET_KEY` line. The module docstring and comment mention it.
8. **Refresh-token repository.** New file `app/auth/repository/refresh_tokens.py`, strictly typed and in the `app.auth.repository.*` mypy override. It holds the SQL only and stores and compares only `token_hash`. It contains the functions the service needs, as a minimum: insert a row, select a row by hash `FOR UPDATE`, revoke a row, revoke all unrevoked rows of a family, and revoke all unrevoked rows of a user. Revoking sets `revoked_at` to the current naive-UTC time and leaves already revoked rows untouched. Each public function is re-exported in `app/auth/repository/__init__.py` and its `__all__`, and the module is listed in that package's docstring.
9. **Refresh-token service.** New file `app/auth/refresh_tokens.py`, added to the mypy `disallow_untyped_defs` override list in `pyproject.toml`. It takes an open `conn` and never commits, per the `app/shared/db.py` contract.
   - `issue(conn, user_id: int) -> str`: the raw value is `secrets.token_urlsafe(32)`. Only `hashlib.sha256(raw.encode()).hexdigest()` is stored, with a new `family_id` (`uuid.uuid4()`) and `expires_at = utc_now() + REFRESH_TOKEN_LIFETIME`. It returns the raw value.
   - `rotate(conn, raw: str) -> tuple[int, str] | None`: it looks the hash up with `SELECT ... FOR UPDATE`.
     - Unknown hash: `None`.
     - Already revoked: it revokes every token in that family and returns `None`. This is reuse detection.
     - Expired (`expires_at <= utc_now()`) and not revoked: `None`. The family is not revoked.
     - Otherwise: it revokes that row, inserts a new token in the **same** `family_id` with a fresh `expires_at = utc_now() + REFRESH_TOKEN_LIFETIME`, and returns `(user_id, new_raw)`.
   - `revoke(conn, raw: str) -> None`: revokes that one token. Unknown or already revoked is a no-op.
   - `revoke_user(conn, user_id: int) -> None`: revokes every token of the user.
   - The module docstring states that the row lock makes two concurrent `rotate` calls with the same token serialize. The second one then sees a revoked token and triggers reuse detection.
10. **Tests.** Every file name contains `token`, so `pytest -k token` selects them.
    - `tests/core/test_tokens.py` (pure): issue then decode round trip (`sub` is a string in the payload, `perms` sorted and deduplicated, `exp - iat` equals 5 minutes); expired (`now` at and past `exp` raises `ExpiredTokenError`; one second before is valid); a token signed with another secret raises `InvalidTokenError`; a tampered payload raises `InvalidTokenError`; a token signed with another algorithm (for example `none` or HS512) raises `InvalidTokenError`; a missing claim raises `InvalidTokenError`; a missing `JWT_SECRET` raises `RuntimeError` (via `monkeypatch.delenv`).
    - `tests/core/db/test_refresh_tokens.py` (uses the existing DB fixtures, with a seeded test user): `issue` stores one row whose `token_hash` is the SHA-256 hex of the returned value and no column holds the raw value; `expires_at` is about 30 days ahead; `rotate` returns `(user_id, new_raw)`, revokes the old row and the new row shares its `family_id`; rotating the old token again returns `None` and revokes every row of the family, including the newest; an expired token (`expires_at` set in the past with an `UPDATE`) returns `None` and does not revoke its family; an unknown token returns `None`; `revoke` makes `rotate` of that token detect reuse; `revoke_user` revokes all of a user's tokens across families and leaves another user's alone; two `issue` calls give different families.
11. **Docs.**
    - `docs/DATABASE.md`: a "### `refresh_tokens`" section (added in migration `0019`; column table with constraints; only the hash is stored; family and reuse meaning; `ON DELETE CASCADE`; both indexes), plus a `users ||--o{ refresh_tokens` line in the ER diagram.
    - `docs/DOMAINS.md`: short sections for `app/shared/tokens.py` (or a mention where `app/shared` is described), `app/auth/refresh_tokens.py` and `app/auth/repository/refresh_tokens.py`, stating that no route uses them yet.
    - `docs/LOCAL_DEVELOPMENT.md`: a `JWT_SECRET` row in the env var table (required, `RuntimeError` at startup, separate from `SECRET_KEY`, must be added to `ENV_FILE_B64` in production), and the note that `conftest.py` forces it.
    - `.env.example`: a `JWT_SECRET=` entry under the web authentication block, with a comment saying it is required, dedicated (not `SECRET_KEY`), and how to generate it.
    - `README.md` line 141: the comment mentions `JWT_SECRET` next to `SECRET_KEY`.
12. **PR description.** It prominently states that `JWT_SECRET` must be added to the `ENV_FILE_B64` GitHub secret before merging, or the deploy's app will not start. It also lists any improvements over the issue, with reasons, or says there were none.

## Acceptance criteria
1. `app/migrations/0019_refresh_tokens.sql` exists and creates `refresh_tokens` with exactly the columns, constraints and two indexes in Requirement 1. Verify: read the file. `.venv/Scripts/python -m pytest -q tests/test_schema_migrate.py` passes, including `test_migrated_old_schema_matches_fresh_init_sql`, `test_fresh_init_sql_records_every_migration_and_applies_none` and the new `test_0019_refresh_tokens_sql_is_safe_to_apply_twice_directly`.
2. `init.sql` contains `CREATE TABLE refresh_tokens` and `'0019_refresh_tokens.sql'` in the `schema_migrations` insert. Verify: `rg -n "refresh_tokens" init.sql`.
3. `rg -n "refresh_tokens" tests/conftest.py tests/test_schema_migrate.py` shows the table in `TABLES` and `_APP_TABLES`.
4. `rg -n "^PyJWT==" requirements.txt` prints exactly one pinned line.
5. With the test DB up (`TEST_POSTGRES_DB=plates_test_23`), `.venv/Scripts/python -m pytest -q tests -k token` runs the tests of Requirement 10 (none skipped) and they pass.
6. `rg -n "token_hash" app` shows only hashed values written and compared, and `rg -n "raw" app/auth/repository/refresh_tokens.py` shows no raw value written to SQL. Verify by reading the repository module.
7. `rg -n "FOR UPDATE" app/auth/repository/refresh_tokens.py` matches the lookup used by `rotate`.
8. Starting the app without `JWT_SECRET` fails. Verify: `SECRET_KEY=x .venv/Scripts/python -c "import app.web.app"` with `JWT_SECRET` unset raises `RuntimeError` whose message mentions `JWT_SECRET`. With `JWT_SECRET=y` added, that error is gone.
9. `rg -n "ACCESS_TOKEN_LIFETIME|REFRESH_TOKEN_LIFETIME|timedelta\(minutes=5|timedelta\(days=30" app` shows the two lifetimes defined only in `app/shared/tokens.py`.
10. `rg -n "flask|psycopg|app\.web|app\.auth" app/shared/tokens.py` prints nothing (the module is pure).
11. `rg -n "JWT_SECRET" .env.example docs/LOCAL_DEVELOPMENT.md README.md tests/conftest.py app/web/app.py app/shared/tokens.py` matches in each file.
12. `docs/DATABASE.md` has a `### \`refresh_tokens\`` section and an ER line for it. `docs/DOMAINS.md` mentions `app/auth/refresh_tokens.py` and `app/shared/tokens.py`. Verify: `rg -n "refresh_tokens|shared/tokens" docs/DATABASE.md docs/DOMAINS.md`.
13. No route, template or decorator uses the new code. Verify: `rg -n "refresh_tokens|shared.tokens|shared import tokens" app/web app/auth/routes.py app/auth/admin_routes.py app/auth/decorators.py` prints nothing.

## Out of scope
- Login, refresh, logout and `/api/auth/me` endpoints, and any `/api` blueprint.
- Token cookies, the `X-Requested-With` CSRF rule, or any change to how pages authenticate (the Flask session stays as is).
- Revoking refresh tokens on user deactivation, role change or password change: `revoke_user` exists, but nothing calls it yet.
- A job or command that purges expired or revoked rows.
- Key rotation, multiple secrets, `kid` headers, or asymmetric algorithms.
- Adding `flask-openapi3`, Pydantic schemas or anything from later migration phases.
- Editing any existing migration file, `factory/checks.sh` or `factory/config.json`.

## Assumptions
- The repository module is `app/auth/repository/refresh_tokens.py`, re-exported from the package. Reason: it matches the existing one-module-per-table layout of `app/auth/repository/`.
- The lifetimes live in `app/shared/tokens.py` and the refresh service imports `REFRESH_TOKEN_LIFETIME` from there. Reason: the issue asks for both "in one place".
- `issue`, `rotate`, `revoke` and `revoke_user` keep the issue's signatures without a `now` argument and use `utc_now()`. Expiry tests age a row with `UPDATE`. Reason: these are the exact signatures the next task relies on.
- An expired but unrevoked token returns `None` without revoking its family. Reason: expiry is normal, not a sign of theft. Only reuse of a revoked token is.
- `JWT_SECRET` is read at call time in `tokens.py`, and `app/web/app.py` enforces it at startup. Reason: this keeps the module importable in pure tests while meeting "refuses to start like `SECRET_KEY`".
- Expiry is checked against the explicit `now` (PyJWT's own exp check off), and `now >= exp` counts as expired. Reason: the issue requires `now` as a parameter, and PyJWT cannot take an injected clock.
- Concurrency is covered by `FOR UPDATE` plus the sequential reuse test, not by a multi-connection race test. Reason: the second caller then sees a revoked row and takes the reuse path, and a threaded DB test would be flaky in the gate.
