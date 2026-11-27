DECISION: ACCEPT
RISK: medium
SURFACES: none
RESCOPED: no

## Summary
- Accepted as written: a pure file move of the production and cash summary code into `app/production/` and `app/cash_summary/`, with no behavior change.
- The dependency #114 (customers, pricing, inventory move) is merged on `main`, so this one can follow the same pattern.
- The move is contained: three production modules, two cash summary modules, their imports in `app/web/app.py`, `app/core/db/__init__.py` and two test files, plus docs and mypy overrides.
- Risk is medium only because it touches shared wiring (blueprint registration, the `db` facade re-exports); URLs, endpoints and templates stay the same.

## Scope
- `git mv` `app/web/blueprints/production.py` to `app/production/routes.py`, `app/core/production.py` to `app/production/service.py`, `app/core/db/production.py` to `app/production/repository.py`.
- `git mv` `app/web/blueprints/cash_summary.py` to `app/cash_summary/routes.py`, `app/core/cash_summary.py` to `app/cash_summary/service.py`. Its SQL stays where it is today, with a `TODO(phase 4)` comment where it reads orders-domain tables.
- Update every importer: `app/web/app.py` (blueprints and `PRODUCTION_STATUS_LABELS`), the production re-exports in `app/core/db/__init__.py`, and the imports and monkeypatch targets in `tests/core/test_production.py` and `tests/core/test_cash_summary.py`.
- The production detail page keeps serving declaration files by importing the declarations code from its current modules (`app/core/db/declarations.py`, `app/web/blueprints/declarations.py`); declarations do not move here.
- Blueprint names, `url_for` endpoints, URLs, templates (still in `app/web/templates/`) and log lines are unchanged.
- Extend the strict mypy override in `pyproject.toml` to the moved service and repository modules; update paths in `docs/CORE.md`, `docs/WEB.md`, `docs/ARCHITECTURE.md` and module docstrings.
- Left out: orders and declarations moves, and any behavior change.

## Why
The issue is a precise, mechanical refactor with clear acceptance criteria (a grep that must find nothing, unchanged tests, `factory/checks.sh` passing). A search of `app/` and `tests/` shows only eight import sites in the app and two test files referencing the moved modules, so it fits comfortably in one PR, following the layout #114 already established in `app/customers/`, `app/pricing/` and `app/inventory/`.
