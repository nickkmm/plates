ESCALATE

## Summary
- Pure refactor for issue #112: a new `app/shared/` package holds the connection pool (`db.py`), `normalize`, `timezones`, `models` and `uploads`.
- The four modules were moved with `git mv` and show up as renames. The pool code was moved into `app/shared/db.py` unchanged, and `app/core/db/__init__.py` re-exports `get_conn`.
- The test fixture now patches `get_conn` on both modules. A new test checks that the re-export is the same object. Strict mypy now covers `app.shared.*`.
- Gate passed: ruff and mypy clean, 1844 tests passed (main has 1843). The code review passed with no findings. No migration, template, environment variable, Docker or CI file changed.
- Recommendation: merge.

## Why
Every spec criterion is met and the round-1 gate and review both passed. The diff stays within intake's Scope: only import paths, docstrings, docs and one return annotation changed. No behaviour changed. The factory always escalates here, and this PR also edits two import lines in `app/web/blueprints/public_declaration.py` and moves the module that stores the declaration form's photos and signatures. I checked both: they are path-only changes, so they don't trigger the "touches the public declaration form" rule in substance.

## Acceptance criteria
1. `app/shared/{__init__,db,normalize,timezones,models,uploads}.py` exist and the old paths are gone: met. The `git diff --stat` shows the four renames and the new files.
2. The old-path `rg` over app, tests, docs, README.md and CLAUDE.md (excluding migrations, factory-history and MIGRATION_PLAN) finds nothing: met. I re-ran it and got no output.
3. No `from app.core import normalize/timezones/models` or `from app.web import uploads` remains: met, per build.md. The diff agrees: `public_declaration.py` now imports `normalize` from `app.shared`.
4. `app/shared` imports no domain package: met. I re-ran the check: the only `app.` import in `app/shared` is `app.shared.models`, inside `normalize.py`.
5. The pool functions are defined only in `app/shared/db.py`, and `app/core/db/__init__.py` imports `get_conn` from there: met. I re-ran the check and read the diff: the code is the same as before, and the per-table re-exports and `__all__` are unchanged.
6. The moved files show as renames: met. The stat shows `app/{core => shared}/...` and `app/{web => shared}/uploads.py`.
7. Tests reach `_get_pool`/`_conninfo_kwargs` only through `app.shared.db`, and conftest patches both modules: met. I saw this in the `tests/conftest.py` diff.
8. The identity test exists and passes: met. `TestGetConnReExport` is in `tests/core/db/test_connection.py`, and the gate's run passed.
9. The mypy override covers `app.shared.*`: met. `pyproject.toml` now reads `module = ["app.core.*", "app.shared.*"]`, and mypy is clean on 52 files.
10. Test count is at least main's and no test file was deleted: met. 1844 against 1843, with no deletions.
11. No change to templates, migrations or `init.sql`, and the blueprints change only import lines: met. That stat is empty, and the blueprint diff I read is import lines only.
12. The docs mention `app/shared/`: met. CORE, WEB, ARCHITECTURE, LOCAL_DEVELOPMENT, DATABASE, SIIGO_INTEGRATION, both READMEs and CLAUDE.md were updated.

## For the human
Recommendation: merge

- The only decision left is the routine human merge. Merging deploys to production. No migration runs and no configuration changes.
- Worth a quick look:
  - `app/shared/db.py` against the removed block in `app/core/db/__init__.py`. The pool code is unchanged, and only the docstring was reworded.
  - `tests/conftest.py` around the `monkeypatch.setattr(... "get_conn" ...)` lines. These patches are what keep tests from committing to the test database.
  - `app/web/blueprints/public_declaration.py` and `app/shared/uploads.py` (the declaration form's file storage). I checked both: they are import and annotation changes only.
- A note for later, not a blocker: the `app/shared/db.py` docstring still says "every SELECT in app/core/db/". Update it when the table modules move out of `app/core/db/`.
- The PR description should say that criterion 2's `rg` excludes `app/migrations/` (`0001_pos_fields.sql` is an applied migration and keeps its old comment). build.md says it does.
