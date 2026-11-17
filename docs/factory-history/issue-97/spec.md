# Parallel-safe test suite with one test database per xdist worker

> Issue #97: Speed up the factory gate

## Summary
- Intake narrowed the issue to the part that can be built: making the pytest suite run safely in parallel with `pytest-xdist`, with each worker on its own test database.
- `factory/checks.sh` is a protected path, so this branch must not touch it. The PR gives the exact `checks.sh` change for the owner to apply by hand after merge.
- `tests/conftest.py` gives each xdist worker its own database (`<base>_<worker id>`, e.g. `plates_test_12_gw0`). Plain `pytest` behaves exactly as it does today.
- The builder audits the suite for shared state outside Postgres, adds `pytest-xdist` (pinned), updates the testing docs, and measures serial vs `-n auto` wall-clock time.

## Context
- **Gate today.** `factory/bin/gate` runs the main checkout's `factory/checks.sh` (ruff, mypy, serial `pytest -q -p no:cacheprovider`, shell and factory script checks). It sets `TEST_POSTGRES_DB=plates_test_<n>` and `TEST_REQUIRE_DB=1` (`factory/bin/gate`, around line 93). `factory/checks.sh` and `factory/config.json` are in `protected_paths`.
- **Test DB selection** is in `tests/conftest.py`, at module level, before any `from app...` import. It forces `DB_HOST=127.0.0.1` and `DB_PORT=5433`. It reads `_TEST_DB = TEST_POSTGRES_DB or "plates_test"`, checks that the name starts with `plates_test` and is alphanumeric once `_` is removed, and sets `os.environ["POSTGRES_DB"]` to it. `_REQUIRE_DB` comes from `TEST_REQUIRE_DB == "1"`.
- **Session fixture `_db_ready`** (`tests/conftest.py`). `_create_database_if_missing()` connects to the `postgres` maintenance DB and runs `CREATE DATABASE` when the name is missing. If `public.plates` is missing it applies `init.sql`, then always runs `schema_migrate.main()`. It returns False on `psycopg.Error` or when there is no `POSTGRES_PASSWORD`. `clean_db` turns False into a skip, or into a failure when `TEST_REQUIRE_DB=1`. Under xdist, session fixtures run once per worker process, so this already runs per worker.
- **Pool.** `app/core/db/__init__.py::_conninfo_kwargs` reads `POSTGRES_DB` from the environment when the pool is built lazily, so the per-worker name must be in `os.environ["POSTGRES_DB"]` before the first `db._get_pool()` call. Setting it at conftest import time does that. xdist exposes the worker id as the `PYTEST_XDIST_WORKER` env var (`gw0`, `gw1`, ...) in each worker process, and it is unset in a non-xdist run.
- **Known shared state that per-worker DBs fix.** `clean_db` truncates the app tables. `tests/test_schema_migrate.py` creates and drops fixed-name schemas (`_throwaway_schema`, `self.SCHEMA`), drops and recreates real tables, and calls `schema_migrate.main()`. Rate limiting (`app/web/antispam.py`) counts rows in the DB. All of these are safe once each worker has its own database.
- **Non-DB state seen so far.** Upload tests use `tmp_path` plus `monkeypatch.setenv("UPLOADS_DIR", ...)` (`tests/web/test_uploads.py`, `test_declarations.py`, `test_public_declaration.py`). `tests/web/conftest.py::client` mutates `flask_app.config` and restores it. Both are per-process and safe under xdist. The builder must still finish the audit (fixed temp paths, fixed ports, module-level state) across `tests/`.
- **Deps.** `requirements-dev.txt` pins `pytest==8.4.2`, `pytest-cov==7.1.0` and others. `pyproject.toml` `[tool.pytest.ini_options]` has `addopts = "-ra --strict-markers"`.
- **Docs.** `docs/LOCAL_DEVELOPMENT.md`, "Testing (`tests/conftest.py`)", says in Spanish that parallel pytest is not enabled and would need a DB per worker (the `clean_db` bullet), and documents `TEST_POSTGRES_DB`. The `clean_db` docstring in `tests/conftest.py` says the same. `AGENTS.md`, "Setup for a fresh checkout", explains `TEST_POSTGRES_DB` for worktrees.

