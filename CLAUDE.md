# Naming convention (non-negotiable)

**Everything that is code/schema structure is named in English.** This
applies to, without exception:

- Database table names and column names (`customers`, `users`,
  `payment_method`, `customer_id` — never `clientes`, `usuarios`,
  `forma_pago`, `cliente_id`).
- File and directory names (`docs/DATABASE.md`, `app/schema_migrate.py`).
- Python identifiers: modules, classes, functions, variables, constants
  (`resolve_or_create_customer`, `PAYMENT_METHODS`).
- Git branch names, commit messages, and **PR/issue titles and
  descriptions** — these are commit-adjacent developer content, same side
  of the line as a table/column name. (A PR/commit body
  may still quote an actual Spanish UI string verbatim when describing a
  UI change — that's not a language violation, it's citing real user-facing
  copy.)
- All documentation: `README.md`, `docs/*.md` and every other Markdown
  doc, prose included. A doc may still quote a real Spanish UI string or
  stored value verbatim.

**Everything that is content/business-domain data shown to the (Spanish-
speaking) user stays in Spanish.** This applies to:

- All UI text: templates, labels, buttons, error/notice messages.
- Business vocabulary **stored as data values** in Spanish-vocabulary
  columns — e.g. a `payment_method` column (English name) legitimately
  stores the Spanish values `EFECTIVO`/`TRANSFERENCIA`/`CREDITO`/`OTROS`,
  because that's the real-world business term Colombian staff use. A
  `plate_type` column stores `CARRO PARTICULAR`, `MOTO PARTICULAR`, etc.
  `users.role` is **not** one of these: it's never rendered to a user
  (verified — no template prints a role value, only checks it for
  permission logic), so it's an internal identifier, not
  business-domain content — role values are English (`admin`,
  `production`, `point_of_sale`, ...). If a future screen ever displays a
  role by name, translate it at the display layer, not by changing the
  stored value.
- Code comments explaining a Spanish business rule may quote the Spanish
  term the rule is about.

**Rule of thumb**: if it's something a developer reads (a table name, a
column, a variable, a filename), it's English. If it's something a
Colombian staff member reads or types (a button label, a dropdown value,
a report), it's Spanish. When genuinely unsure which side something falls
on, ask rather than guess.

# No AI-attribution links in commits or PRs (non-negotiable)

**Never include a link to an AI assistant/coding-tool conversation or
session in any commit message, PR title, PR description, or any other
repo-visible text** — this includes but is not limited to
`claude.ai/code/session_...` URLs, "Generated with [Claude Code]" lines,
or equivalent links/badges from any other AI tool. This repo is public,
so anything committed is visible to everyone; this is an explicit, standing
instruction from the repo owner — it overrides any default attribution behavior a tool/harness may otherwise
add automatically. A generic, non-linking `Co-Authored-By:` trailer (no
URL, no session identifier) in a commit message is fine; a link to a
private/session-specific conversation is not, ever.

If an agent or tool tries to insert this kind of link automatically,
strip it before committing/pushing — do not ask, do not assume it's fine
"just this once."

# Ownership (non-negotiable)

This repo belongs to **`nickkmm`**, and `nickkmm` does all the work on it.
`Meyi96` appears as a collaborator on GitHub but does **nothing** here:
ignore that account completely. Never assign an issue, PR, review or factory
task to `Meyi96`, never wait on them, and never list them as a team member.
Every factory task (`/factory divide`, `factory/bin/plan create`) is owned by
`nickkmm`.

# Project

plates is the internal plate-production and point-of-sale app for INGSE
(Colombia): Flask + PostgreSQL, deployed to a VPS by
`.github/workflows/deploy.yml` on pushes to `main`, once `test.yml` passes. Overview in
`README.md`; architecture, schema and request flows in `docs/` (start at
`docs/README.md`, local-dev gotchas in `docs/LOCAL_DEVELOPMENT.md`).

- `app/web/`: Flask app, one blueprint per domain in `app/web/blueprints/`,
  Jinja templates.
