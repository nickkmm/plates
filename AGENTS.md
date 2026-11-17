# AGENTS.md

Read `CLAUDE.md` first: its rules apply to every agent and every factory job,
without exception. In short:

- Code and schema names (tables, columns, files, identifiers), branch names,
  commit messages, and PR titles and bodies are in **English**.
- Everything a staff member reads (templates, labels, messages, dropdown
  values, `docs/*.md` prose) is in **Spanish**.
- **No AI-attribution links** in commits, PR titles or PR bodies: no
  `claude.ai/code/session_...` URLs, no "Generated with ..." lines or badges.
  A plain `Co-Authored-By:` trailer without a URL is fine.

## Project

plates is the internal plate-production and point-of-sale app for INGSE
(Colombia): Flask + PostgreSQL, deployed to a VPS by
`.github/workflows/deploy.yml` on pushes to `main`, once `test.yml` passes. Overview in
`README.md`; architecture, schema and request flows in `docs/` (start at
`docs/README.md`, local-dev gotchas in `docs/LOCAL_DEVELOPMENT.md`).

- `app/web/`: Flask app, one blueprint per domain in `app/web/blueprints/`,
  Jinja templates.
- CSRF: every `method="post"` form and every JS request that changes data
  must carry the CSRF token (`csrf_token()` field or `X-CSRFToken` header);
  see `docs/WEB.md`, "Protección CSRF".
- `app/core/`: business logic (pure, strictly typed) and `app/core/db/` (one
  module per table).
- Schema: `init.sql` plus ordered migrations in `app/migrations/*.sql`, applied
  by `python -m app.schema_migrate` (run automatically on deploy). A schema
  change is a new migration file, never an edit to an applied one, and updates
  `docs/DATABASE.md`. A new migration also adds its version row (the file
  name) to the `INSERT INTO schema_migrations` in `init.sql`, which records
  the migrations a fresh database already contains.

## Setup for a fresh checkout (or a factory worktree)

```sh
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # Windows
# .venv/bin/python -m pip install -r requirements-dev.txt     # Linux/macOS
```

`factory/checks.sh` uses `.venv` when it exists. Database tests need the local
Postgres from `docker compose up -d db` (host port 5433) and
`POSTGRES_PASSWORD` set in the environment; without them they skip, and CI
runs them. In a factory worktree, run them against the job's own database
so other jobs' migrations don't leak in: `TEST_POSTGRES_DB=plates_test_<n>`
(`<n>` is the job number, `004` → `plates_test_4`), the same one the gate
uses. The gate takes the credentials from the main checkout's `.env` and
stops if Postgres isn't up. The suite can run in parallel with
`pytest -n auto`; each worker then uses its own `<TEST_POSTGRES_DB>_gwN`
database.

## Branches

`feature/<short-name>` for features, `fix/<short-name>` for fixes, `docs/...`
and `chore/...` for the rest, always in English. PRs target `main`.

<!-- factory:start -->
## Software factory

`/factory run` builds every open GitHub issue labeled `factory` **and assigned to you** through intake → spec → build → gate → review → approve, one PR per issue. A person always reviews and merges the PR: the factory never merges (`auto_merge` is `false` in `factory/config.json`, and the approver always escalates). Never turn it on: merging to `main` deploys to production. State is in `factory/jobs/` (gitignored, local to your machine); `factory/bin/job` is its only writer. Checks: `factory/checks.sh`. Dashboard: `cd factory && python -m http.server`, then open http://localhost:8000/dashboard.html (stop the `plates-web` container first, or use another port: it also listens on 8000).

| Command | What it does |
|---|---|
| `/factory run` | release the issues whose dependencies are merged, then build your queue (3 at a time) |
| `/factory run <n>` | build issue `<n>` now (it must be assigned to you) |
| `/factory divide <#n, file or text>` | split a large request into PR-sized issues, each with an owner; shows the plan before creating anything |
| `/factory <anything>` | ask about jobs, give feedback on a PR waiting for you, or file a new feature |

- **Who builds what.** Every factory issue is assigned to one person, and each person's factory only takes their own issues, so two people never build the same issue. Never take an issue assigned to someone else.
- **Order.** A task that needs another one says `Depends on #n` and stays unlabeled; `/factory run` labels it once `#n` is closed by a merged PR.
- **Record.** Each PR carries the job's record (intake, spec, build, reviews, checks, decision) in `docs/factory-history/issue-<n>/`. Only the factory writes there.
- **Branches.** The factory pushes `factory/<job-id>` branches and opens PRs against `main`; this is the only exception to the branch-name rule above. Merging a PR deploys to production, so the human review is the last line of defence.
- **PR bodies.** There is no `.github/pull_request_template.md`: write a short English body (`Closes #<n>`, what changed, how it was tested). Never add an AI-attribution link or "Generated with ..." line (see the top of this file).
- **Worktrees.** Each job works in its own git worktree, a full checkout of the repo at `.worktrees/<job-id>` (gitignored). A tool run from the repo root that doesn't read `.gitignore` sees those copies too; exclude `.worktrees/` in its config. Run the setup above in the worktree before the first build.
- **Checking a page.** The `plates-web` container and port 8000 are shared by every checkout, so a job never uses them. Run the app from the worktree on the job's port, against the local Postgres (`docker compose up -d db`): `DB_HOST=127.0.0.1 DB_PORT=5433 POSTGRES_DB=plates POSTGRES_USER=... POSTGRES_PASSWORD=... SECRET_KEY=dev TURNSTILE_BYPASS=1 UPLOADS_DIR="$TMP/plates-uploads-<job-id>" .venv/Scripts/python -m flask --app app.web.app:app run --port <port>` (values from your `.env`). Never point it at the production database. On Windows there is no `lsof`: find the server on your port with `netstat -ano | grep :<port>` and stop that PID only.
- **Skill copies.** The `/factory` skill lives in `.agents/skills/factory/SKILL.md`; `.claude/skills/factory/SKILL.md` is a plain copy of it (no symlinks on Windows). Edit the first and copy it over; `factory/checks.sh` fails when they differ.
- **Antigravity.** Use the App or the CLI (custom subagents aren't in the IDE yet), from the repo root: `agy --model <id>`, with an id from `agy models` (Gemini 4 Argon if your plan has it, otherwise Gemini 3.8 Flash), then the same `/factory` commands. The agents in `.agents/agents/` are generated from `.claude/agents/` by `factory/bin/sync-agents`; edit the `.claude/agents/` file and run it, never edit `.agents/agents/` by hand.
- **License.** The factory comes from ZazenCodes' software factory v2 (GPLv3, see `factory/LICENSE`).
<!-- factory:end -->

## Team

| GitHub login | Owns |
|---|---|
| `nickkmm` | everything: every issue, PR and factory task |

`Meyi96` is listed as a collaborator on GitHub but does no work here: never
assign them anything (see `CLAUDE.md`, "Ownership").
