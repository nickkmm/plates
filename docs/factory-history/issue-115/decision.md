ESCALATE

## Summary
- Issue #115 (phase 1 of docs/MIGRATION_PLAN.md) is a file move only. Production now lives in `app/production/` (routes, service, repository) and cash summary in `app/cash_summary/` (routes, service). All five files were moved with `git mv` and git shows them as renames.
- `app/core/db` no longer re-exports the 14 production names. Callers and the two monkeypatches now go through the repository module. Docs, docstrings, `CLAUDE.md` and the strict mypy list point at the new paths.
- The gate passed in round 1: ruff, mypy, and 1844 tests, the same count as `origin/main`. The code review is `VERDICT: PASS` with no findings.
- I escalate because this project always escalates. Also, the change moves the cuadre (cash-reconciliation) code and the files that hold the production and cuadre route permissions, even though none of that logic changes.

## Why
The diff changes only imports, call prefixes, docstrings, one `TODO(phase 4)` comment and the mypy override list. No SQL, permission decorator, template, migration, URL or endpoint changes, and the work stays inside intake's Scope. Every acceptance criterion is met. The only reason not to approve outright is the project's rule that changes touching the cuadre and route-permission files get a human look.

## Acceptance criteria
1. Files moved, old paths gone: met. The diff shows the 5 renames and the 2 new `__init__.py` files.
2. No old module paths (rg): met. I ran the rg myself and it found nothing (exit 1).
3. No old file paths (rg with the listed exclusions): met, per build.md. The review confirms that the only leftovers are in an applied migration comment and the plan doc, both excluded.
4. No `db.<production name>` calls: met. The diff replaces every call with `repository.` or `production_repository.`.
5. Re-exports removed from `app.core.db`: met. The diff removes the import and all 14 `__all__` entries.
6. Modules import cleanly: met, per build.md. The gate's test run imports the whole app.
7. Same URL map: met, per build.md (8 rules, the same on the branch and on main). Blueprint names are unchanged in the diff.
8. Package `__init__` files import nothing: met. I checked the diff and both files hold only a docstring.
9. Moves recorded as renames: met. `git diff -M --stat` shows `=>` for all five.
10. Templates, migrations, `init.sql` and `navigation.py` untouched: met. I checked myself: the diff of those paths plus `docker-compose.yml`, `web.Dockerfile` and `.github` against origin/main has 0 lines.
11. `TODO(phase 4)` sits above the cash summary queries and the SQL is unchanged: met. I checked myself: it is at lines 107-109 of `app/cash_summary/service.py`, and the diff touches no SQL line.
12. Strict mypy on the three non-route modules: met. They are in `pyproject.toml`, and the gate's mypy run passes.
13. Tests unchanged in substance, same count: met. 1844 tests on both, no test deleted, and the test diffs change only imports, prefixes and monkeypatch targets.
14. Docs and `CLAUDE.md` name the new packages: met. See the CLAUDE.md diff and the counts in build.md.
15. Pages behave as before: met, through the web tests, which the spec allows. A logged-in browser check was not done. The smoke run gave a 302 to login on every route.

## For the human
Recommendation: merge

Reasons:
- This is a pure refactor and every check passes. The cuadre totals logic in `app/cash_summary/service.py` is byte-for-byte unchanged except for one 3-line comment and two docstring sentences. The `@permission_required` decorators in both routes modules are unchanged.
- `app/cash_summary/routes.py` now takes `get_conn` from `app.shared.db` instead of `app.core.db`. It is the same function: `app.core.db` re-exports it from `app.shared.db`.
- Nothing touches deploy config, migrations, environment variables or dependencies.

The one check I'd like from you: after merging, open `/produccion`, one `/produccion/<id>` with a linked declaration photo, and `/cuadre` plus `/cuadre/pdf` while logged in, because no logged-in browser check was done.

Files to look at:
- `app/cash_summary/service.py`, `app/cash_summary/routes.py`
- `app/production/routes.py`, `app/production/service.py`
- `app/core/db/__init__.py`, `app/web/app.py`
- `app/web/blueprints/declarations.py`, `app/web/blueprints/records.py` (call prefix only)
