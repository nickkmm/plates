DECISION: ACCEPT
RISK: medium
SURFACES: none
RESCOPED: no

## Summary
- Pure refactor: create `app/shared/` and move the pool, normalize, timezones, models and uploads modules into it, with no behaviour change.
- The issue is precise (exact moves, rules, acceptance criteria), and all five source files exist where it says.
- About 23 app files and 44 test files need their imports or monkeypatch targets updated. That is wide but mechanical, so it fits one PR.
- One small exception to acceptance criterion 2: a SQL comment in an already-applied migration mentions `app.core.normalize`. Leave that comment alone.

## Scope
Build exactly the moves the issue lists, using `git mv` so history follows the files:
- `get_conn`, `_get_pool`, `_conninfo_kwargs` and the transaction-contract and pooling docstring move from `app/core/db/__init__.py` to `app/shared/db.py`. `app/core/db/__init__.py` keeps re-exporting every per-table function and imports `get_conn` from `app.shared.db`, so `db.get_conn` still works everywhere. Tests that monkeypatch the pool or `get_conn` must patch the object the code actually uses, so a test can't pass while patching a dead name.
- `app/core/normalize.py`, `timezones.py` and `models.py` move to `app/shared/`. `app/web/uploads.py` moves to `app/shared/uploads.py` (it already depends only on Flask and the standard library).
- New `app/shared/__init__.py` with a docstring that states the rule: shared code depends on no domain package.
- Update every import in `app/` and `tests/`. Extend the strict mypy override in `pyproject.toml` to the new paths. Update the paths in `docs/CORE.md`, `docs/WEB.md`, `docs/ARCHITECTURE.md` and the module docstrings.
- Exception to criterion 2: `app/migrations/0001_pos_fields.sql` line 8 is a SQL comment that names `app.core.normalize`. Migrations that have already been applied are never edited, so leave it. The PR description should say that the `rg` check excludes `app/migrations/` for this reason.
- No URL, endpoint, template, log line or behaviour changes. No test is removed or weakened, and the test count is the same as on main or higher.

Left out, as the issue says: moving blueprints or table modules, splitting normalize.py into domain constants.

## Why
The issue is a well-defined first slice of the migration plan's phase 1. The code matches its description: `app/core/db/__init__.py` holds the pool next to the per-table re-exports, `normalize.py` imports only `app.core.models`, and `app/web/uploads.py` imports only Flask. I rated the risk medium rather than low because the change touches shared code across about 70 files, and every push to `main` deploys. No schema, data or money logic changes, so the full test suite and mypy can verify it.