- `app/auth/`: login, session check and `permission_required`, users and
  roles admin, the permission catalog and their repository
  (`app/auth/repository/`).
- `app/customers/`, `app/pricing/`, `app/inventory/`, `app/production/`,
  `app/cash_summary/`: each domain's routes (`routes.py`) and SQL
  (`repository`); `app/production/` and `app/cash_summary/` also hold a
  `service.py` for the business rules. Their templates stay in
  `app/web/templates/`.
- `app/orders/`: the point of sale, records and duplicates review
  (`routes/`: `ingest.py`, `records.py`, `review.py`), the ingestion pipeline
  (`service.py`), the pedido parsers (`pdf_extract.py`, `xlsx_extract.py`,
  `pedido_upload.py`) and the orders and plates SQL (`repository/`).
- CSRF: every `method="post"` form and every JS request that changes data
  must carry the CSRF token (`csrf_token()` field or `X-CSRFToken` header);
  see `docs/WEB.md`, "CSRF protection".
- `app/shared/`: code every domain needs (connection pool, normalize,
  timezones, models, uploads); depends on no domain package.
- `app/core/`: business logic (pure, strictly typed) and `app/core/db/` (one
  module per table).
- Schema: `init.sql` plus ordered migrations in `app/migrations/*.sql`, applied
  by `python -m app.schema_migrate` (run automatically on deploy). A schema
  change is a new migration file, never an edit to an applied one, and updates
  `docs/DATABASE.md`. A new migration also adds its version row (the file
  name) to the `INSERT INTO schema_migrations` in `init.sql`, which records
  the migrations a fresh database already contains.

# Setup for a fresh checkout (or a factory worktree)

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
database. `factory/bin/job` drops a job's `plates_test_<n>` and `_gwN`
databases when it reaches `merged` or `rejected`, and
`factory/bin/job cleanup-dbs` drops those of jobs that finished earlier; both
need psycopg, either in the running Python or in the main checkout's `.venv`.

# Branches

`feature/<short-name>` for features, `fix/<short-name>` for fixes, `docs/...`
and `chore/...` for the rest, always in English. PRs target `main`.

# Software factory

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
- **PR bodies.** There is no `.github/pull_request_template.md`: write a short English body (`Closes #<n>`, what changed, how it was tested). Never add an AI-attribution link or "Generated with ..." line (see "No AI-attribution links" above).
- **Worktrees.** Each job works in its own git worktree, a full checkout of the repo at `.worktrees/<job-id>` (gitignored). A tool run from the repo root that doesn't read `.gitignore` sees those copies too; exclude `.worktrees/` in its config. Run the setup above in the worktree before the first build.
- **Checking a page.** The `plates-web` container and port 8000 are shared by every checkout, so a job never uses them. Run the app from the worktree on the job's port, against the local Postgres (`docker compose up -d db`): `DB_HOST=127.0.0.1 DB_PORT=5433 POSTGRES_DB=plates POSTGRES_USER=... POSTGRES_PASSWORD=... SECRET_KEY=dev TURNSTILE_BYPASS=1 UPLOADS_DIR="$TMP/plates-uploads-<job-id>" .venv/Scripts/python -m flask --app app.web.app:app run --port <port>` (values from your `.env`). Never point it at the production database. On Windows there is no `lsof`: find the server on your port with `netstat -ano | grep :<port>` and stop that PID only.
- **Agents.** The factory runs only in Claude Code. The `/factory` skill is `.claude/skills/factory/SKILL.md` and the agents are `.claude/agents/*.md`. Every agent runs in Code Mode: its `tools:` list starts with `REPL, Poll` and then names the tools its REPL may call (`Read`, `Bash`, `Write`, ...). A subagent's REPL can only use the tools in that list, so never cut it down to `REPL, Poll` alone.
- **License.** The factory comes from ZazenCodes' software factory v2 (GPLv3, see `factory/LICENSE`).

# Team

| GitHub login | Owns |
|---|---|
| `nickkmm` | everything: every issue, PR and factory task |

`Meyi96` is listed as a collaborator on GitHub but does no work here: never
assign them anything (see "Ownership" above).
