# Rewrite the README as an up-to-date portfolio page

> Issue #68: Rewrite the README as an up-to-date portfolio page

## Summary
- Docs-only job: rewrite `README.md` in English with the eleven sections the issue lists, in order, describing only what is on `main` today.
- The current README is out of date (it says there is no admin UI for users and doesn't mention declarations, notarization, the production board or the factory), and it lists deploy secrets with an SSH user. Both problems go away.
- Replace only the intro paragraph of `docs/README.md` with two or three Spanish sentences on the permission model and the production flow.
- No code, schema, test or other doc changes. No LICENSE file.

## Context
- `README.md` (about 135 lines today): sections Stack, Local development, Web authentication, Deployment (with a "Required GitHub secrets" table naming `VPS_HOST`, `VPS_USER` = `root`, `VPS_SSH_KEY`, `ENV_FILE_B64` and the `/opt/plates` path), and Schema. Everything in it is replaced.
- `docs/README.md`: lines 1-9 are the stale intro ("Tres roles: `admin`..., `point_of_sale`..., y `production` (solo lectura)"). The rest of the file (the paragraph that starts "Esta carpeta es documentación...", the document map and the mermaid overview) stays unchanged.
- What really exists, to describe in the README:
  - Public sworn declaration: `app/web/blueprints/public_declaration.py` (`GET/POST /declaracion`), template `declaration.html`, anti-spam in `app/web/antispam.py` (Cloudflare Turnstile via `verify_turnstile`, `honeypot_triggered`, signed form token via `verify_form_token`, `is_rate_limited`), ownership cards in `app/core/db/ownership_cards.py`, consent fields `accept_data_policy` / `accept_terms` / `marketing_consent`.
  - Point of sale: `app/web/blueprints/declarations.py` (`/declaraciones`, `/declaraciones/<id>`, `approve`, `reject`, `notarized`, `notarized/undo`, `pdf`, `photos.zip`), stages in `app/core/db/declarations.py` (`to_review`, `awaiting_notarization`, `ready`, `attended`, `rejected`), flags in `app/core/declarations.py` (`FLAG_PLATE_MADE_RECENTLY`, `FLAG_CARD_OTHER_OWNER`, ...); order capture in `app/web/blueprints/ingest.py` (`/ingest`, `/ingest/pdf`, with `app/core/pdf_extract.py`, `app/core/xlsx_extract.py`, `app/core/pedido_upload.py`); cash summary `app/web/blueprints/cash_summary.py` (`/cuadre`, `/cuadre/pdf`); records `/records`.
  - Production: `app/web/blueprints/production.py` (`/produccion`, `/produccion/<id>`, `advance`, `undo`), statuses in `app/core/db/production.py` (`pending`, `in_progress`, `ready`, `delivered`), whose Spanish UI names are *Por realizar*, *En realización*, *Lista para entregar*, *Entregada* (see `docs/PRODUCTION.md`).
  - Admin: `app/web/blueprints/users.py` (`/usuarios`, `/usuarios/nuevo`, `/usuarios/roles`, ...), permission catalog `app/core/permissions.py` (admin always holds every permission; other roles get permissions through `role_permissions`), prices `/precios`, customers `/customers`, inventory `/inventory`, duplicate review `/review`.
  - Architecture: blueprints in `app/web/blueprints/`, pure typed logic in `app/core/`, one module per table in `app/core/db/`, `init.sql` + `app/migrations/*.sql` applied by `python -m app.schema_migrate`, `docker-compose.yml`, `web.Dockerfile`, gunicorn, PostgreSQL 16.
  - Quality and CI: `pyproject.toml` (ruff; mypy with `disallow_untyped_defs` on `app.core.*`), `tests/` with a real Postgres, `pytest-cov` in `.github/workflows/test.yml`, `.github/workflows/codeql.yml`, `.github/dependabot.yml`, `.github/workflows/deploy.yml` (reuses `test.yml`, deploys only from `main` through the `production` environment, actions pinned to SHAs), `factory/checks.sh`. `docs/ARCHITECTURE.md` already documents the deploy secrets, so nothing moves there.
  - Local setup: `.env.example`, `app/web/hash_password.py`, the one-off `AUTH_USERS`/`AUTH_ROLES` backfill in `app/schema_migrate.py`, the Compose `$` → `$$` gotcha, `docs/LOCAL_DEVELOPMENT.md`.
  - Other files to link: `AGENTS.md`, `SECURITY.md`, and every `docs/*.md`: `ARCHITECTURE.md`, `BACKUPS.md`, `CORE.md`, `DATABASE.md`, `LOCAL_DEVELOPMENT.md`, `PRODUCTION.md`, `README.md`, `WEB.md`.

## Requirements
1. `README.md` is entirely rewritten in English. It has these sections in this order: title plus a two-to-three sentence pitch (internal plate-production and point-of-sale app for INGSE, Colombia, replacing a spreadsheet workflow, in production), `Features`, `How it works`, `Tech stack`, `Quality`, `CI/CD`, `Local development`, `Software factory`, `Documentation`, `Security`, `License / status`.
2. `Features` is grouped by who uses it: the public sworn-declaration form, point of sale, production and admin. It covers every item the issue lists for each group and quotes Spanish UI names where useful (for example *Por realizar* → *En realización* → *Lista para entregar* → *Entregada*, «Cuadre», «Precios»).
3. `How it works` has one mermaid block (`flowchart` or `stateDiagram-v2`) for the order lifecycle from declaration to delivery: submission, review, approval or rejection, notarization, order capture, the production statuses, delivery. After it comes a short architecture paragraph: Flask blueprints, `app/core` pure typed logic, `app/core/db` one module per table, PostgreSQL 16, gunicorn, Docker Compose, the uploads volume.
4. `Quality` names ruff, mypy (strict on `app/core`), pytest against a real Postgres, coverage in CI, CodeQL, Dependabot and the `factory/checks.sh` gate.
5. `CI/CD` says: tests run on every PR; deploy to the VPS happens only from `main` after the tests pass, through a protected environment; actions are pinned to commit SHAs. It links `docs/ARCHITECTURE.md`. It does not contain host names, IPs, usernames, secret names or values, or server paths (`/opt/...`).
6. `Local development` gives the short setup: `cp .env.example .env`, `docker compose up -d`, `python -m app.schema_migrate` (run in the `web` container as `docker compose exec web python -m app.schema_migrate`), the first admin created with `python -m app.web.hash_password` plus the one-off `AUTH_USERS`/`AUTH_ROLES` backfill that only works on a brand-new database, and after that users are managed at `/usuarios`. It also covers the Compose `$$` escaping gotcha and how to run the tests (`pytest`, which needs `POSTGRES_PASSWORD` and the local `db` on port 5433). It links `docs/LOCAL_DEVELOPMENT.md` for the rest.
7. `Software factory` is two or three sentences: work is planned as GitHub issues and built by agents into PRs, and a human always reviews and merges. It links `AGENTS.md`.
8. `Documentation` links `docs/README.md` and every other `docs/*.md` file listed in Context.
9. `Security` links `SECURITY.md`.
10. `License / status` says this is a private business's production code published for portfolio purposes, with no license granted. No LICENSE file is added.
11. The lowercase string `root` appears nowhere in `README.md`, not even in phrases like "repo root" or "root directory".
12. Every route, screen, command and file name in `README.md` exists in the repo as of this branch. Nothing is claimed that isn't in the code (for example no "SMS notifications" or features from the roadmap).
13. Lines 1-9 of `docs/README.md` (the intro before "Esta carpeta es documentación") are replaced with two or three sentences of Spanish prose. They say permissions are granted per role, roles are managed in `/usuarios`, `admin` always has every permission, and they summarize the production flow (declaration → order → *Por realizar* → ... → *Entregada*). The heading and the rest of the file are unchanged.
14. No other file changes.

## Acceptance criteria
1. The sections are there in order: `grep -nE "^## " README.md` lists Features, How it works, Tech stack, Quality, CI/CD, Local development, Software factory, Documentation, Security, License / status, in that order, and the file starts with a `# ` title followed by the pitch.
2. Every relative link resolves. For each target printed by `grep -oE "\]\([^)#:]+" README.md | sed 's/](//'`, `test -e <target>` succeeds from the worktree's top directory. Do the same for `docs/README.md`, resolving relative to `docs/`.
3. `grep -nE "root|VPS_HOST|VPS_USER|VPS_SSH_KEY|VPS_SSH_FINGERPRINT|ENV_FILE_B64|/opt/|[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}" README.md` prints nothing.
4. Every route the README names (`/declaracion`, `/declaraciones`, `/ingest`, `/cuadre`, `/produccion`, `/usuarios`, `/precios`, `/customers`, `/inventory`, `/review`, `/records`, ...) matches a decorator in `grep -rhE "\.(get|post|route)\(" app/web/blueprints app/web/app.py`, and every file path it names passes `test -e`.
5. The README contains exactly one fenced mermaid block, and its first line is `flowchart ...` or `stateDiagram-v2`. Node labels with special characters (`→`, parentheses, accents) are quoted. It renders when the branch's README is viewed on GitHub.
6. `README.md` no longer contains "no admin UI" (`grep -ni "no admin UI" README.md` prints nothing), and it mentions `/usuarios`.
7. In `docs/README.md`, `git diff origin/main -- docs/README.md` only touches the intro lines before "Esta carpeta es documentación". The new text is Spanish, no longer says `production` is read-only, and mentions roles with permissions and the production flow.
8. `git diff --name-only origin/main` lists only `README.md` and `docs/README.md` (plus the factory history files the factory writes itself).

## Out of scope
- Screenshots, GIFs or badges that need external services.
- Adding a LICENSE file or choosing a license.
- Any code, template, schema, workflow or test change.
- Editing other docs: the rest of `docs/README.md` (including its document map, which lacks `BACKUPS.md`), `docs/ARCHITECTURE.md`, `docs/LOCAL_DEVELOPMENT.md`, `AGENTS.md`, `SECURITY.md`, `PLAN_ARQUITECTURA_Y_POS.md`.
- Moving the secrets table anywhere: `docs/ARCHITECTURE.md` already documents the secrets.
- A full schema section in the README: link `docs/DATABASE.md` instead.

## Assumptions
- No new automated test for the README. It is prose, and the link and grep checks above are manual commands, so no test file is added (the issue says "no code changes").
- The `/opt/` and `VPS_SSH_FINGERPRINT` checks go into criterion 3 beyond the issue's grep, because the issue bans server paths and secret names in general.
- The README may say "VPS" generically (the issue's own CI/CD wording uses it). Only the specific host, user, secret and path details are banned.
- The test command in `Local development` is plain `pytest` (with `-n auto` optional), with the env it needs. The full per-worktree database details stay in `docs/LOCAL_DEVELOPMENT.md` and `AGENTS.md`.
- `PLAN_ARQUITECTURA_Y_POS.md` may be linked under `Documentation` as historical context, but it is not required. The issue only asks for `docs/` links.
