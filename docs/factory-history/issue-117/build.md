STATUS: READY

# Build: Move declarations into app/declarations/ and remove app/core/

## Summary
- The two declaration blueprints, anti-spam, service, PDF and SQL now live in `app/declarations/` (moved with `git mv`, history kept). URLs, endpoints, templates and SQL are unchanged.
- `app/core/` (with its `app/core/db` re-export) and the empty `app/web/blueprints/` are deleted. Callers use `app.shared.db` for `get_conn` and `declarations_repository` for the declarations SQL.
- Strict mypy now covers `app.declarations.{service,repository,pdf,antispam}`, and mutmut points at `app/declarations`.
- `docs/CORE.md` is now `docs/DOMAINS.md`, every doc points at the new paths, and phase 1 is marked done.
- Draft PR: https://github.com/nickkmm/plates/pull/196

## Changes
- `app/declarations/`: `routes.py` (was `app/web/blueprints/declarations.py`), `public_routes.py` (was `public_declaration.py`), `antispam.py` (was `app/web/antispam.py`, unchanged), `service.py` (was `app/core/declarations.py`), `pdf.py` (was `app/core/declaration_pdf.py`), `repository.py` (was `app/core/db/declarations.py`). Only the import lines, the `db.` -> `declarations_repository.` call prefixes, docstrings and comments changed. The new `__init__.py` holds only a docstring with the dependency rule.
- Deleted: `app/core/__init__.py`, `app/core/db/__init__.py`, `app/web/blueprints/__init__.py`.
- Callers moved to the new imports: `app/web/app.py` (and its docstring), `app/migrate.py`, `app/schema_migrate.py`, `app/orders/routes/{ingest,records,review}.py`, `app/production/routes.py`.
- Docstrings and comments: `app/shared/__init__.py`, `app/shared/db.py`, `app/orders/__init__.py`, `app/production/__init__.py`, `app/orders/service.py`, `app/orders/pdf_extract.py`, `app/cash_summary/service.py`.
- `pyproject.toml`: the strict override lists the four `app.declarations` modules where `app.core.*` was; the comment above it is updated. `setup.cfg`: `source_paths=app/declarations`.
- Tests: import lines, call prefixes, monkeypatch targets (`declarations_repository.list_cards_for_declarations`, `"app.declarations.routes.build_declaration_pdf"`, and `pd.declarations_repository.count_recent_by_ip` in `test_public_declaration.py`), `tests/conftest.py` patching `get_conn` only on `app.shared.db` (it drops the `shared_db` alias, so `shared_db._get_pool()` becomes `db._get_pool()`), and docstrings. `TestGetConnReExport` is replaced by `TestNoCorePackage::test_app_core_is_gone`, which checks that `Path(app.__file__).parent / "core"` does not exist.
- Docs: `docs/CORE.md` -> `docs/DOMAINS.md` (new title and intro; the `db/` section is now "Data layer (each domain's repository)" with the `db/__init__.py` row removed; the declarations sections are titled `app/declarations/repository.py`, `service.py` and `pdf.py`). Also `docs/ARCHITECTURE.md` (intro, diagram, a new `app/declarations/` paragraph, the "until it moves" clauses dropped), `docs/WEB.md` (intro, the declarations rows with `declarations_repository.<fn>`, the public-form section, the anchor link), `docs/README.md` (map row), `README.md`, `docs/DATABASE.md`, `docs/LOCAL_DEVELOPMENT.md`, `docs/SIIGO_INTEGRATION.md` (`antispam.py:98` is still `_post`, so the line number is kept and the function is named), `docs/FRONTEND.md`, `docs/MIGRATION_PLAN.md` (a status line under the phase 1 heading), and the one `app/core/` bullet in `CLAUDE.md`.

