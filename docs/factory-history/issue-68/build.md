STATUS: READY

# Build: Rewrite the README as an up-to-date portfolio page

## Summary
- `README.md` is rewritten in English with the eleven sections in the order the spec gives, describing only what is on `main`.
- Features are grouped by user (public form, point of sale, production, admin) and quote the Spanish UI names; a mermaid flowchart shows the declaration-to-delivery lifecycle.
- The deploy secrets table, SSH user and `/opt/...` path are gone; CI/CD links `docs/ARCHITECTURE.md`.
- The stale intro of `docs/README.md` is replaced with Spanish prose on per-role permissions and the production flow.
- Docs only, draft PR #102.

## Changes
- `README.md`: full rewrite. Sections: title + pitch, Features, How it works (one `flowchart TD` mermaid block, every label quoted, plus an architecture paragraph), Tech stack, Quality, CI/CD, Local development, Software factory, Documentation, Security, License / status.
- `docs/README.md`: only the intro paragraph under the heading (old lines 3-10, which said `production` was read-only) is replaced with three Spanish sentences. The heading, the "Esta carpeta es documentación..." paragraph, the document map and the mermaid overview are unchanged.

## Acceptance criteria
1. [x] Sections in order: `grep -nE "^#{1,2} " README.md` prints `# plates` (line 1, pitch follows), then `## Features`, `## How it works`, `## Tech stack`, `## Quality`, `## CI/CD`, `## Local development`, `## Software factory`, `## Documentation`, `## Security`, `## License / status`.
2. [x] Links resolve: the `test -e` loop over `grep -oE "\]\([^)#:]+" README.md` printed `ok` for all 12 targets (docs/ARCHITECTURE.md, docs/LOCAL_DEVELOPMENT.md, AGENTS.md, docs/README.md, docs/DATABASE.md, docs/CORE.md, docs/WEB.md, docs/PRODUCTION.md, docs/BACKUPS.md, SECURITY.md); the same loop in `docs/` over `docs/README.md` printed no MISSING.
3. [x] `grep -nE "root|VPS_HOST|VPS_USER|VPS_SSH_KEY|VPS_SSH_FINGERPRINT|ENV_FILE_B64|/opt/|<IPv4>" README.md` prints nothing.
4. [x] Routes named (`/declaracion`, `/declaraciones`, `/ingest`, `/ingest/pdf`, `/cuadre`, `/cuadre/pdf`, `/records`, `/produccion`, `/produccion/<order_id>`, `/usuarios`, `/usuarios/roles`, `/precios`, `/customers`, `/inventory`, `/review`) all match decorators in `grep -rhE "\.(get|post|route)\(" app/web/blueprints app/web/app.py`. File paths named (app/web/blueprints, app/core, app/core/db, init.sql, app/migrations, docker-compose.yml, web.Dockerfile, pyproject.toml, factory/checks.sh, .github/workflows/test.yml, .github/workflows/deploy.yml, .env.example, plus the linked docs) all pass `test -e`. Spanish labels were checked against the code: stage labels and flag labels in `app/web/blueprints/declarations.py`, nav labels in `app/web/navigation.py`, «Perdí mis placas» and Ley 1581 in the form/core, production status names in `docs/PRODUCTION.md`.
5. [x] Exactly one ```` ```mermaid ```` block (`grep -c` = 1), first line `flowchart TD`, every node and edge label quoted (labels contain accents, `/` and commas). Syntax is standard GitHub mermaid; not visually checked in a browser (no page to check in this docs-only job).
6. [x] `grep -ni "no admin UI" README.md` prints nothing; `/usuarios` appears 3 times.
7. [x] `git diff origin/main -- docs/README.md` has a single hunk that only replaces the intro before "Esta carpeta es documentación"; the new text is Spanish, drops "solo lectura", and covers roles with permissions in `/usuarios`, `admin` holding every permission, and declaración → pedido → *Por realizar* → ... → *Entregada*.
8. [x] `git diff --name-only origin/main` lists only `README.md` and `docs/README.md`.

## Validation
- `bash factory/checks.sh` from the worktree: passed (exit 0; ruff, mypy, pytest 648 passed / 1188 skipped, shell and factory checks).
- Database tests are skipped locally: Docker Desktop is not running on this machine, so the local Postgres on 5433 is unreachable (a run with `TEST_REQUIRE_DB=1 TEST_POSTGRES_DB=plates_test_14` errored on connection only). This change touches no code, so the DB tests are unaffected; the gate will run them against Postgres.
- Link, grep, route and file checks listed above: passed.

## Log
### Round 1
- Ran the venv setup from AGENTS.md in the worktree.
- Read the blueprints, `app/core` stage/flag/status constants, templates, `pyproject.toml`, workflows, `docker-compose.yml` and `docs/` to describe only what exists.
- Rewrote `README.md` and the `docs/README.md` intro, ran the acceptance checks and `factory/checks.sh`, committed, pushed, and opened draft PR #102 (no "Generated with" line, per CLAUDE.md).
