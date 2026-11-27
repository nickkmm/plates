DECISION: ACCEPT
RISK: medium
SURFACES: none
RESCOPED: no

## Summary
- Accepted as written: a phase 1 move of the point of sale, records, duplicates review, ingestion, the pedido parsers and the orders/plates SQL into `app/orders/`, with no behavior change.
- The dependency #115 (production and cash summary moves) is merged on `main`, so this can start now.
- It is a mechanical but wide change (about 2,500 lines moved, plus every `db.<order/plate function>` caller and about 11 test files), so the risk is medium.
- One cycle needs care: `app/customers/repository/customers.py` calls `plates.relink_plates`, and ingestion imports `customers`.

## Scope
Build what the issue lists, following docs/MIGRATION_PLAN.md phase 1:
- `git mv` `app/web/blueprints/ingest.py`, `records.py` and `review.py` to `app/orders/routes/`. Blueprint names, URLs, `url_for` endpoints, templates (still in `app/web/templates/`) and log lines stay the same.
- `git mv` `app/core/ingestion.py` (to `app/orders/service.py`, or to `app/orders/ingestion.py` re-exported by the service), plus `pedido_upload.py`, `pdf_extract.py` and `xlsx_extract.py` into `app/orders/` under the same names.
- Merge `app/core/db/orders.py` and `app/core/db/plates.py` into `app/orders/repository.py` (or a `repository/` package). Drop their re-exports from `app/core/db/__init__.py` and point every caller at `app.orders`. Callers include `app/web/helpers.py`, `app/production/routes.py`, `app/web/blueprints/declarations.py`, `app/core/declarations.py`, `app/migrate.py`, `app/cash_summary` and `app/customers`.
- Keep the dependencies one-way: `orders` may use `customers`, but `customers` must not import `orders`. Today `customers` calls `relink_plates`. Fix that with the smallest change that keeps behavior identical, for example by moving the shared piece to `app/shared/` as the doc says, or by having the orders side make the call. Explain the choice in the PR.
- Extend the strict mypy override in `pyproject.toml` to the new module paths. Update the paths in `docs/CORE.md`, `docs/WEB.md`, `docs/ARCHITECTURE.md` and in the module docstrings, including `app/core/db/__init__.py` and `app/customers/__init__.py`.
- Change tests only in their import paths and monkeypatch targets. You may move test files too.

Left out: any behavior change (the API, Siigo, new screens), and moving templates or the HTML helpers (those come in phase 6).

## Why
The issue is precise, matches docs/MIGRATION_PLAN.md ("Where today's code goes" puts exactly these files in `orders/`), and has clear acceptance criteria (an `rg` check, an import check, the existing tests, `factory/checks.sh`). It is one coherent move that fits in a single PR. It is not low risk: the point of sale is the most critical flow, about 34 `app.core` import sites and 11 test files change, and the `customers` → `plates` import would become a cycle if the files were simply moved.
