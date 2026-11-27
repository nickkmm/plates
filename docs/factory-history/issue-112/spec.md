# Create app/shared with the pool, normalize, timezones, models and uploads

> Issue #112: Create app/shared with the connection pool, normalize, timezones, models and uploads

## Summary
- Pure refactor, the first phase 1 step of `docs/MIGRATION_PLAN.md`: a new `app/shared/` package holds the connection pool, `normalize`, `timezones`, `models` and `uploads`.
- Files move with `git mv`. Every import in `app/` and `tests/` is updated. `app.core.db` keeps re-exporting `get_conn` and every per-table function.
- The test fixture that swaps `get_conn` for a savepoint connection must patch the name the code actually calls, so no test passes against a dead name.
- The strict mypy override extends to `app.shared.*`. The docs and docstrings that name the old paths are updated.
- No URL, endpoint, template, log line or behaviour changes. The one SQL comment in an applied migration stays as it is.

## Context
- **Pool**: `app/core/db/__init__.py` has a long module docstring (row factory, transaction contract, connection pooling, the gunicorn `--preload` note, the submodule list), the `_conninfo_kwargs()`, `_pool`, `_get_pool()` and `get_conn()` code, and explicit re-exports of every per-table submodule (`customers`, `declarations`, `inventory`, `orders`, `ownership_cards`, `plates`, `pricing`, `production`, `roles`, `users`), with `"get_conn"` in `__all__`. All app code calls `db.get_conn()` through `from app.core import db`.
- **Tests that touch the pool**:
  - `tests/conftest.py` `clean_db` calls `db._get_pool()` at about line 345, then `monkeypatch.setattr(db, "get_conn", _shared_get_conn)` at about line 435, where `db` is `app.core.db`. Its docstring cites `app/core/db/__init__.py::get_conn`.
  - `tests/core/db/test_connection.py` calls `db._conninfo_kwargs()`, and its docstring cites `app/core/db/__init__.py`.
- **Modules to move**:
  - `app/core/normalize.py` imports only `app.core.models`. `app/core/models.py` imports only pydantic, and `app/core/timezones.py` only the stdlib.
  - `app/web/uploads.py` imports only Flask and the stdlib. Its `send_stored_file` (line 121) has no return annotation.
- **Importers** (`rg -l "app\.core\.(normalize|timezones|models)|app\.web\.uploads|from app\.core import .*(normalize|timezones|models)|from app\.web import .*uploads" app tests`):
  - In `app/`: `app/core/db/{customers,inventory,orders,ownership_cards,plates,pricing,production}.py`, `app/core/{declaration_pdf,declarations,ingestion,normalize,pdf_extract}.py`, `app/migrate.py`, `app/schema_migrate.py` (an import plus a docstring line 35), `app/web/app.py`, `app/web/helpers.py`, and `app/web/blueprints/{customers,declarations,ingest,production,public_declaration,records}.py`.
  - In `tests/`: `tests/conftest.py`, `tests/core/db/{_helpers,test_declarations,test_inventory,test_orders,test_plates,test_pricing}.py`, `tests/core/test_{cash_summary,ingestion,models,normalize,production,timezones}.py`, `tests/test_schema_migrate.py`, and `tests/web/{_helpers,test_cash_summary,test_declaration_template,test_declarations,test_ingest_pdf,test_pricing,test_records_search_pdf,test_uploads}.py`.
  - Also `app/migrations/0001_pos_fields.sql` line 8, a SQL comment in an applied migration.
- **mypy**: `pyproject.toml` has `[[tool.mypy.overrides]] module = "app.core.*"` with `disallow_untyped_defs = true`.
- **Docs that name the old paths**:
  - `docs/ARCHITECTURE.md` lines 18 and 21 (mermaid labels).
  - `docs/CORE.md`: the `models.py`, `normalize.py` and `timezones.py` sections, the pipeline diagram at lines 13-14, and the `db/__init__.py` row at line 289 plus the pool paragraph after it.
  - `docs/WEB.md` (uploads and helper references).
  - `docs/LOCAL_DEVELOPMENT.md` lines 116 (`DB_POOL_*` → `app/core/db/__init__.py`) and 122 (`UPLOADS_DIR` → `app/web/uploads.py`).
  - `docs/DATABASE.md` line 231, and `docs/SIIGO_INTEGRATION.md` lines 334 and 531.
  - `docs/README.md` line 23 (CORE.md row), `CLAUDE.md` "Project" bullets, and `README.md` around lines 83-107.
  - The submodule docstrings in `app/core/db/*.py`, `app/core/declarations.py`, `app/core/production.py`, `app/core/user_admin.py` and `app/core/cash_summary.py` say "See app/core/db/__init__.py for the module-wide connection/transaction contract".