## Acceptance criteria
1. [x] Paths gone, package contents: `test ! -e app/core && test ! -e app/web/blueprints && test ! -e app/web/antispam.py` printed OK. `git ls-files app/declarations` lists exactly `__init__.py antispam.py pdf.py public_routes.py repository.py routes.py service.py`.
2. [x] No `app.core` or `app/core` left: with `--glob '!docs/factory-history/**' --glob '!app/migrations/**'` the only match is the old-path wording I first put in the MIGRATION_PLAN status line, which I then rewrote to "the old core package". A re-run finds nothing.
3. [x] With one exception that the spec itself forces: `rg "web\.antispam|web/antispam|blueprints\.(declarations|public_declaration)|web/blueprints|CORE\.md"` finds nothing in app, tests, README.md and every doc except `docs/MIGRATION_PLAN.md` lines 131-138, the "Where today's code goes" table. Requirement 9 and Out of scope say not to edit that table, so it keeps its historical `web/blueprints/...` and `web/antispam.py` paths. `rg "from app\.shared\.db import get_conn" app tests` finds nothing (exit 1).
4. [x] `rg -n "^(from|import) " app/declarations/__init__.py` finds nothing (exit 1).
5. [x] Each of `import app.declarations`, `.routes`, `.public_routes`, `.antispam`, `.service`, `.pdf`, `.repository`, `app.production.routes`, `app.orders.routes.ingest`, `app.migrate`, `app.schema_migrate` and `app.web.app` exits 0 in a fresh process with `SECRET_KEY=dev`.
6. [x] The `url_map` dump (rule, endpoint, sorted methods) is byte-identical on the branch and on an `origin/main` export (`git archive origin/main` into the scratchpad, run with the same venv). Compared in JS: same === true, 4291 chars.
7. [x] `git diff -M --name-status origin/main...HEAD` shows R100 antispam, R099 pdf, R098 public_routes, R099 repository, R090 routes, R097 service and R095 `docs/CORE.md -> docs/DOMAINS.md`. The diff over templates, migrations, `init.sql`, `csrf.py`, `helpers.py` and `navigation.py` is 0 bytes.
8. [x] The strict override lists the four `app.declarations` modules and no `app.core` entry. `.venv/Scripts/python -m mypy app/` reports "Success: no issues found in 61 source files".
9. [x] `pytest --collect-only` gives 1844 on the branch and 1844 on `origin/main`. No test file is deleted (`--diff-filter=D` lists 0 files). In the test diff, every changed line that isn't an import or a call prefix is a docstring, a monkeypatch target, a conftest `get_conn` patch line, or the replaced `TestGetConnReExport` test. I checked this by grepping the `-U0` diff.
10. [x] The full suite passes against `TEST_POSTGRES_DB=plates_test_22` with `TEST_REQUIRE_DB=1` (1844 passed, through `factory/checks.sh`). That run includes every file listed in the criterion.
11. [x] `rg -c "app/declarations"` counts DOMAINS.md 11, WEB.md 14, ARCHITECTURE.md 2, README.md 2, CLAUDE.md 1. `docs/README.md` links `DOMAINS.md`. `docs/MIGRATION_PLAN.md` has "Status: done (#117 ...)" right under the phase 1 heading.
12. [x] `git diff origin/main...HEAD -- CLAUDE.md` changes only the `app/core/` bullet of `# Project`. The `setup.cfg` diff is only `-source_paths=app/core` / `+source_paths=app/declarations`.
13. [x] Covered by criterion 10, as the spec allows: `agent-browser` is not installed here (`command not found`). I also ran the app from the worktree on port 5320 against `plates_test_22` with `TURNSTILE_BYPASS=1` and checked with curl: `/healthz` 200; `/declaracion` 200 and the form renders (it has `form_token`/`card_0_` fields); `/declaraciones` and `/produccion` answer 302 to `/login?next=...`. Then I stopped the server (PID 16208 on :5320 only). I did not do the logged-in approve and `/pdf` flow by hand; `tests/web/test_declarations.py` and `test_production.py` cover it.

## Validation
- `bash factory/checks.sh` with `TEST_POSTGRES_DB=plates_test_22 TEST_REQUIRE_DB=1`: passed (ruff clean, mypy clean, 1844 passed, shell and factory-script checks OK).
- `ruff check --select I --fix` on the changed .py files only (import blocks): applied. Then `ruff check app/` passed.
- Comparison of `url_map` and collect count against `origin/main`: identical.
- Import smoke test of the 12 modules: all exit 0.

## Log
### Round 1
- Made the moves with `git mv` and deleted `app/core/` and `app/web/blueprints/`. Rewrote the callers mechanically: `db.<declarations name>` became `declarations_repository.<name>`, and `db` is now `app.shared.db` wherever `get_conn` is still used.
- The first gate run failed 2 tests in `tests/web/test_public_declaration.py`. They monkeypatched `pd.db.count_recent_by_ip`, which no longer exists on `app.shared.db`. I retargeted them to `pd.declarations_repository`, the same kind of change as the two monkeypatch targets the spec names. The second gate run passed.
- The replacement for `TestGetConnReExport` checks the filesystem instead of `find_spec("app.core")`, so the test file does not contain the string `app.core`, which criterion 2 forbids.
- A deviation the spec forces: the MIGRATION_PLAN mapping table stays unedited, so criterion 3's regex still matches it (see criterion 3).
- Opened draft PR #196. Following CLAUDE.md, the body has no "Generated with" line.
