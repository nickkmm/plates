STATUS: READY

# Build: Move production and cash summary into their domain packages

## Summary
- Production now lives in `app/production/` (`routes.py`, `service.py`, `repository.py`) and cash summary in `app/cash_summary/` (`routes.py`, `service.py`). All five files were moved with `git mv` and git records them as renames.
- `app/core/db` no longer re-exports the 14 production names. Callers use `repository.<name>` inside the domain and `production_repository.<name>` outside it, and the two monkeypatches target the repository module.
- A `TODO(phase 4)` comment sits above the cash summary queries. The three non-route modules are in mypy strict. Docs and `CLAUDE.md` point at the new paths.
- The gate passes against the job database: 1844 tests passed, the same collected count as `origin/main`. Draft PR: https://github.com/nickkmm/plates/pull/194

## Changes
- `app/production/__init__.py`, `app/cash_summary/__init__.py` (new): a docstring only, giving the domain's contents and dependency rule.
- `app/production/routes.py` (moved from `app/web/blueprints/production.py`):
  - imports `repository`, the `STATUS_*` constants from `app.production.repository`, and `advance_order`/`revert_order` from `app.production.service`;
  - the 4 production calls now use the `repository.` prefix;
  - declarations and orders calls and `db.get_conn` stay on `app.core.db`;
  - the docstring path is updated.
- `app/production/service.py` (moved from `app/core/production.py`): uses `repository.get_production_status`/`set_production_status`. Docstring updated.
- `app/production/repository.py` (moved from `app/core/db/production.py`): docstring path only.
- `app/cash_summary/routes.py` (moved from `app/web/blueprints/cash_summary.py`): imports from `app.cash_summary.service` and takes `db` (`get_conn`) from `app.shared`. Docstring updated.
- `app/cash_summary/service.py` (moved from `app/core/cash_summary.py`): adds the `TODO(phase 4)` comment above the two queries, with the SQL unchanged. Two docstring sentences are updated, including the layer rule ("this service never imports from app.web").
- `app/core/db/__init__.py`: drops the production import, its 14 `__all__` entries and its docstring entry. The "live in ..." line now names `app/production/repository.py`.
- `app/web/app.py`: imports the two blueprints and `PRODUCTION_STATUS_LABELS` from the new routes modules. In the docstring, the two entries move to the domain-package list.
- `app/web/blueprints/declarations.py`, `app/web/blueprints/records.py`: `from app.production import repository as production_repository`, plus the call prefix.
- `pyproject.toml`: adds `app.production.service`, `app.production.repository` and `app.cash_summary.service` to the strict override.
- Tests (`tests/core/test_production.py`, `tests/core/db/test_production.py`, `tests/core/db/test_orders.py`, `tests/core/test_ingestion.py`, `tests/web/test_production.py`, `tests/core/test_cash_summary.py`, `tests/web/test_cash_summary.py`): only imports, `production_repository.` prefixes, the two monkeypatch targets and docstrings changed.
- Docs: `docs/CORE.md` (intro, db table and text, three sections renamed in place), `docs/WEB.md` (intro paragraph, `/cuadre` rows), `docs/ARCHITECTURE.md` (diagram and a new paragraph on the two packages), `docs/PRODUCTION.md`, `docs/DATABASE.md`, `docs/SIIGO_INTEGRATION.md`, and `CLAUDE.md` (the domains bullet).

## Acceptance criteria
1. [x] **Files moved:** `ls app/production app/cash_summary` shows `__init__`, `routes`, `service` and `repository` (production) and `__init__`, `routes` and `service` (cash summary). `ls` of the 5 old paths answers "No such file" for each.
2. [x] **No old module paths:** the rg for `app.core.(production|cash_summary)`, `app.core.db.production` and `blueprints.(production|cash_summary)` finds nothing.
3. [x] **No old file paths:** the rg for the old file paths (with the given exclusions) finds nothing.
4. [x] **No `db.<name>` calls:** the rg for `db.<14 names>` finds nothing.
5. [x] **Re-exports gone:** the `hasattr` assertion on `app.core.db` exits 0.
6. [x] **Imports work:** the import loop over the 10 modules prints no FAIL, and `import app.web.app` exits 0.
7. [x] **Same URL map:** the URL map list is identical on the branch and on an `origin/main` export (`git archive` into the scratchpad). Both list 8 rules: `/cuadre`, `/cuadre/pdf`, `/produccion`, `/produccion/<id>`, `advance`, `undo`, `declaration_file` and `declaration_card_photo`, with the same endpoints.
8. [x] **Package inits import nothing:** the rg for import lines in both `__init__.py` finds nothing.
9. [x] **Renames:** `git diff -M --stat origin/main...HEAD` shows all 5 as `=>` renames.
10. [x] **Protected areas untouched:** the diff of templates, migrations, `init.sql` and `navigation.py` against `origin/main` is empty (0 lines).
11. [x] **Phase 4 TODO:** `TODO(phase 4)` is at line 107 of `app/cash_summary/service.py`, directly above `with conn.cursor()` and the two queries. `git diff -M origin/main...HEAD -- app/core/cash_summary.py app/cash_summary/service.py` changes only 2 docstring passages and adds the 3 comment lines; no SQL line changes.
12. [x] **Strict mypy:** the three modules are in the strict override, and `mypy app/` passes in `checks.sh`. A bare `mypy` run also checks tests and reports errors there, in files this branch does not touch; the gate runs `mypy app/`.
13. [x] **Tests unchanged in substance:** `pytest --collect-only` reports 1844 tests on both the branch and `origin/main`. No test file is deleted. The test diffs touch only imports, prefixes, monkeypatch targets and docstrings.
14. [x] **Docs and CLAUDE.md:** `rg -c` counts CORE.md 8, WEB.md 4, ARCHITECTURE.md 4, PRODUCTION.md 3. `CLAUDE.md` names both packages (3 matches).
15. [x] **Pages behave as before (covered by tests):** the full suite passes against the job database, including `tests/web/test_production.py` and `tests/web/test_cash_summary.py`. A smoke run of the app from the worktree on port 5300 against the local Postgres gave `/healthz` 200 and 302 to login for `/produccion`, `/produccion/1`, `/cuadre` and `/cuadre/pdf`. No logged-in browser check was done, because there are no dev credentials to log in with; the spec allows the web tests to stand in for it.

## Validation
- `set -a; . <main>/.env; set +a; TEST_POSTGRES_DB=plates_test_20 TEST_REQUIRE_DB=1 bash <main>/factory/checks.sh`: passed (ruff, mypy, 1844 tests passed, shell and factory checks).
- The first run without the database skipped the DB tests. One gate run caught a missing import in `records.py` (ruff F821), which was fixed before the commit.
- The acceptance-criteria commands 1 to 14 above were all run and passed.
- `pytest --collect-only`: 1844 on the branch and 1844 on `origin/main`.

## Log
### Round 1
- Set up `.venv`, moved the five files in a moves-only commit, then fixed imports, prefixes, monkeypatches, mypy strict and docstrings (one commit) and docs (one commit).
- Local Postgres was still recovering after a restart (fsync on startup), so I waited for `pg_isready` before the DB-backed gate run.
- Pushed and opened draft PR #194. It has no "Generated with" line, per CLAUDE.md.
