STATUS: READY

# Build: Move customers, pricing and inventory into their domain packages

## Summary
- The customers, pricing and inventory blueprints and their SQL now live in `app/customers/`, `app/pricing/` and `app/inventory/` (`routes.py` + repository). All seven files moved with `git mv` and show as renames.
- `app/core/db/__init__.py` no longer re-exports the 30 moved names. Callers (app and tests) go through the module: `repository.<fn>` inside a domain's own routes, `customers_repository` / `pricing_repository` / `inventory_repository` elsewhere.
- Import cycle avoided: the customers repository imports the `plates` module object, and `plates.py` imports the two leaf modules by full path. All 13 modules import cleanly in a fresh interpreter.
- Strict mypy now covers the new repositories. Docs, docstrings and the `CLAUDE.md` Project bullets point at the new paths.
- Draft PR: https://github.com/nickkmm/plates/pull/193

## Changes
- `app/customers/routes.py`, `app/pricing/routes.py`, `app/inventory/routes.py` (moved from `app/web/blueprints/`): `from app.<domain> import repository` and `repository.<fn>`; `get_conn` from `app.shared.db`; docstring paths.
- `app/customers/repository/customers.py`, `ownership_cards.py`, `app/pricing/repository.py`, `app/inventory/repository.py` (moved from `app/core/db/`): only `customers.py` changed (`from app.core.db import plates`, `plates.relink_plates(...)`).
- New `app/customers/__init__.py`, `app/pricing/__init__.py`, `app/inventory/__init__.py` (docstring only, with the dependency rule) and `app/customers/repository/__init__.py` (explicit re-export + `__all__`).
- `app/core/db/__init__.py`: removed the four imports, their 30 `__all__` entries and their docstring entries; added one line saying where those tables live now.
- `app/core/db/plates.py`: leaf imports from `app.customers.repository.ownership_cards` and `app.pricing.repository`; docstring.
- `app/web/app.py`: imports the three blueprints from the new routes modules (same names and registration order); docstring.
- `app/core/declarations.py`, `app/core/ingestion.py`, `app/migrate.py`, `app/web/blueprints/{declarations,ingest,records,review}.py`: aliased repository imports and call prefixes.
- Docstrings/comments only: `app/core/db/orders.py`, `app/web/helpers.py`, `app/shared/models.py`.
- `pyproject.toml`: added `app.customers.repository`, `app.customers.repository.*`, `app.pricing.repository`, `app.inventory.repository` to the strict override.
- Tests (same files, no renames or deletions): imports, call prefixes and docstrings in `tests/conftest.py`, `tests/core/db/test_{customers,ownership_cards,pricing,inventory,plates,orders,declarations}.py`, `tests/core/test_{declarations,ingestion}.py`, `tests/web/test_{customers,pricing,inventory,csrf,declarations,ingest,records,review,public_declaration}.py`.
- Docs: `docs/CORE.md` (the four rows moved into a new table under a paragraph about the three domains, sections renamed in place, `db/__init__.py` row updated), `docs/WEB.md` (route rows name `app/<domain>/routes.py` and `repository.<fn>`; other rows use the aliases), `docs/ARCHITECTURE.md` (diagram node + paragraph next to `app/auth/`), `docs/DATABASE.md`, `docs/README.md`, `docs/LOCAL_DEVELOPMENT.md`, `README.md`, `CLAUDE.md` (new Project bullet).

## Acceptance criteria
1. [x] Layout: `ls` lists all 11 new files; `ls` of the 7 old paths gives 7 "No such file".
2. [x] `rg -n "app\.core\.db\.(customers|ownership_cards|pricing|inventory)|blueprints\.(customers|pricing|inventory)" app tests docs --glob '!docs/factory-history/**'`: exit 1, no matches.
3. [x] The path `rg` of criterion 3 (with its excludes): exit 1, no matches.
4. [x] The import loop over the 13 modules printed no `FAIL`; `SECRET_KEY=dev .venv/Scripts/python -c "import app.web.app"` exited 0.
5. [x] The `hasattr` assertion on `app.core.db` exited 0.
6. [x] The `db.<moved name>` `rg` of criterion 6: exit 1, no matches.
7. [x] `rg 'Blueprint\("(customers|pricing|inventory)"' app` matches only `app/inventory/routes.py:13`, `app/customers/routes.py:32`, `app/pricing/routes.py:24`; `git diff origin/main -- app/web/templates app/migrations init.sql` is empty (0 lines).
8. [x] `git diff -M --stat origin/main...HEAD` shows all 7 moves with `=>` (customers.py, ownership_cards.py, customers routes, inventory repository/routes, pricing repository/routes).
9. [x] `rg "^(from|import) "` on the three package `__init__.py` files: no matches. The `app.` imports of the three leaf repositories are only `app.shared.normalize`.
10. [x] `pyproject.toml` strict override lists the four modules (mypy: "Success: no issues found").
11. [x] `pytest -q --collect-only | tail -1`: "1844 tests collected" on the branch and on an `origin/main` checkout; `git diff --diff-filter=D --name-only origin/main...HEAD -- tests` is empty.
12. [x] `rg -c "app/customers|app/pricing|app/inventory"`: WEB.md 9, ARCHITECTURE.md 4, CORE.md 13. `CLAUDE.md` has the new bullet.
13. [x] `agent-browser` is not installed on this machine (`agent-browser: command not found`), so I checked over HTTP. I ran the branch app on port 5290 and an `origin/main` checkout on 5291, both against the job's test database `plates_test_19_gw0` with seeded rows (2 customers, 1 ownership card, 1 price, 1 preparation), and logged in as the admin `testuser`. `/`, `/customers`, `/customers?q=empresa`, `/customers/1`, `/precios` and `/inventory` all returned 200. The list shows both customers, the filter shows only the EMPRESA, the detail shows the ownership-card table (ZZLIC1/ZZZ999), `/precios` shows the 45000 rate and `/inventory` shows the material cards. `diff -r` of the two servers' HTML (CSRF tokens masked) printed IDENTICAL. The nav on `/` links `/inventory`, `/precios` and `/customers`. The seeded rows were deleted afterwards and both servers were stopped.

## Validation
- `bash factory/checks.sh` (with `TEST_POSTGRES_DB=plates_test_19`, `TEST_REQUIRE_DB=1` and the `.env` credentials): passed. ruff "All checks passed!", mypy "Success", pytest "1844 passed".
- `ruff check --select I,F401` on the changed files: clean before the change; I used `--fix` only on those files for the new import lines and the now-unused `db` imports.
- The `rg`, import, `hasattr`, collect-only and `git diff` checks above all passed.

## Log
### Round 1
- Set up `.venv` and installed `requirements-dev.txt` in the worktree.
- Commit 1: the seven `git mv` moves only. Commit 2: package `__init__` files, the repository re-export, the import cycle fix, callers and tests switched to module access, `app/core/db/__init__.py` trimmed, the strict mypy override. Commit 3: docs and `CLAUDE.md`.
- Pushed and opened draft PR #193.
- The PR body omits the "Generated with Claude Code" line because `CLAUDE.md` forbids it. Commits carry only the generic `Co-Authored-By` trailer.
- By mistake I wrote an empty stray file, `factory-tmp-019.py`, at the main checkout root. I removed it right away and the main checkout's `git status` is clean.