## Requirements
1. Add `pytest-xdist` to `requirements-dev.txt`, pinned with `==` to a release that installs alongside `pytest==8.4.2`.
2. In `tests/conftest.py`, at module level where `_TEST_DB` is computed today, derive the database name like this:
   - Base name = `TEST_POSTGRES_DB` or `plates_test`, validated by the existing guard (prefix `plates_test`, alphanumeric once `_` is removed). An invalid base must still raise `RuntimeError` at import.
   - If `PYTEST_XDIST_WORKER` is set, the name is `f"{base}_{worker}"` (e.g. `plates_test_12_gw0`, `plates_test_gw3`). The final name must also pass the same guard. A worker id that would break it raises `RuntimeError`.
   - If `PYTEST_XDIST_WORKER` is unset, the name is exactly the base, as today.
   - The result goes in `os.environ["POSTGRES_DB"]`, as today.
   
   Put the derivation in a small pure helper, e.g. `_test_db_name(base: str, worker: str | None) -> str`, so it can be unit-tested.
3. Each worker creates its own database if it is missing and brings it to the current schema through the existing `_db_ready` path (`_create_database_if_missing`, then `init.sql` if needed, then `schema_migrate.main()`). Don't add a second setup path. Workers can create databases at the same moment, so `_create_database_if_missing` must tolerate another session creating the same database at the same time: treat `psycopg.errors.DuplicateDatabase` as success.
4. No code path may reach the real `plates` database. `DB_HOST`, `DB_PORT` and `POSTGRES_DB` are still forced, and every name produced starts with `plates_test`.
5. `TEST_REQUIRE_DB=1` still turns an unreachable or unready database into a test failure, never a skip, in every worker. The `clean_db` logic for this stays as it is.
6. Do not add `-n` (or any xdist option) to `addopts` in `pyproject.toml`. Plain `pytest` stays serial.
7. Finish the shared-state audit of `tests/`: temp files at fixed paths, fixed ports, module-level or app-level state that tests change without restoring, and order-dependent tests. Fix whatever breaks under `pytest -n auto`, in the tests or fixtures and not by skipping tests. Mark nothing as serial-only or skipped to get around a problem.
8. Add unit tests for the name helper in a new test module under `tests/` (e.g. `tests/test_conftest_db_name.py`). They must not need Postgres, and must cover:
   - no worker: the base is unchanged
   - with a worker: `<base>_<worker>`, with and without a custom `TEST_POSTGRES_DB`
   - an invalid base raises
   - a worker id that breaks the guard raises
9. Update the comments and docs that say parallel runs are unsupported:
   - the `clean_db` docstring in `tests/conftest.py`
   - the `clean_db` bullet in `docs/LOCAL_DEVELOPMENT.md`
   - the `TEST_POSTGRES_DB` bullet in `docs/LOCAL_DEVELOPMENT.md`: explain in Spanish that `pytest -n auto` gives each worker its own `<base>_gwN` database, created and migrated on first use and reused afterwards.

   In `AGENTS.md`, "Setup for a fresh checkout", add one English sentence: the suite can run in parallel with `pytest -n auto`, and each worker uses `<TEST_POSTGRES_DB>_gwN`.
10. Measure wall-clock time on the worktree against the local Postgres, with `TEST_POSTGRES_DB=plates_test_12` and `TEST_REQUIRE_DB=1`. Time a serial `python -m pytest -q -p no:cacheprovider` and a `python -m pytest -q -p no:cacheprovider -n auto` (after a warm-up run so the worker DBs exist). Record both times, the pass/skip counts of each, and the worker count in the build report, for the PR description.
11. Put the exact proposed `factory/checks.sh` diff in the build report for the PR description, without applying it. The diff:
    - changes the pytest line to `-n auto`
    - changes the dev-dependency check to `import ruff, mypy, pytest, xdist`
    - optionally runs ruff, mypy and the script checks concurrently. Each step then writes to its own output, its exit status is collected separately, and the outputs are printed in order under their existing `== <step>` headers. A failing step must still make the script exit non-zero and must stay identifiable in `checks.log`.

