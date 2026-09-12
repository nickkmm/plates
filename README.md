# plates

Plate production tracking for INGSE (Colombia). Replaces a spreadsheet-based
workflow with a Flask + PostgreSQL app running on a VPS, deployed automatically
on every push to `main` via GitHub Actions.

For architecture, database schema, and internal request-flow reference docs
(with diagrams), see [`docs/README.md`](docs/README.md).

## Stack

- Flask web app (`app/web/`) behind gunicorn, gated by session login:
  **Registrar** (data-entry form), **Inventario** (stock per material),
  **Registros** (order history + per-order detail), **Revisión** (flagged
  possible duplicates).
- Shared business logic (`app/core/`) used by both the web app and the
  one-off Excel migration (`app/migrate.py`): normalization, validation,
  duplicate detection, inventory calculation.
- PostgreSQL 16 in Docker (single `db` service via `docker-compose.yml`).
  Schema in `init.sql` (applied on first volume initialization).
- CI/CD in `.github/workflows/deploy.yml`: SSH into the VPS, pull `main`,
  rebuild and restart the stack.

## Local development

```sh
cp .env.example .env   # then set a real POSTGRES_PASSWORD
docker compose up -d
docker compose exec db psql -U plates_user -d plates -c '\dt'
```

## Web authentication

The web app is gated by a session-based login page (`/login`). Configure two
env vars (see `.env.example`):

- `SECRET_KEY` — signs session cookies. Generate with
  `python -c "import secrets; print(secrets.token_hex(32))"`. Required.
- `AUTH_USERS` — comma-separated `username:passwordhash` pairs. Hash a password
  with `python -m app.web.hash_password 'the-password'`, then set e.g.
  `AUTH_USERS=admin:<printed-hash>`.
  **Docker Compose gotcha:** the generated hash contains `$` (e.g.
  `scrypt:32768:8:1$salt$hash`). Compose interpolates `$VAR` in files loaded
  via `env_file:`, so a literal `$` in `.env` must be escaped as `$$` or
  Compose silently strips it (and everything that looks like a variable name
  right after it), corrupting the hash. Double every `$` before pasting it in.
- `COOKIE_SECURE=1` in production (HTTPS) so the cookie is marked `Secure`.

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

- `production_orders` — one row per production order.
- `plates` — plates within an order (`plate_number` may be NULL for batch
  replacements; multiple `plate_type` rows per order are normal).
- `inventory_categories` — per-category stock tracking.
- `blank_preparations` — "alistamiento" events (blanks prepared/received),
  feeds `inventory_categories.prepared`.
