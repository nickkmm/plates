# Move declarations into app/declarations/ and remove app/core/

> Issue #117: Move declarations into their domain and remove app/core

## Summary
- Last step of phase 1 of docs/MIGRATION_PLAN.md: `git mv` the two declaration blueprints, anti-spam, the declarations service, PDF and SQL into a new `app/declarations/` package. No behavior change: same URLs, endpoints, templates, SQL and log lines.
- `app/core/` is deleted, including the `app/core/db` re-export package. Every `from app.core import db` caller (app and tests) switches to `from app.shared import db` for `get_conn` and `from app.declarations import repository as declarations_repository` for the declarations SQL.
- Strict mypy moves from `app.core.*` to the four new `app.declarations` modules.
- Docs follow the move: `docs/CORE.md` becomes `docs/DOMAINS.md`, phase 1 is marked done, and the one `app/core/` bullet in CLAUDE.md is rewritten.
- `app/web/blueprints/` is left with only an empty `__init__.py`, so it is removed too.

## Context
**Files that move** (sizes from `wc -l`):
- `app/web/blueprints/declarations.py` (601 lines; blueprint `declarations`, `/declaraciones...`). Imports `from app.core import db`, `from app.core import declarations as decl`, constants from `app.core.db.declarations` and `build_declaration_pdf` from `app.core.declaration_pdf`. It also imports `app.production.repository`, `app.orders.repository`, `app.customers.repository` and `app.auth`.
- `app/web/blueprints/public_declaration.py` (378; blueprint `public_declaration`, `GET/POST /declaracion`, the public form). Imports `from app.core import db`, names from `app.core.declarations`, `from app.web import antispam` and `app.web.csrf`.
- `app/web/antispam.py` (127; fully annotated, no DB).
- `app/core/declarations.py` (745; service: `validate_declaration`, `submit_declaration`, `approve_declaration`, `reject_declaration`, constants). Imports `from app.core import db` and `app.core.db.declarations`.
- `app/core/declaration_pdf.py` (485; `build_declaration_pdf`). Imports `app.core.db.declarations` and `app.core.declarations`.
- `app/core/db/declarations.py` (461; declarations / declaration_cards SQL, constants, `declaration_stage`, `requires_notarization`).

**Files that are deleted:** `app/core/__init__.py` (empty), `app/core/db/__init__.py` (re-exports `get_conn` from `app.shared.db` and 14 declarations names), and, after the move, `app/web/blueprints/__init__.py` (empty file; nothing else is left in that package).

**Other callers of `app.core` / the moved modules:**
- app: `app/web/app.py` (lines 79-80 import the two blueprints; its module docstring lists them under `app/web/blueprints/`), `app/migrate.py:23`, `app/schema_migrate.py:56` (and its comment at 108), `app/orders/routes/ingest.py:12-14`, `records.py:9` (and the comment at 247), `review.py:9`, `app/production/routes.py:32-33,45` (imports `CARD_PHOTO_SIDES`, `FILE_KINDS` from the staff declarations blueprint).
- tests: `tests/conftest.py:142` (and its docstring at 250-257), `tests/core/test_declarations.py`, `test_declaration_pdf.py`, `test_ingestion.py`, `tests/core/db/test_connection.py`, `test_declarations.py`, `test_production.py`, `tests/web/test_antispam.py`, `test_csrf.py`, `test_declaration_template.py`, `test_declarations.py`, `test_ingest.py`, `test_inventory.py`, `test_production.py`, `test_public_declaration.py`, `test_records.py`, `test_review.py` (`rg -l "app\.core|web\.antispam|blueprints\." tests`).
- Monkeypatch targets: `tests/web/test_declarations.py:279` (`monkeypatch.setattr(db, "list_cards_for_declarations", spy)`, which only works while the route calls it through the module) and `:1253` (`"app.web.blueprints.declarations.build_declaration_pdf"`).
- `get_conn`: `tests/conftest.py` (around line 437) monkeypatches `get_conn` on both `app.core.db` and `app.shared.db`. Once the re-export is gone only `app.shared.db` is patched, so every caller must reach it as an attribute (`from app.shared import db` then `db.get_conn()`), the pattern `app/customers/routes.py`, `app/pricing/routes.py` and `app/auth/decorators.py` already use. `from app.shared.db import get_conn` would bypass the test patch.
- `tests/core/db/test_connection.py::TestGetConnReExport` asserts `app.core.db.get_conn is app.shared.db.get_conn`, i.e. the re-export this task removes.

