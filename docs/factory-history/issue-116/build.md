STATUS: READY

# Build: Move the point of sale, records and duplicates review into app/orders/

## Summary
- New `app/orders/` package: `routes/` (ingest, records, review), `service.py` (was `app/core/ingestion.py`), the three pedido parsers and `repository/` (orders.py, plates.py). All nine files moved with `git mv` and show up as renames.
- `app/core/db` no longer re-exports orders/plates names; every caller uses `orders_repository.<name>`.
- `relink_plates` and `_card_id_for` (now `card_id_for_order_plate`) moved to `app/customers/repository/ownership_cards.py` with a `TODO(phase 4)`, so the dependency only points orders -> customers.
- Strict mypy now covers the moved non-route modules. Docs point at the new paths.
- Round 1: first build. checks.sh passes (1844 tests); draft PR #195.

## Changes
- `app/orders/__init__.py`, `app/orders/routes/__init__.py`: new docstring-only modules (domain description and dependency rule).
- `app/orders/repository/__init__.py`: new; docstring plus explicit re-exports and `__all__` for the 20 public names of orders.py and plates.py.
- `app/orders/routes/{ingest,records,review}.py`, `app/orders/service.py`, `app/orders/{pdf_extract,xlsx_extract,pedido_upload}.py`, `app/orders/repository/{orders,plates}.py`: moved. Only import lines, `db.` -> `orders_repository.` call prefixes and docstrings changed. The exception is plates.py, which lost `relink_plates`/`_card_id_for` and now calls `card_id_for_order_plate`.
- `app/customers/repository/ownership_cards.py`: gained `card_id_for_order_plate` (body unchanged) and `relink_plates`, under a `TODO(phase 4)` comment. `customers.py` calls `relink_plates` directly and no longer imports `app.core.db`. `__init__.py` re-exports `relink_plates`.
- `app/core/db/__init__.py`: orders/plates imports, `__all__` entries and docstring lines removed. The docstring now names the new locations.
- Callers: `app/web/app.py` (blueprint imports and module docstring), `app/migrate.py`, `app/web/helpers.py`, `app/production/routes.py`, `app/web/blueprints/declarations.py`, `app/core/declarations.py`. Docstrings in `app/shared/db.py`, `app/shared/models.py`, `app/customers/__init__.py` and `app/production/__init__.py`.
- `pyproject.toml`: the six `app.orders.*` entries were added to the strict override.
- Tests: only import lines, call prefixes, the two monkeypatch targets and docstrings changed. In addition, `tests/core/test_ingestion.py` now monkeypatches `orders_repository.insert_plate` instead of `db.insert_plate`, the same call-prefix change.
- Docs: CORE.md (orders section, repository table, `relink_plates` and `card_id_for_order_plate` rows moved to the ownership-cards table with a note on why), WEB.md, ARCHITECTURE.md, DATABASE.md, SIIGO_INTEGRATION.md (`:line` refs into moved files dropped), CUSTOMER_PORTAL.md, docs/README.md, README.md, CLAUDE.md.

## Acceptance criteria
1. [x] Old paths gone, 12 new files: `git ls-files <9 old paths>` printed nothing. `git ls-files app/orders` listed all 12 expected files.
2. [x] The dotted-name `rg` over app, tests, docs, README.md and CLAUDE.md (factory-history excluded) found nothing.
3. [x] The path `rg` (factory-history and migrations excluded) found nothing.
4. [x] The `rg` on `app/core/db/__init__.py` found nothing. The `hasattr` python check exited 0.
5. [x] `rg "app\.orders|app\.core\.db" app/customers` found nothing. The forbidden-import `rg` on service, repository and parsers found nothing. `rg "^(from|import) " app/orders/__init__.py` found nothing.
6. [x] Each of the 13 modules, plus `app.web.app`, imported with exit 0 in its own process (`SECRET_KEY=dev .venv/Scripts/python -c "import <m>"`).
7. [x] The sorted URL map (rule, endpoint, methods) printed by the branch and by an `origin/main` export (`git archive` into the scratchpad) was identical.
8. [x] `git diff -M --stat origin/main...HEAD` shows all nine moves with `=>`. The templates/migrations/init.sql/navigation.py diff is empty (0 lines).
9. [x] `ownership_cards.py` has `TODO(phase 4)` (line 252), `def card_id_for_order_plate` (255) and `def relink_plates` (283). `rg` for those definitions in `app/orders` found nothing.
10. [x] The `pyproject.toml` strict override lists `app.orders.service`, `.pedido_upload`, `.pdf_extract`, `.xlsx_extract`, `.repository` and `.repository.*`.
11. [x] `pytest --collect-only` reports 1844 tests on both the branch and origin/main. No test file was deleted (the `--diff-filter=D` list is empty). The test diff is limited to imports, prefixes, the two monkeypatch targets and docstrings.
12. [x] `rg -c app/orders`: CORE.md 20, WEB.md 6, ARCHITECTURE.md 4, README.md 1, CLAUDE.md 1.
13. [x] Covered by the test fallback, not a browser check: `tests/web/test_ingest.py`, `test_ingest_pdf.py`, `test_records.py`, `test_records_search_pdf.py`, `test_review.py`, `test_customers.py` and `tests/core/db/test_plates.py` with `TEST_POSTGRES_DB=plates_test_21 TEST_REQUIRE_DB=1` gave 275 passed.

## Validation
- `bash factory/checks.sh` (TEST_POSTGRES_DB=plates_test_21, TEST_REQUIRE_DB=1): passed (ruff, mypy with no issues in 63 files, 1844 tests passed).
- `ruff check --select I --fix` was run only on the changed files whose imports I edited.
- Each acceptance-criterion command above: passed.

## Log
### Round 1
- Set up `.venv` and moved the nine files with `git mv`.
- Moved `relink_plates` and `card_id_for_order_plate` into ownership_cards.py, which removes the customers -> orders cycle.
- Rewrote the `db.<orders/plates name>` call sites in app and tests, removed the re-exports from `app/core/db`, added the strict mypy entries and updated docs and docstrings.
- Ran the checks and pushed. Commits:
  - e7591da Keep the orders package name out of app/customers
  - 3236a5c Point docs at the orders package
  - fdcbfff Move point of sale, records and review into app/orders
- Opened draft PR https://github.com/nickkmm/plates/pull/195. Per CLAUDE.md, the body has no "Generated with" line.
