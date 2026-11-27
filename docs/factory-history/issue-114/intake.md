DECISION: ACCEPT
RISK: medium
SURFACES: none
RESCOPED: no

## Summary
- Phase 1 move of three small domains (customers, pricing, inventory) out of `app/core/db/` and `app/web/blueprints/` into `app/customers/`, `app/pricing/` and `app/inventory/`.
- Its dependency, #113 (the `app/auth` move), is merged on `main`, so this one follows the same pattern.
- The change is mechanical but wide: about 380 `db.<function>` call sites in 31 app and test files use the functions being moved.
- No behaviour, URL, endpoint name, template or schema changes. Risk is medium because of how many shared files it touches.

## Scope
Build, as one pure-refactor PR (use `git mv` so history follows the files):
- `app/customers/`: `routes.py` (from `app/web/blueprints/customers.py`) and a repository (from `app/core/db/customers.py` + `app/core/db/ownership_cards.py`, as one `repository.py` or a `repository/` package). An optional `service.py` for any non-HTML customer logic now in the blueprint.
- `app/pricing/`: `routes.py` + `repository.py` (from the pricing blueprint and `app/core/db/pricing.py`).
- `app/inventory/`: `routes.py` + `repository.py` (from the inventory blueprint and `app/core/db/inventory.py`).
- Remove the customer, ownership-card, pricing and inventory re-exports from `app/core/db/__init__.py` and update its docstring. Point every caller at the new modules: `app/web/app.py` blueprint registration, `app/core/db/plates.py`, `app/core/db/orders.py`, `app/core/declarations.py`, `app/core/ingestion.py`, `app/migrate.py`, `app/web/helpers.py`, the declarations/ingest/records/review blueprints, and the tests and their monkeypatch targets.
- Blueprint names, `url_for` endpoints, URLs, templates (still in `app/web/templates/`), log lines and SQL behave exactly as before.
- Extend the strict mypy override in `pyproject.toml` (`disallow_untyped_defs = true`) to the new modules that came from `app/core/` (the repositories, and any service).
- Update the paths in `docs/CORE.md`, `docs/WEB.md`, `docs/ARCHITECTURE.md` and the module docstrings.
- Acceptance: the old files are gone. `rg -n "app\.core\.db\.(customers|ownership_cards|pricing|inventory)|blueprints\.(customers|pricing|inventory)" app tests docs` finds nothing. Tests pass with only import and monkeypatch-target changes, none deleted or weakened. `factory/checks.sh` passes. The PR description lists the improvements made, or says there were none.

Left out: any behaviour change, other domains (orders, plates, declarations, production) and moving templates or HTML helpers (phase 6). The `rg` check runs on `app tests docs`, so it would also hit the old paths quoted in `docs/factory-history/`. That folder is the factory's frozen record, so leave it unchanged and exclude it from the check.

## Why
The issue is a precise, behaviour-neutral slice of `docs/MIGRATION_PLAN.md` phase 1. It names its files, which the plan's "Where today's code goes" table maps to the same three domains, and its acceptance criteria can be checked. The code matches the description: `app/core/db/__init__.py` re-exports these functions, and `app/web/app.py` imports the three blueprints. The #113 `app/auth` move, merged on `main`, set the pattern this follows. The diff is wide but mechanical, and the three domains are small enough to review together in one PR.