## Acceptance criteria
1. `requirements-dev.txt` contains a line `pytest-xdist==<version>`, and `.venv/Scripts/python -m pip install -r requirements-dev.txt` succeeds in the worktree.
2. `git diff origin/main --name-only` lists none of `factory/checks.sh`, `factory/config.json`, `factory/bin/gate` or `.github/workflows/test.yml`.
3. `grep -n "addopts" pyproject.toml` shows no `-n` and no other xdist option.
4. With Postgres up (`docker compose up -d db`) and `POSTGRES_PASSWORD` set, `TEST_POSTGRES_DB=plates_test_12 TEST_REQUIRE_DB=1 .venv/Scripts/python -m pytest -q -p no:cacheprovider -n auto` passes. Its passed count matches the serial run with the same env vars, and its skipped count is no higher.
5. After criterion 4, `SELECT datname FROM pg_database WHERE datname LIKE 'plates_test_12%'` on 127.0.0.1:5433 lists `plates_test_12_gw0`, `plates_test_12_gw1`, ... (one per worker). The serial run used `plates_test_12`.
6. Running criterion 4 a second time, with the worker databases already in place, also passes.
7. Running criterion 4's command with Postgres stopped, or with a wrong `POSTGRES_PASSWORD`, reports DB-dependent tests as failed, not skipped.
8. `TEST_POSTGRES_DB=plates .venv/Scripts/python -m pytest -q -n 2` errors at conftest import with the existing `RuntimeError` and runs no tests.
9. The new helper tests (requirement 8) pass with Postgres stopped and `POSTGRES_PASSWORD` unset: `.venv/Scripts/python -m pytest -q tests/test_conftest_db_name.py` (or whatever the builder names the module).
10. `docs/LOCAL_DEVELOPMENT.md` no longer says that parallel pytest is unsupported, and it describes the per-worker `<base>_gwN` databases in Spanish. `AGENTS.md` mentions `pytest -n auto` and the per-worker database name.
11. The build report has the serial and `-n auto` timings, counts and worker count, plus the proposed `factory/checks.sh` diff that meets requirement 11.

## Out of scope
- Any edit to `factory/checks.sh`, `factory/config.json`, `factory/bin/gate` or other gate machinery (protected or gate-owned). The `checks.sh` change is only proposed in the report and the PR.
- Changing `.github/workflows/test.yml` to run in parallel.
- Dropping, cleaning up or recreating per-worker test databases between runs. Reusing them is intended.
- Enabling xdist by default (`addopts`, `pytest.ini` or similar).
- Speeding up individual tests, removing tests or merging tests.
- Changing `app/` code. Fixes for parallel-safety belong in `tests/`. If the audit finds a real need to touch `app/`, keep the change minimal and explain it in the build report.
- Using per-worker Postgres schemas instead of per-worker databases.

## Assumptions
- Worker databases are named `<base>_<PYTEST_XDIST_WORKER>`, the form intake suggests (`plates_test_12_gw0`). It passes the existing guard without loosening it.
- A `DuplicateDatabase` race on `CREATE DATABASE` counts as success. Concurrent first runs could otherwise fail at random, and the outcome (the database exists) is the same.
- The timings and the proposed `checks.sh` diff go in the build report, which the factory turns into the PR description. The builder doesn't write the PR body itself.
- The ruff/mypy concurrency in the proposed diff is optional, as in intake. The required parts are `-n auto` and the `xdist` import check.
- The worker count is `-n auto` (one per CPU), as the issue suggests. Tuning it is left to the owner when applying the `checks.sh` change.
