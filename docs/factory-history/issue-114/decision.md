ESCALATE

## Summary
- Issue #114 is a phase 1 refactor with no behaviour change. The customers (including ownership cards), pricing and inventory routes and SQL moved into `app/customers/`, `app/pricing/` and `app/inventory/`. All 7 files moved with `git mv` and git shows them as renames.
- Round 1 passed the gate: `GATE: PASS` with 1844 tests (the same count as `origin/main`), and the code review is `VERDICT: PASS` with no findings.
- I spot-checked several criteria myself: the old-path `rg`, the `hasattr` check on `app.core.db`, the import-cycle imports, and the empty diff on templates, migrations, `init.sql`, Docker, CI and requirements. All of them passed.
- The pricing (tariff) editor and its SQL moved, and so did the customer master-data code. The project rules ask a person to check any change to prices or tariffs before it merges.

## Why
Every spec criterion is met and the checks are green. The diff contains only file moves, import and call-prefix changes, and docstring and doc updates. No SQL, URL, blueprint name, permission decorator or template changed. The change stays inside intake's Scope and touches no migration, env var, `docker-compose.yml`, `web.Dockerfile`, CI or dependency. The project's rule for anything that touches prices or tariffs still applies, though, so a person has to check the PR before it merges.

## Acceptance criteria
1. New layout exists and the 7 old files are gone: met. I listed `app/core/db` and `app/web/blueprints`, and none of the old files are there.
2. The old-import `rg` finds nothing: met. I ran it and it exited 1 with no matches.
3. The old-path `rg` finds nothing: met (build.md, and the review re-ran it).
4. No import order hits the cycle: met. I imported `app.pricing.repository`, `app.customers.repository.customers` and `app.core.db.plates` each in a fresh interpreter, with no failures. build.md covers all 13 modules and `app.web.app`.
5. `app.core.db` no longer has the moved names: met. I ran the `hasattr` assertion and it exited 0.
6. No `db.<moved name>` remains: met (build.md, and the review re-ran it).
7. Blueprints are defined only in the new routes, and templates, migrations and `init.sql` are untouched: met. The diff on those paths, plus Docker, CI and requirements, is 0 lines.
8. The seven moves show as renames: met (`git diff -M --stat`). `pricing/repository.py`, `inventory/repository.py` and `ownership_cards.py` are 100% similar.
9. The package `__init__` files have no imports, and the leaf repositories import only `app.shared`: met (build.md, and the diff matches).
10. The strict mypy override lists the four modules: met (`pyproject.toml` in the diff; mypy passes in checks.md).
11. Same test count, no test file deleted: met. The count is 1844 on both, and checks.md shows 1844 passed.
12. The docs name the new packages and `CLAUDE.md` has the bullet: met (build.md counts, plus the `CLAUDE.md` diff).
13. The pages render as on main: met. build.md diffed the HTML from the branch and from main over HTTP and found it identical apart from CSRF tokens. The review accepted this as a substitute for the browser check.

## For the human
Recommendation: don't merge yet

Reasons:
- The project rules ask for a human check on any change that touches prices or tariffs. The `/precios` tariff editor (`app/pricing/routes.py`) and its SQL (`app/pricing/repository.py`) moved. I checked that the repository is byte-identical (100% rename) and that the routes changed only in imports and the `db.` → `repository.` prefix. `_parse_unit_price`, `permission_required("pricing.manage")` and the error handling are unchanged. `app/core/ingestion.py` still takes the tariff snapshot, now through `pricing_repository.current_prices`.
- Everything else is clean. Once you've checked the files below, I see no reason not to merge.

What to check: the pricing and customer files are moves with only import changes, and the declarations review blueprint still calls the same customer and ownership-card functions. Merging to `main` deploys to production, but this PR runs no migrations.

Files to look at:
- `app/pricing/routes.py`, `app/pricing/repository.py`
- `app/customers/routes.py`, `app/customers/repository/customers.py` (now calls `plates.relink_plates` through the module, to avoid the import cycle), `app/customers/repository/__init__.py`
- `app/core/db/plates.py` (leaf imports), `app/core/db/__init__.py`
- `app/core/ingestion.py`, `app/core/declarations.py`, `app/web/blueprints/declarations.py`
- `CLAUDE.md` (new Project bullet)
