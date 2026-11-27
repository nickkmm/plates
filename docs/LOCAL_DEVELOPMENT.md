# Local development

[← back to the index](README.md)

For the basic quickstart (`docker compose up -d`, generating auth hashes)
see the root [`README.md`](../README.md) — here are only the gotchas that
README does not cover.

## Infra gotchas

- **Fixed container names and ports**: `plates-db-1` (host `5433`) and
  `plates-web` (host `8000`). Only **one** instance of each can run at a
  time on a given machine — if you need to bring the project up from two
  different worktrees/branches in parallel (e.g. two agents working at the
  same time), they have to coordinate so they do not step on the same
  container/port.
- **`DB_HOST` must be `plates-db-1`, never `db`** — see the real
  name-collision incident with the "informes" stack in
  [`ARCHITECTURE.md`](ARCHITECTURE.md#deployment-docker-compose).
- If `docker compose up -d --build web` fails with `Conflict: container
  name "/plates-web" is already in use`, it is almost always a stopped
  container left over from a previous run: `docker rm -f plates-web` and
  retry.
- The `edge` network (`informes_informes_net`) is `external: true` — it
  has to be created once by hand in a new environment (`docker network create
  informes_informes_net`) if the neighboring stack has not already created it.

## Dozzle (web log viewer)

`dozzle` service in `docker-compose.yml` — shows live, from the
browser, the same logs that `docker logs <container>` would give over SSH.
It needs a users file that is **never committed** (gitignored,
`dozzle_users.yml`, one per environment):

```sh
docker run --rm amir20/dozzle:v11.1.1 generate admin > dozzle_users.yml
# asks for the password interactively (it never ends up in the bash history)
docker compose up -d dozzle
```

In production it is reached at `https://placas.ingse.co/dozzle/` (proxied by
the shared nginx of the "informes" stack — the `location /dozzle/` block
lives in `/opt/informes/nginx.conf`, **outside** this repo, added by
hand just like the `cargador.ingse.co` block). Locally, directly at
`http://127.0.0.1:8081/dozzle/`.

## Testing (`tests/conftest.py`)

- **The test database is a separate database, `plates_test`**, in the same
  Postgres that Compose already exposes on `127.0.0.1:5433` — never the real
  `plates` database. `DB_HOST`/`DB_PORT`/`POSTGRES_DB` are **forced** (not
  `setdefault`) to `127.0.0.1`/`5433`/`plates_test` regardless of what the
  environment says, precisely so that a test can never point at production
  data by accident.
- **`TEST_POSTGRES_DB`** changes the name of that database for checkouts that
  run in parallel (the factory worktrees use
  `plates_test_<n>`), so one branch's migrations do not leak into
  another branch's run. It must start with `plates_test`; any other
  name makes `conftest.py` fail on import. With `pytest -n auto`
  (pytest-xdist) each worker uses its own database, `<base>_gwN` (for example
  `plates_test_12_gw0`, `plates_test_12_gw1`, ...): it is created and migrated
  the first time it is used and reused in later runs. Without
  `-n`, pytest runs serially against `<base>` as usual.
- **`TEST_REQUIRE_DB=1`** turns "Postgres not available" into a failure
  instead of a skip. The factory gate sets it so a branch is not approved with
  the database tests skipped.
- `SECRET_KEY`/`AUTH_USERS` are also forced when `conftest.py` is imported,
  before any `from app...` — otherwise a local run would inherit
  the real credentials from `.env` and the test logins would fail
  silently.
- Three test identities are already defined: `testuser`/`testpass123`
  (admin), `produccionuser`/`produccionpass123` (`production`),
  `puntoventauser`/`puntoventapass123` (`point_of_sale`, Phase 2) — all
  three are real rows in the `users` table of the test database since
  login became DB-backed, not just entries in `AUTH_USERS`/
  `AUTH_ROLES`.
- The `clean_db` fixture (Phase 1d, step 10) does a **transactional rollback
  per test**, not `TRUNCATE`: it does `TRUNCATE ... RESTART IDENTITY CASCADE` +
  seeds the 3 test identities once at the start, inside a
  `SAVEPOINT` that is never really released until the end of the test — there
  a real `ROLLBACK` happens, so no test leaves a trace in the physical
  `plates_test` regardless of whether it passed or failed. `app.core.db.get_conn` is
  patched (via `monkeypatch`) to always return that SAME connection
  instead of taking a new one from the pool — so any HTTP request the test
  fires (`authenticated_client`, etc.) operates on the same session/
  transaction, without needing real commits to see its own changes.
  `conn.commit()`/`conn.rollback()` (called both by the real code in
  `records.py`/`review.py`/`pricing.py`/`inventory.py` and by ~100 test
  sites) are reassigned to do `RELEASE SAVEPOINT` + `SAVEPOINT` /
  `ROLLBACK TO SAVEPOINT` on THAT savepoint instead of touching the real database —
  see the `clean_db` docstring in `tests/conftest.py` for the full detail
  (why psycopg's `conn.transaction()` was not used, how a transaction
  aborted by an expected error is recovered, etc.).
  By itself this does not let two workers share the same database (they would
  serialize on Postgres locks just like with `TRUNCATE`); running
  in parallel with `pytest -n auto` works because each worker has its
  own `<base>_gwN` database (see `TEST_POSTGRES_DB` above). The benefit of
  this fixture is speed (no disk/WAL work per test) and that a
  test that fails halfway never leaves corrupt data for the next one.
- **Silent gotcha**: if Postgres is not running or is not reachable
  at `127.0.0.1:5433`, the DB-dependent tests are **skipped**
  (`pytest.skip`), not failed. A "green" run with many `skipped`
  may be hiding that the DB was never available — it is worth
  checking the skip count, not just that there are no reds.
- In CI (`test.yml`) the Postgres service is explicitly published on
  port `5433` so that this same hardcode in `conftest.py` works
  the same way there.

## Environment variables

| Variable | Where it is used | Note |
|---|---|---|
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | Compose (`db`), `app/shared/db.py` | No default for the password — never hardcoded anywhere. |
| `DB_HOST` | `app/shared/db.py` | Code default `"db"` — in Compose it is **overridden** to `plates-db-1`. Never use the default in an environment with the `edge` network. |
| `DB_PORT` | `app/shared/db.py` | `5432` inside the container; `5433` is only the host mapping. |
| `DB_POOL_MIN_SIZE` / `DB_POOL_MAX_SIZE` | `app/shared/db.py` | Size of the `psycopg_pool.ConnectionPool` per gunicorn worker. Default `1`/`5` — with 2 workers, at most 10 connections from the app. |
| `SECRET_KEY` | `app/web/app.py` | Required — `RuntimeError` at startup if missing. |
| `AUTH_USERS` | `app/schema_migrate.py` (one-time backfill) | `user:hash` separated by commas. **No longer feeds the live login** (Phase 2) — it is only read once, the first time `schema_migrate` runs against a new database, to create the `users` rows. The hash contains `$` → it must be escaped as `$$` in `.env` or Compose corrupts it when interpolating variables (already documented in the root README). |
| `AUTH_ROLES` | `app/schema_migrate.py` (one-time backfill) | `user:role` separated by commas, same one-time backfill as `AUTH_USERS`. **It is not in `.env.example` today** — any user without an entry here ends up as `admin` by default. |
| `COOKIE_SECURE` | `app/web/app.py` | `1` in production (HTTPS) to mark the cookie `Secure`; off in local dev over plain HTTP. |
| `SENTRY_DSN` | `app/web/app.py` | Optional — if not set, Sentry is not even initialized (local dev and tests never send anything). |
| `UPLOADS_DIR` | `app/shared/uploads.py` | Folder where the authenticated PDFs, the signatures and the ID card and vehicle registration photos from the public form are stored. Default `/srv/uploads`; in Compose it is the `plates_uploads` volume. **The volume is NOT covered by `pg_dump` backups**: it must be backed up separately. Tests use a temporary folder. |
| `TURNSTILE_SITE_KEY` | public form | Public Cloudflare Turnstile key (shown in the widget). In local development the test keys that Cloudflare publishes work. |
| `TURNSTILE_SECRET_KEY` | `app/web/antispam.py` | Secret key to verify the token on the server. If missing (and there is no bypass), verification **fails closed**: the submission is rejected. In production it must be added to the `ENV_FILE_B64` secret. |
| `TURNSTILE_BYPASS` | `app/web/antispam.py` | `1`/`true` skips the Turnstile verification without calling the network. Only for development and tests; leave empty in production. Without bypass and without both keys (`TURNSTILE_SITE_KEY` + `TURNSTILE_SECRET_KEY`), `/declaracion` responds 503 "El formulario no está disponible por ahora" (the form is not available right now): to try the form locally export `TURNSTILE_BYPASS=1`. |