- `docs/MIGRATION_PLAN.md` (Target layout, line 139 "Where today's code goes", Phase 1) is the plan this implements.

## Requirements
1. Create `app/shared/__init__.py` with a module docstring that states the rule: code in `app/shared/` depends on no domain package (`app.auth`, `app.customers`, `app.pricing`, `app.inventory`, `app.production`, `app.orders`, `app.cash_summary`, `app.declarations`, and today's `app.core` and `app.web` packages, which hold domain code). It may depend only on the stdlib, third-party libraries and other `app.shared` modules.
2. `git mv` these files so history follows them, and change only their imports and docstrings:
   - `app/core/normalize.py` → `app/shared/normalize.py`
   - `app/core/timezones.py` → `app/shared/timezones.py`
   - `app/core/models.py` → `app/shared/models.py`
   - `app/web/uploads.py` → `app/shared/uploads.py`

   `app/shared/normalize.py` imports `ExtractedRow` from `app.shared.models`.
3. Create `app/shared/db.py` holding `_conninfo_kwargs`, `_pool`, `_get_pool`, `get_conn` (moved as written, no behaviour change) and the docstring parts about the env vars, `dict_row`, the transaction contract, pooling and gunicorn `--preload`. Update the `tests/core/db/test_connection.py` reference in those docstrings so it stays correct.
4. `app/core/db/__init__.py` keeps its explicit per-table re-exports and `__all__` unchanged. It gets `get_conn` with `from app.shared.db import get_conn`, so `db.get_conn` still works. Its docstring keeps the submodule list and points to `app/shared/db.py` for the connection and transaction contract. It no longer defines the pool. App call sites keep calling `db.get_conn()` through `app.core.db` as they do today.
5. Point every import and reference in `app/` and `tests/` (code, docstrings and comments) at the new paths. The only exception is `app/migrations/0001_pos_fields.sql`: that migration is applied, so it is never edited.
6. Tests that reach the pool use the real module:
   - `tests/conftest.py` `clean_db` gets the pool from `app.shared.db._get_pool()`.
   - It installs `_shared_get_conn` on both `app.core.db.get_conn` and `app.shared.db.get_conn` with `monkeypatch.setattr`. Whichever name a caller uses then gets the savepoint connection, and no real commit can reach the test database.
   - `tests/core/db/test_connection.py` tests `app.shared.db._conninfo_kwargs`.
   - Update the docstrings of both files to the new paths.
7. Add a test, in `tests/core/db/test_connection.py` or a new `tests/shared/` module with an `__init__.py`, that checks `app.core.db.get_conn is app.shared.db.get_conn` when nothing is patched. This locks the re-export contract in place.
8. Test files keep their current locations, except for an optional `git mv` of `tests/core/test_{normalize,timezones,models}.py` / `tests/web/test_uploads.py` into `tests/shared/`. If they move, use `git mv` and change only imports and docstrings. No test is deleted, skipped or weakened, and the collected test count is at least main's.
9. In `pyproject.toml`, extend the strict override to `module = ["app.core.*", "app.shared.*"]` (or an equivalent second block). `app/shared/uploads.py` must pass it, so give `send_stored_file` a return annotation (for example the Flask/werkzeug `Response` type). That is a typing-only change.
10. Update the docs:
    - `docs/CORE.md`: the `models.py`, `normalize.py` and `timezones.py` sections and the diagram name `app/shared/`, either moved under a new short "`app/shared/`" section or with the path stated. The `db/__init__.py` row and the pool paragraph say the pool lives in `app/shared/db.py` and is re-exported.
    - `docs/WEB.md`: the uploads references point to `app/shared/uploads.py`.
    - `docs/ARCHITECTURE.md`: the mermaid labels on lines 18 and 21.
    - `docs/LOCAL_DEVELOPMENT.md` lines 116 and 122, `docs/DATABASE.md` line 231, and `docs/SIIGO_INTEGRATION.md` lines 334 and 531 (path only; check the line numbers it cites).
    - `docs/README.md` CORE.md row, `README.md` layout paragraph, and the `CLAUDE.md` "Project" bullets: add a one-line `app/shared/` bullet ("code every domain needs; depends on no domain package").
    - The "See app/core/db/__init__.py for the ... contract" lines in `app/core/db/*.py`, `app/core/{declarations,production,user_admin,cash_summary}.py` point to `app/shared/db.py`.
11. Nothing else changes: no URL, blueprint name, endpoint, template, log line, SQL or runtime behaviour. Templates and `app/web/{helpers,navigation,csrf}.py` stay where they are.
12. The PR description says that criterion 2's `rg` excludes `app/migrations/` (an applied migration's SQL comment), and lists any improvements made with their reasons, or says there were none.

