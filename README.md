# plates

Plate production tracking for INGSE (Colombia). Replaces a spreadsheet-based
workflow with a Flask + PostgreSQL app running on a VPS, deployed automatically
on every push to `main` via GitHub Actions.

For architecture, database schema, and internal request-flow reference docs
(with diagrams), see [`docs/README.md`](docs/README.md).

## Stack

- Flask web app (`app/web/`) behind gunicorn (connection-pooled via
  `psycopg_pool`), gated by session login: **Registrar** (full order
  capture — customer, payment, per-plate pricing), **Precios** (tariff
  editor, admin-only), **Cuadre** (daily cash-reconciliation report, with
  PDF export), **Inventario** (stock per material), **Registros** (order
  history + per-order detail), **Revisión** (flagged possible duplicates).
  Routes live one blueprint per domain under `app/web/blueprints/`.
- Shared business logic (`app/core/`) used by both the web app and the
  one-off Excel migration (`app/migrate.py`): normalization, validation,
  duplicate detection, inventory calculation, plus `app/core/db/` (one
  module per table) and `app/core/cash_summary.py` (the cuadre
  aggregation).
- PostgreSQL 16 in Docker (single `db` service via `docker-compose.yml`).
  Schema in `init.sql` (applied on first volume initialization); schema
  changes since then are applied by `python -m app.schema_migrate`
  (`app/migrations/*.sql`), run automatically on every deploy.
- CI/CD in `.github/workflows/deploy.yml`: SSH into the VPS, pull `main`,
  rebuild and restart the stack, then run the migration runner.
- Sentry (optional, `SENTRY_DSN` env var) for error monitoring; Docker log
  rotation configured in `docker-compose.yml`.

See [`docs/README.md`](docs/README.md) and `PLAN_ARQUITECTURA_Y_POS.md`
for the full history of the POS feature (customer capture, pricing, cash
reconciliation) that built on top of the original plate-production
tracker.

## Local development

```sh
cp .env.example .env   # then set a real POSTGRES_PASSWORD
docker compose up -d
docker compose exec db psql -U plates_user -d plates -c '\dt'
```

**Running migrations locally**: `init.sql` only applies the first time a
Postgres volume is created — rebuilding `web`/`db` against an
already-existing local volume does **not** re-run it. After any rebuild,
bring the schema up to date (and run any user backfill still pending) with:

```sh
docker compose exec web python -m app.schema_migrate
```

Safe to run any number of times — it records each applied step in
`schema_migrations` and skips what's already there. `deploy.yml` already
runs this automatically after every production deploy.

## Web authentication

The web app is gated by a session-based login page (`/login`). Login is
**DB-backed**: `app/web/auth.py` checks the `users` table (username,
password hash, role, active) on every request, not env vars.

- `SECRET_KEY` — signs session cookies. Generate with
  `python -c "import secrets; print(secrets.token_hex(32))"`. Required,
  no default.
- `COOKIE_SECURE=1` in production (HTTPS) so the cookie is marked `Secure`.
- `SENTRY_DSN` — optional; if unset, Sentry is never initialized (dev/test
  never send anything).

**Provisioning users** (`AUTH_USERS`/`AUTH_ROLES` — historical, one-time
only): on a **brand-new** database, `python -m app.schema_migrate` reads
these two env vars once and backfills `users` from them — comma-separated
`username:passwordhash` (`AUTH_USERS`) and `username:role` (`AUTH_ROLES`,
defaults to `admin` if a username has no entry). Hash a password with
`python -m app.web.hash_password 'the-password'`.
**Docker Compose gotcha:** the generated hash contains `$` (e.g.
`scrypt:32768:8:1$salt$hash`). Compose interpolates `$VAR` in files loaded
via `env_file:`, so a literal `$` in `.env` must be escaped as `$$` or
Compose silently strips it (and everything that looks like a variable name
right after it), corrupting the hash. Double every `$` before pasting it
in. **This backfill only ever runs once** (recorded in
`schema_migrations`) — on an already-migrated database (including
production), adding/editing `AUTH_USERS`/`AUTH_ROLES` does **nothing**;
add or update a user by inserting/updating a row in `users` directly
(there is no admin UI for this yet).

`/healthz` stays open for liveness probes. Sessions last 12 hours.

## Deployment

Push to `main`. The workflow connects to the VPS, syncs `/opt/plates` to the
latest `main`, writes `.env` from the `ENV_FILE_B64` secret, and runs
`docker compose up -d --build`.

### Required GitHub secrets

| Secret         | Description                                  |
|----------------|----------------------------------------------|
| `VPS_HOST`     | VPS IP address                               |
| `VPS_USER`     | SSH user (`root`)                            |
| `VPS_SSH_KEY`  | Private key for the Actions → VPS deploy key |
| `ENV_FILE_B64` | base64-encoded contents of the prod `.env`   |

The VPS uses its own read-only **deploy key** registered on this repo to pull
the private code.

## Schema

- `production_orders` — one row per production order, plus the full POS
  header (customer, receipt, payment method, tracking/claim numbers,
  dates, dispatch/receipt names, notes).
- `plates` — plates within an order (`plate_number` may be NULL for batch
  replacements; multiple `plate_type` rows per order are normal), plus
  per-plate `unit_price`/`reason`/`vehicle_brand`.
- `customers` — sale customers (same entity as the historical
  `requested_by` text), one row per canonical name.
- `users` — login accounts (replaces the old `AUTH_USERS`/`AUTH_ROLES`
  env-var-only setup — see "Web authentication" above).
- `plate_type_prices` — suggested tariff per plate type, editable at
  `/precios`.
- `inventory_categories` — per-category stock tracking.
- `blank_preparations` — "alistamiento" events (blanks prepared/received),
  feeds `inventory_categories.prepared`.

Full schema reference (ER diagram, every column, data quirks) in
[`docs/DATABASE.md`](docs/DATABASE.md).
