DECISION: ACCEPT
RISK: medium
SURFACES: security
RESCOPED: no

## Summary
- Last step of phase 1 of docs/MIGRATION_PLAN.md: move the declarations code into `app/declarations/` and delete `app/core/`.
- Its dependency, #116 (orders move), is merged. `app/core/` now holds only the declarations service, PDF and repository, plus the `db` re-export package.
- The task is a mechanical move with a precise file list and checkable acceptance criteria, so it fits in one PR.
- Risk is medium because about 10 app modules and 16 test files change their imports. The moved code includes the public form and anti-spam, so security reviewers should confirm nothing changes apart from paths.

## Scope
Build what the issue specifies, with no behavior change:
- Move with `git mv`: `app/web/blueprints/declarations.py` -> `app/declarations/routes.py`, `app/web/blueprints/public_declaration.py` -> `app/declarations/public_routes.py`, `app/web/antispam.py` -> `app/declarations/antispam.py`, `app/core/declarations.py` -> `app/declarations/service.py`, `app/core/declaration_pdf.py` -> `app/declarations/pdf.py`, `app/core/db/declarations.py` -> `app/declarations/repository.py`.
- Delete `app/core/db/__init__.py` and `app/core/`. Every `from app.core import db` caller moves to direct imports: `get_conn` from `app.shared.db`, declaration functions from `app.declarations.repository`. Today those callers are `app/migrate.py`, `app/schema_migrate.py`, `app/orders/routes/{ingest,records,review}.py`, `app/production/routes.py`, the two declaration blueprints and the tests. No re-export shim is left behind.
- URLs, blueprint names and `url_for` endpoints, templates (still in `app/web/templates/`) and log lines stay the same. Tests change only in import paths and monkeypatch targets.
- `pyproject.toml`: replace `app.core.*` in the strict mypy override with the new `app.declarations` modules (service, repository, pdf, plus antispam if it passes). Keep `disallow_untyped_defs = true`.
- Docs: update `docs/CORE.md` (it may be renamed, e.g. to `docs/DOMAINS.md`, with `docs/README.md`'s map fixed), `docs/ARCHITECTURE.md`, `docs/WEB.md` and the module docstrings that mention `app/core`, including `app/shared/__init__.py`, `app/shared/db.py`, `app/orders/__init__.py` and `app/production/__init__.py`. Mark phase 1 as done in `docs/MIGRATION_PLAN.md`. In `CLAUDE.md`, change only the `# Project` bullet about `app/core/`.
- Applied SQL migrations that mention `app/core` in comments must not be edited. Acceptance criterion 1 lists only `app tests docs README.md CLAUDE.md`, so those comments in `app/migrations/` count as history and stay as they are.

Out of scope: any behavior change, and any CLAUDE.md edit beyond that one bullet.

## Why
On main, `app/core/` holds only `declarations.py`, `declaration_pdf.py`, `db/declarations.py` and the `db/__init__.py` re-export. The remaining `app.core` imports are the ones listed above, so the move is bounded and every acceptance criterion can be checked by command (`test ! -e app/core`, `rg`, mypy, `factory/checks.sh`). It follows the moves of earlier phase 1 tasks such as #116.

## Questions