## Acceptance criteria
1. `ls app/shared` shows `__init__.py db.py normalize.py timezones.py models.py uploads.py`. `app/core/normalize.py`, `app/core/timezones.py`, `app/core/models.py` and `app/web/uploads.py` do not exist.
2. `rg -n "app\.core\.(normalize|timezones|models)|app\.web\.uploads|app/core/(normalize|timezones|models)\.py|app/web/uploads\.py" app tests docs README.md CLAUDE.md --glob '!app/migrations/**' --glob '!docs/factory-history/**' --glob '!docs/MIGRATION_PLAN.md'` finds nothing.
3. `rg -n "from app\.core import .*\b(normalize|timezones|models)\b|from app\.web import .*\buploads\b" app tests` finds nothing.
4. `rg -n "^(from|import) app\.(auth|customers|pricing|inventory|production|orders|cash_summary|declarations|core|web)" app/shared` finds nothing.
5. `rg -n "def (_conninfo_kwargs|_get_pool|get_conn)|ConnectionPool\(" app` matches only `app/shared/db.py`. `rg -n "from app.shared.db import get_conn" app/core/db/__init__.py` matches.
6. `git diff --stat -M origin/main...HEAD` (or `git log --follow app/shared/normalize.py`) shows the four moved files as renames, not as delete plus add.
7. `rg -n "_get_pool|_conninfo_kwargs" tests` shows them reached only through `app.shared.db`. `rg -n "setattr\(.*get_conn" tests/conftest.py` shows the patch on both `app.core.db` and `app.shared.db`.
8. A test asserts `app.core.db.get_conn is app.shared.db.get_conn`, and it passes.
9. `pyproject.toml` has a `[[tool.mypy.overrides]]` with `disallow_untyped_defs = true` that covers `app.shared.*`.
10. `.venv/Scripts/python -m pytest -q --collect-only | tail -1` reports at least as many tests as on `origin/main`. `git diff origin/main...HEAD --stat -- tests` deletes no test file except by rename.
11. `git diff origin/main...HEAD -- app/web/templates app/migrations init.sql` is empty. `git diff origin/main...HEAD -- app/web/blueprints` changes only import lines.
12. `docs/CORE.md`, `docs/WEB.md`, `docs/ARCHITECTURE.md`, `docs/LOCAL_DEVELOPMENT.md` and `CLAUDE.md` mention `app/shared/` where they named the old paths (`rg -n "app/shared" docs CLAUDE.md`).

## Out of scope
- Moving any blueprint, any `app/core/db/*` table module or any other `app/core` module (later phase 1 tasks).
- Removing the per-table re-exports from `app/core/db/__init__.py`, or switching app call sites from `db.get_conn` to `app.shared.db.get_conn`.
- Splitting `normalize.py` into domain constants, or moving customer- or plate-specific constants.
- Moving templates or `app/web/{helpers,navigation,csrf}.py`. Adding `shared/config`, JWT, a permission decorator or `shared/storage.py` (later phases).
- Editing `app/migrations/0001_pos_fields.sql` or any applied migration.
- Rewriting `docs/factory-history/` or the plan in `docs/MIGRATION_PLAN.md`.
- Any behaviour, URL, log or schema change, and any refactor of the moved code beyond imports, docstrings and the one return annotation.

## Assumptions
- Today's `app.core` and `app.web` count as domain-side packages for the "shared depends on no domain" rule: the moved modules don't import them, and this keeps `shared` clean as domains move out of them.
- `conftest.py` patches `get_conn` on both modules rather than switching call sites: this keeps call sites unchanged and still makes a later direct `app.shared.db.get_conn` caller safe in tests.
- Docs that name the old paths outside the three the issue lists (LOCAL_DEVELOPMENT, DATABASE, SIIGO_INTEGRATION, README, CLAUDE.md) are updated too: a path the code no longer has is a stale doc.
- `app/shared/uploads.py` is held to the strict mypy bar because a module-level override for `app.shared.*` is simpler than an exception. That only means adding one return annotation.
- Moving the matching test files into `tests/shared/` is optional: the issue only requires import-path changes, and either layout keeps the test count.
