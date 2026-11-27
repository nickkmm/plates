STATUS: READY

# Build: Create app/shared with the pool, normalize, timezones, models and uploads

## Summary
- New `app/shared/` package: `db.py` (connection pool), `normalize.py`, `timezones.py`, `models.py`, `uploads.py`; its docstring states the "depends on no domain package" rule.
- The four modules moved with `git mv` (detected as renames); every import in `app/` and `tests/` points at `app.shared`.
- `app/core/db/__init__.py` no longer defines the pool and re-exports `get_conn` from `app.shared.db`; per-table re-exports and `__all__` unchanged.
- `clean_db` patches `get_conn` on both `app.core.db` and `app.shared.db`; a new test locks `app.core.db.get_conn is app.shared.db.get_conn`.
- Strict mypy now covers `app.shared.*`; docs and docstrings updated. Draft PR #191.

## Changes
- `app/shared/__init__.py` (new): package docstring with the dependency rule.
- `app/shared/db.py` (new): `_conninfo_kwargs`, `_pool`, `_get_pool`, `get_conn` moved verbatim from `app/core/db/__init__.py`, with the env-var, dict_row, transaction-contract, pooling and gunicorn `--preload` docstring parts (reference to `tests/core/db/test_connection.py::TestConninfoKwargs` kept).
- `app/core/db/__init__.py`: shorter docstring pointing to `app/shared/db.py`, submodule list kept; `from app.shared.db import get_conn`; pool code and its imports removed.
- `app/shared/{normalize,timezones,models,uploads}.py`: renamed from `app/core/` / `app/web/`; `normalize.py` imports `ExtractedRow` from `app.shared.models`; `uploads.send_stored_file` gets `-> Response` (typing only).
- Import-only updates in `app/core/{declaration_pdf,declarations,ingestion,pdf_extract}.py`, `app/core/db/*.py`, `app/migrate.py`, `app/schema_migrate.py`, `app/web/{app,helpers}.py`, `app/web/blueprints/{customers,declarations,ingest,production,public_declaration,records}.py` (isort order fixed by `ruff --select I --fix` on those files only). `public_declaration.py` splits `from app.core import db, normalize` into `from app.core import db` + `from app.shared import normalize`.
- "Same contract" docstrings in `app/core/db/*.py` and `app/core/{declarations,production,user_admin,cash_summary}.py` point to `app/shared/db.py`.
- `tests/conftest.py`: pool from `shared_db._get_pool()`; `monkeypatch.setattr` of `get_conn` on `db` and `shared_db`; docstring updated.
- `tests/core/db/test_connection.py`: tests `app.shared.db._conninfo_kwargs`; new `TestGetConnReExport`.
- Other tests: import and docstring paths only (no test moved, deleted or skipped).
- `pyproject.toml`: `module = ["app.core.*", "app.shared.*"]` with `disallow_untyped_defs = true`.
- Docs: `docs/{CORE,WEB,ARCHITECTURE,LOCAL_DEVELOPMENT,DATABASE,SIIGO_INTEGRATION,README}.md`, `README.md`, `CLAUDE.md` (new `app/shared/` bullet). SIIGO line refs (`:147-149`, `:224`) checked, still correct.

## Acceptance criteria
1. [x] `ls app/shared` -> `__init__.py db.py models.py normalize.py timezones.py uploads.py` (plus untracked `__pycache__`); `ls` of the four old paths -> "No such file or directory".
2. [x] The spec's `rg` over app tests docs README.md CLAUDE.md (excluding migrations, factory-history, MIGRATION_PLAN) -> no matches (exit 1).
3. [x] `rg "from app\.core import .*\b(normalize|timezones|models)\b|from app\.web import .*\buploads\b" app tests` -> no matches.
4. [x] `rg "^(from|import) app\.(auth|...|core|web)" app/shared` -> no matches.
5. [x] `def _conninfo_kwargs/_get_pool/get_conn` and `ConnectionPool(` match only `app/shared/db.py`; `app/core/db/__init__.py:152: from app.shared.db import get_conn`.
6. [x] `git diff --stat -M origin/main...HEAD` shows `app/{core => shared}/models.py`, `normalize.py`, `timezones.py`, `app/{web => shared}/uploads.py` as renames.
7. [x] `_get_pool` in tests only as `shared_db._get_pool()` (conftest), `_conninfo_kwargs` only via `db` = `app.shared.db` in test_connection.py; conftest lines 437-438 patch `db` (app.core.db) and `shared_db` (app.shared.db).
8. [x] `tests/core/db/test_connection.py::TestGetConnReExport::test_core_db_get_conn_is_shared_db_get_conn` passes (`pytest tests/core/db/test_connection.py`: 5 passed).
9. [x] `pyproject.toml:52 module = ["app.core.*", "app.shared.*"]` / `disallow_untyped_defs = true`; mypy passes on 52 files.
10. [x] `pytest --collect-only`: branch 1844 tests, `origin/main` (main checkout at 1b17fc5) 1843. `git diff --diff-filter=D --name-only -- tests` is empty.
11. [x] `git diff origin/main...HEAD --stat -- app/web/templates app/migrations init.sql` is empty; blueprint diff lines are all `from ... import` lines.
12. [x] `rg -c "app/shared" docs CLAUDE.md`: CORE.md 9, LOCAL_DEVELOPMENT.md 5, ARCHITECTURE.md 2, WEB.md 1, CLAUDE.md 1 (plus README, DATABASE, SIIGO).

## Validation
- `bash factory/checks.sh` with POSTGRES creds from main `.env`, `TEST_POSTGRES_DB=plates_test_17`, `TEST_REQUIRE_DB=1`: exit 0 (ruff clean, mypy clean, 1844 passed).
- `ruff check app/ tests/core/db/test_connection.py tests/conftest.py`: passed.
- `pytest --collect-only` on branch and main: 1844 vs 1843.
- All acceptance-criterion rg/git commands above: as listed.

## Log
### Round 1
- Ran the CLAUDE.md venv setup in the worktree.
- `git mv` of the four modules; regex rewrite of import paths and docstring paths in `app/` and `tests/` (migrations excluded); `ruff --select I --fix` on touched files only to keep import order.
- Split the pool into `app/shared/db.py`, re-exported from `app/core/db/__init__.py`; updated conftest and test_connection; added the re-export identity test.
- Extended the mypy override, annotated `send_stored_file`, updated docs.
- Commits: a7d0c04 Point the docs at app/shared; 35ec86f Move the pool, normalize, timezones, models and uploads into app/shared. Pushed; opened draft PR https://github.com/nickkmm/plates/pull/191 (body notes the `app/migrations/` exclusion and the two doc improvements). No PR attribution line, per CLAUDE.md.