**Docs and docstrings that name `app/core` or the moved paths** (`rg -n "app\.core|app/core" app tests docs README.md CLAUDE.md --glob '!docs/factory-history/**'`): `README.md` (83, 89-90, 115, 175), `CLAUDE.md` (the `app/core/` bullet), `docs/ARCHITECTURE.md` (7-9, 36, 61-62, 71-72), `docs/CORE.md` (title, intro, `## db/`, `### db/declarations.py`, `### declarations.py`, `### declaration_pdf.py`, line 364), `docs/WEB.md` (intro lines 5-20 and the declarations rows 36, 58-65 that cite `db.<fn>`, 238, 258, 291, 331, the `CORE.md#...` link at 400), `docs/README.md:23`, `docs/DATABASE.md` (560, plus the `CORE.md` links at 245, 752), `docs/LOCAL_DEVELOPMENT.md` (82, 124-125), `docs/SIIGO_INTEGRATION.md` (192-195, 646), `docs/FRONTEND.md:390`, `docs/MIGRATION_PLAN.md` (phase 1 section, line 201), and the module docstrings of `app/shared/__init__.py`, `app/shared/db.py` (9, 13, 19, 28, 44-45), `app/orders/__init__.py`, `app/production/__init__.py`, `app/orders/service.py:10`, `app/cash_summary/service.py:12`, and the moved files themselves.

`setup.cfg` has `[mutmut] source_paths=app/core`; mutmut 3.8.1 is in `requirements-dev.txt` but no CI job runs it.

**Existing patterns to reuse** (set by #113-#116):
- `app/production/__init__.py` / `app/orders/__init__.py`: package `__init__.py` that holds only a docstring describing the domain and a "Dependency rule" paragraph, with no `import`.
- `app/production/`: `routes.py` + `service.py` + `repository.py`; callers outside the domain write `<domain>_repository.<name>` (`orders_repository`, `customers_repository`).
- `pyproject.toml`'s strict `[[tool.mypy.overrides]]` list (explicit module names, `disallow_untyped_defs = true`).
- The #116 record, `docs/factory-history/issue-116/spec.md`, for the move-and-retarget procedure.

## Requirements
1. Move with `git mv` so history follows:
   - `app/web/blueprints/declarations.py` -> `app/declarations/routes.py`
   - `app/web/blueprints/public_declaration.py` -> `app/declarations/public_routes.py`
   - `app/web/antispam.py` -> `app/declarations/antispam.py`
   - `app/core/declarations.py` -> `app/declarations/service.py`
   - `app/core/declaration_pdf.py` -> `app/declarations/pdf.py`
   - `app/core/db/declarations.py` -> `app/declarations/repository.py`
2. Add `app/declarations/__init__.py` with only a docstring, following `app/orders/__init__.py`: what the domain holds (staff screens, public form, anti-spam, service, PDF, SQL) and its dependency rule (other code may import `app.declarations`; it imports `app.shared`, `app.auth`, `app.customers`, `app.orders`, `app.production` and, from the routes, `app.web.csrf` until phase 6). No `import` statement.
3. Delete `app/core/` entirely (`app/core/__init__.py`, `app/core/db/__init__.py` and any leftover directory). No compatibility shim or re-export of the moved names is left anywhere. Delete `app/web/blueprints/__init__.py` so the empty `app/web/blueprints/` package is gone.
4. Blueprint names (`declarations`, `public_declaration`), URL rules, endpoint names, methods, permissions, the CSRF handling of `/declaracion`, the body-size limit, templates (still in `app/web/templates/`), flash and log messages, and SQL strings are unchanged. Apart from import lines, call prefixes, docstrings and comments, the moved files' diff is empty.
5. Point every caller at the new modules:
   - `get_conn`: `from app.shared import db` and `db.get_conn()` (attribute access, so the test fixture's patch on `app.shared.db` still applies). Never `from app.shared.db import get_conn`.
   - Declarations SQL and records: outside `app/declarations/`, `from app.declarations import repository as declarations_repository` and `declarations_repository.<name>`; constant imports (`from app.declarations.repository import REASON_LOSS, ...`) are fine. Inside the domain, either that alias or `from app.declarations import repository`. Functions that a test monkeypatches on the module (`list_cards_for_declarations`) keep being called through the module attribute.
   - Service: `from app.declarations import service as decl` (keeping the existing `decl` alias) or explicit name imports from `app.declarations.service`.
   - `app/web/app.py` imports the blueprints from `app.declarations.routes` and `app.declarations.public_routes`; `public_routes.py` imports `from app.declarations import antispam`; `app/production/routes.py` imports `CARD_PHOTO_SIDES`, `FILE_KINDS` from `app.declarations.routes`.
   - The callers are those listed in Context: `app/web/app.py`, `app/migrate.py`, `app/schema_migrate.py`, `app/orders/routes/{ingest,records,review}.py`, `app/production/routes.py` and the moved files.
6. Tests change only in import lines, the `db.` -> `declarations_repository.` call prefix for declarations functions (`db.get_conn` stays `db.get_conn` with `db` now `app.shared.db`), the two monkeypatch targets (`declarations_repository` module and `"app.declarations.routes.build_declaration_pdf"`), `tests/conftest.py` patching `get_conn` only on `app.shared.db`, and docstrings or comments naming moved paths. Test files stay where they are. No test is deleted, skipped or weakened, with one exception: `tests/core/db/test_connection.py::TestGetConnReExport` tests the re-export this task removes, so replace its single test with one that guards the new invariant (for example `importlib.util.find_spec("app.core") is None`), keeping the collected test count the same. Say so in the PR description.
7. In `pyproject.toml`, replace `"app.core.*"` in the strict `[[tool.mypy.overrides]]` with `"app.declarations.service"`, `"app.declarations.repository"`, `"app.declarations.pdf"` and `"app.declarations.antispam"`, keeping `disallow_untyped_defs = true`. Update the comment above it so it no longer says `core/`. Do not add the two route modules. If `antispam` needs an annotation to pass, add only the annotation.
8. In `setup.cfg`, change `source_paths=app/core` to `source_paths=app/declarations`.
9. Docs, in English:
   - `git mv docs/CORE.md docs/DOMAINS.md`. Retitle it (e.g. "Domains and shared code"), rewrite its intro so it describes `app/shared/` and the domain packages with no `app/core`, and turn the `## db/`, `### db/declarations.py`, `### declarations.py` and `### declaration_pdf.py` sections into `app/declarations/repository.py`, `service.py` and `pdf.py` sections (the `db/__init__.py` re-export row goes away). Fix every link to `CORE.md` (`README.md`, `docs/README.md`, `docs/ARCHITECTURE.md`, `docs/DATABASE.md`, `docs/WEB.md` including its `#...` anchor, `docs/SIIGO_INTEGRATION.md`, `app/shared/db.py`, `app/orders/routes/records.py`) and fix `docs/README.md`'s map row so it describes the new page.
   - `docs/ARCHITECTURE.md`: describe `app/declarations/` the way it describes `app/orders/`, drop the "until the declarations domain moves" clauses and the `app/core` wording in the intro and diagram.
   - `docs/WEB.md`: the intro says routes live in the domain packages (no `app/web/blueprints/`); the declarations rows name `app/declarations/routes.py` / `public_routes.py` and `declarations_repository.<fn>` where they cite `db.<fn>`; lines 238, 258, 291, 331 use the new paths.
   - `docs/MIGRATION_PLAN.md`: mark "Phase 1: reorganize by domain (no behavior change)" as done (heading or a one-line status note). Do not edit its "Where today's code goes" table.
   - `README.md` (83-90, 115, 175), `docs/DATABASE.md:560`, `docs/LOCAL_DEVELOPMENT.md` (82, 124-125), `docs/SIIGO_INTEGRATION.md` (192-195; correct or drop the `:98` line reference) and `docs/FRONTEND.md:390`: new paths.
   - `CLAUDE.md`: replace only the `# Project` bullet that starts with `app/core/` with one describing `app/declarations/` (staff screens `routes.py`, public form `public_routes.py`, `antispam.py`, `service.py`, `pdf.py`, `repository.py`). It may add that every blueprint now lives in its domain package. No other line of CLAUDE.md changes.
   - Module docstrings and comments listed in Context, and the moved files' own docstrings (e.g. `app/declarations/service.py` names `app/declarations/repository.py` as its data layer). `app/web/app.py`'s docstring lists the two declaration blueprints under `app/declarations/`.
   - Do not edit applied `app/migrations/*.sql`, `init.sql`, `docs/factory-history/` or the root `ARCHITECTURE_AND_POS_PLAN.md` (historical plan).
10. The PR description lists each improvement made over the issue and the docs, with its reason (at least: removing `app/web/blueprints/`, the `TestGetConnReExport` replacement, the `setup.cfg` change, the `CORE.md` rename), or says there were none. It lists as follow-ups: the `app/web/` bullet of CLAUDE.md still mentioning `app/web/blueprints/` (out of scope here), and the `declarations` <-> `production` two-way dependency (routes import each other's modules) for phase 4.

## Acceptance criteria
1. `test ! -e app/core && test ! -e app/web/blueprints && test ! -e app/web/antispam.py` succeeds. `git ls-files app/declarations` lists exactly `__init__.py`, `antispam.py`, `pdf.py`, `public_routes.py`, `repository.py`, `routes.py`, `service.py`.
2. `rg -n "app\.core|app/core" app tests docs README.md CLAUDE.md --glob '!docs/factory-history/**' --glob '!app/migrations/**'` finds nothing (the comments in applied migrations are history and stay).
3. `rg -n "web\.antispam|web/antispam|blueprints\.(declarations|public_declaration)|web/blueprints|CORE\.md" app tests docs README.md --glob '!docs/factory-history/**'` finds nothing. `rg -n "from app\.shared\.db import get_conn" app tests` finds nothing.
4. `rg -n "^(from|import) " app/declarations/__init__.py` finds nothing.
5. For each module in `app.declarations app.declarations.routes app.declarations.public_routes app.declarations.antispam app.declarations.service app.declarations.pdf app.declarations.repository app.production.routes app.orders.routes.ingest app.migrate app.schema_migrate`, `SECRET_KEY=dev .venv/Scripts/python -c "import <module>"` in a fresh process exits 0, and so does `SECRET_KEY=dev .venv/Scripts/python -c "import app.web.app"`.
6. `SECRET_KEY=dev .venv/Scripts/python -c "from app.web.app import app; print(sorted((r.rule, r.endpoint, sorted(r.methods)) for r in app.url_map.iter_rules()))"` prints the same output on the branch and on an `origin/main` checkout.
7. `git diff -M --stat origin/main...HEAD` shows the six moves and `docs/CORE.md => docs/DOMAINS.md` as renames. `git diff origin/main...HEAD -- app/web/templates app/migrations init.sql app/web/csrf.py app/web/helpers.py app/web/navigation.py` is empty.
8. `pyproject.toml`'s strict override lists `app.declarations.service`, `app.declarations.repository`, `app.declarations.pdf` and `app.declarations.antispam` and no `app.core` entry; `.venv/Scripts/python -m mypy app/` passes.
9. `.venv/Scripts/python -m pytest -q --collect-only | tail -1` reports the same count on the branch and on `origin/main`. `git diff --diff-filter=D --name-only origin/main...HEAD -- tests` is empty. `git diff origin/main...HEAD -- tests` touches only import lines, call prefixes, the monkeypatch targets, the conftest `get_conn` patch, the one `TestGetConnReExport` test, and docstrings or comments.
10. `tests/web/test_declarations.py`, `test_public_declaration.py`, `test_antispam.py`, `test_declaration_template.py`, `test_csrf.py`, `test_production.py`, `test_ingest.py`, `tests/core/test_declarations.py`, `test_declaration_pdf.py` and `tests/core/db/test_declarations.py`, `test_connection.py` pass against the job database (`TEST_POSTGRES_DB=plates_test_22`, `TEST_REQUIRE_DB=1`).
11. `rg -c "app/declarations" docs/DOMAINS.md docs/WEB.md docs/ARCHITECTURE.md README.md CLAUDE.md` reports at least one match in each file; `docs/README.md` links `DOMAINS.md`; `docs/MIGRATION_PLAN.md`'s phase 1 heading or the line under it says it is done.
12. `git diff origin/main...HEAD -- CLAUDE.md` changes only the `app/core/` bullet of `# Project`. `git diff origin/main...HEAD -- setup.cfg` shows only `source_paths=app/declarations`.
13. With the app run from the worktree on the job's port (CLAUDE.md, "Checking a page", with `TURNSTILE_BYPASS=1`): `/declaracion` renders and accepts a submission, and, logged in as admin, `/declaraciones`, a declaration detail, approve, its `/pdf` and `/produccion` detail behave as on `origin/main`. When a browser check is not possible, criterion 10 passing covers this.

## Out of scope
- Any behavior change: URLs, endpoints, permissions, CSRF or anti-spam logic, rate limits, SQL, templates, messages.
- Moving templates or `app/web/helpers.py`, `navigation.py`, `csrf.py` (phase 6).
- Splitting or rewriting the moved modules, renaming their functions or constants, introducing schemas or a new service layer.
- Breaking the `declarations` <-> `production` dependency (phase 4; follow-up only).
- Moving or renaming test files (they stay in `tests/core/` and `tests/web/`), or adding any `app.core` shim.
- Editing CLAUDE.md beyond the one `app/core/` bullet, including its `app/web/` bullet.
- Editing `docs/MIGRATION_PLAN.md`'s mapping table, applied migrations, `init.sql`, `ARCHITECTURE_AND_POS_PLAN.md`, `docs/factory-history/`, `factory/checks.sh` or `factory/config.json`.

## Assumptions
- `docs/CORE.md` is renamed to `docs/DOMAINS.md`. Reason: the issue suggests it, and a page titled "Core (`app/core/`)" makes no sense once the folder is gone.
- `app/web/blueprints/` is removed. Reason: after the move it holds only an empty `__init__.py`, and the issue's "base, not a ceiling" asks for such cleanups. The CLAUDE.md `app/web/` bullet that names it is left alone because the issue limits CLAUDE.md edits to one bullet; it goes in the PR as a follow-up.
- `TestGetConnReExport` is replaced, not kept. Reason: the issue requires removing the re-export it tests; a replacement that checks `app.core` is gone keeps the count and still guards the change.
- Acceptance criterion 1 of the issue ("finds only history") is read as excluding `docs/factory-history/` and the comments in applied `app/migrations/*.sql`. Reason: applied migrations are never edited (CLAUDE.md), as intake also noted.
- `antispam` joins the strict mypy list. Reason: it came from `app/web/` but is fully annotated and pure, like the other strict modules; the two route modules are not added, matching #116.
- `setup.cfg` points mutmut at `app/declarations`. Reason: that is exactly what `app/core` holds today, so the mutation scope does not change, and leaving a missing path would break the tool.
- The service keeps the `decl` alias at call sites. Reason: smallest diff in the moved routes and tests.
