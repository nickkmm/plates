"""Shared pytest configuration and fixtures.

CRITICAL ORDERING: ``app.web.app`` reads ``SECRET_KEY`` from the environment
at MODULE IMPORT TIME (not inside a factory function) and raises
``RuntimeError`` if it's unset. ``app.web.auth`` reads ``AUTH_USERS`` lazily
(inside functions), but we set both here together, up front, so that ANY test
module importing ``app.web.app`` or ``app.web.auth`` -- directly or
transitively -- never races the module-level check. Because pytest always
imports a directory's ``conftest.py`` before collecting test modules inside
it, everything below runs before any ``tests/**/test_*.py`` file is imported.

Do not reorder: the env vars must be set before the ``from app...`` imports
further down this file.

**Fase 2**: the live login flow now queries the `users` TABLE, not
AUTH_USERS/AUTH_ROLES (see app/web/auth.py's module docstring) -- so the
three test identities below are seeded into `users` by the `clean_db`
fixture itself (see TEST_USERS/its INSERT below), not just exported as env
vars. AUTH_USERS/AUTH_ROLES are still set here too, because
app.schema_migrate.backfill_users (a one-time, already-applied-in-production
migration, still covered by tests/test_schema_migrate.py) reads them --
that's the ONLY remaining consumer.
"""

from __future__ import annotations

import os

from werkzeug.security import generate_password_hash

# --- Auth env vars required at `app.web.app` import time ------------------
# Forced (not setdefault): local dev runs this suite inside the plates-web
# container too (matching Python/psycopg without a host venv), which already
# has the REAL SECRET_KEY/AUTH_USERS exported from .env at container start.
# setdefault would silently keep those real values, so login attempts with
# TEST_USERNAME/TEST_PASSWORD below would 401 against the real AUTH_USERS
# instead of testing anything -- forced assignment keeps the suite hermetic
# regardless of where it's invoked from, matching DB_HOST/DB_PORT below.
TEST_USERNAME = "testuser"
TEST_PASSWORD = "testpass123"
_TEST_PASSWORD_HASH = generate_password_hash(TEST_PASSWORD)

# A second user with the 'production' role -- read-only today (demoted from
# its original full-access grant once real usage showed it should only ever
# look things up), for testing the default-deny role restrictions (see
# app.web.auth.consulta_allowed). TEST_USERNAME deliberately has role 'admin'
# below -- it must keep behaving as full-access, matching production today
# (every AUTH_USERS-era account with no AUTH_ROLES entry defaulted to admin).
PRODUCCION_USERNAME = "produccionuser"
PRODUCCION_PASSWORD = "produccionpass123"
_PRODUCCION_PASSWORD_HASH = generate_password_hash(PRODUCCION_PASSWORD)

# A third user with the new (Fase 2) 'point_of_sale' role -- this one has NO
# AUTH_USERS/AUTH_ROLES equivalent at all (the role is DB-native from birth,
# never lived in the env-var world), so it's seeded into `users` only, never
# exported below.
PUNTO_VENTA_USERNAME = "puntoventauser"
PUNTO_VENTA_PASSWORD = "puntoventapass123"
_PUNTO_VENTA_PASSWORD_HASH = generate_password_hash(PUNTO_VENTA_PASSWORD)

os.environ["SECRET_KEY"] = "test-secret-key-do-not-use-in-production"
# Still set for app.schema_migrate.backfill_users's own tests (see module
# docstring above) -- NOT read by the live login flow anymore.
os.environ["AUTH_USERS"] = (
    f"{TEST_USERNAME}:{_TEST_PASSWORD_HASH},"
    f"{PRODUCCION_USERNAME}:{_PRODUCCION_PASSWORD_HASH}"
)
os.environ["AUTH_ROLES"] = f"{PRODUCCION_USERNAME}:production"

# --- Test database ----------------------------------------------------------
# Point at a SEPARATE database (`plates_test`) on the same already-running
# Postgres the docker-compose `db` service exposes at 127.0.0.1:5433. Never
# the real `plates` database. POSTGRES_USER/PASSWORD must match that server;
# for local dev convenience we best-effort load them from a repo-root `.env`
# (gitignored, never committed) if one is present and the env doesn't already
# have them, mirroring `docker compose`'s own `env_file: .env`. In CI (no
# `.env`), set POSTGRES_USER/POSTGRES_PASSWORD directly and DB-only tests
# will run; without them, DB-dependent tests skip cleanly (see `_db_ready`).
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)


def _load_dotenv_defaults(path: str) -> None:
    """Tiny best-effort `.env` reader (`KEY=VALUE` lines). Never overrides an
    already-set env var, and intentionally has no third-party dependency."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv_defaults(os.path.join(_REPO_ROOT, ".env"))

# Force these regardless of what's in the environment/.env: tests must never
# be able to point at the real `plates` database.
os.environ["DB_HOST"] = "127.0.0.1"
os.environ["DB_PORT"] = "5433"
os.environ["POSTGRES_DB"] = "plates_test"
os.environ.setdefault("POSTGRES_USER", "plates_user")
# POSTGRES_PASSWORD has no hardcoded fallback here on purpose (never commit a
# real secret) -- it must come from the environment or the local .env above.

import psycopg  # noqa: E402
import pytest  # noqa: E402
from psycopg.rows import DictRow, tuple_row  # noqa: E402

from app import schema_migrate  # noqa: E402
from app.core import db  # noqa: E402
from app.core.normalize import INVENTORY_CATEGORIES  # noqa: E402

TABLES = (
    "plates", "production_orders", "blank_preparations", "inventory_categories",
    "customers", "users", "plate_type_prices",
)
_SCHEMA_PATH = os.path.join(_REPO_ROOT, "init.sql")

# Seeded into `users` by `clean_db` on every test (see its docstring) --
# (username, password_hash, role). This is now the ONLY place these three
# identities are made valid for an actual login; AUTH_USERS/AUTH_ROLES above
# no longer feed the live login flow at all.
_TEST_USERS: tuple[tuple[str, str, str], ...] = (
    (TEST_USERNAME, _TEST_PASSWORD_HASH, "admin"),
    (PRODUCCION_USERNAME, _PRODUCCION_PASSWORD_HASH, "production"),
    (PUNTO_VENTA_USERNAME, _PUNTO_VENTA_PASSWORD_HASH, "point_of_sale"),
)


def _create_database_if_missing() -> None:
    """CREATE DATABASE plates_test on the shared Postgres, if it doesn't exist yet.

    Connects to the always-present `postgres` maintenance DB to do so (you
    can't run CREATE DATABASE while connected to the DB you're creating).
    """
    conn = psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
        dbname="postgres",
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (os.environ["POSTGRES_DB"],))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{os.environ["POSTGRES_DB"]}"')
    finally:
        conn.close()


def _schema_applied(conn: psycopg.Connection[DictRow]) -> bool:
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT to_regclass('public.plates') IS NOT NULL")
        row = cur.fetchone()
        return bool(row and row[0])


def _apply_schema(conn: psycopg.Connection[DictRow]) -> None:
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        schema_sql = f.read()
    with conn.cursor() as cur:
        cur.execute(schema_sql)
    conn.commit()


@pytest.fixture(scope="session")
def _db_ready() -> bool:
    """Session-scoped: ensure `plates_test` exists with the CURRENT schema
    applied (base schema + every app/migrations/*.sql).

    Returns True if the test database is reachable and ready, False
    otherwise (e.g. no POSTGRES_PASSWORD, or Postgres not reachable) so
    DB-dependent tests can skip cleanly instead of erroring the whole run.

    The to_regclass check below only tells us `plates` exists at all, not
    that it has the latest columns -- a developer with an old `plates_test`
    volume from before a schema change would otherwise sail past this check
    and then hit a confusing "column does not exist" deep inside some
    unrelated test. So after that check (applying init.sql from scratch if
    needed), we ALWAYS run the migration runner too: on a fresh DB it's a
    fast no-op (nothing new to apply), and on a stale one it converges the
    schema the same way `python -m app.schema_migrate` does in production.
    """
    if not os.environ.get("POSTGRES_PASSWORD"):
        return False
    try:
        _create_database_if_missing()
        with db.get_conn() as conn:
            if not _schema_applied(conn):
                _apply_schema(conn)
        schema_migrate.main()
        return True
    except psycopg.Error:
        return False


@pytest.fixture
def clean_db(_db_ready: bool):
    """Truncate all app tables (identities reset) before the test runs.

    Skips the test if the shared Postgres / `plates_test` isn't reachable,
    rather than failing outright -- keeps `app/core` pure-logic tests runnable
    with zero DB dependency while still exercising real DB code when
    available. Still TRUNCATEs rather than relying on a rollback for
    isolation: `db.get_conn()` is a pooled connection now (Fase 1d), so a
    rollback-per-test fixture is possible in principle, but this project
    hasn't adopted one yet (see the Fase 1d plan's step 10 fast-follow) --
    truncating keeps every test's starting state identical regardless of
    what a previous test committed.

    Reseeds `inventory_categories` with the 3 fixed materials right after
    truncating it (mirroring init.sql/0002_reference_integrity.sql, which
    seed it the same way) -- since Fase 1b, blank_preparations.category is a
    real FK to this table, so any row must already exist before the first
    `db.insert_blank_preparation()` in a test, exactly like on a real
    from-birth database (`db.recalc_inventory()` only creates the row
    afterwards, too late for a non-deferred FK on an insert that comes
    first). `plate_types` (the other Fase 1b reference table) is NOT in
    TABLES/truncated here on purpose -- it's static and never touched by
    tests, so it stays seeded from `_db_ready`'s one-time schema setup.

    **Fase 2**: also reseeds `_TEST_USERS` into `users` right after
    truncating it. This matters more than it looks: `users` IS in TABLES
    (so every test starts with a clean one), but `_db_ready`'s one-time
    `schema_migrate.main()` backfill of AUTH_USERS/AUTH_ROLES into `users`
    only ever runs ONCE per physical `plates_test` database (tracked in
    `schema_migrations`, which `clean_db` deliberately never truncates) --
    so without this, every test AFTER the very first `clean_db` call in a
    session would find `users` empty and login would fail for the rest of
    the suite. Truncating/reseeding here, every test, is what makes the
    three test identities reliably able to log in regardless of run order.
    """
    if not _db_ready:
        pytest.skip("Postgres test database not reachable at 127.0.0.1:5433 (plates_test)")
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
        cur.executemany(
            "INSERT INTO inventory_categories (category) VALUES (%s)",
            [(category,) for category in INVENTORY_CATEGORIES],
        )
        cur.executemany(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
            _TEST_USERS,
        )
    yield


@pytest.fixture
def db_conn(clean_db: None):
    """A live connection to the clean test database, for test setup/assertions."""
    with db.get_conn() as conn:
        yield conn


@pytest.fixture
def test_credentials(clean_db: None) -> tuple[str, str]:
    """The (username, password) pair for the 'admin'-role test user.

    Depends on `clean_db` (Fase 2): login now queries the `users` table, so
    requesting this fixture is what guarantees that table has actually been
    (re)seeded with this identity before a test tries to log in with it --
    see clean_db's docstring. Any test that logs in via this fixture (or a
    web-layer fixture built on top of it, like `authenticated_client`) gets
    a real Postgres dependency now, same accepted tradeoff the plan calls
    out ("el login pasa a depender de la BD").
    """
    return TEST_USERNAME, TEST_PASSWORD


@pytest.fixture
def produccion_credentials(clean_db: None) -> tuple[str, str]:
    """The (username, password) pair for the read-only 'production'-role test
    user. See test_credentials' docstring re: the `clean_db` dependency."""
    return PRODUCCION_USERNAME, PRODUCCION_PASSWORD


@pytest.fixture
def punto_venta_credentials(clean_db: None) -> tuple[str, str]:
    """The (username, password) pair for the new (Fase 2) 'point_of_sale'-role
    test user. See test_credentials' docstring re: the `clean_db`
    dependency."""
    return PUNTO_VENTA_USERNAME, PUNTO_VENTA_PASSWORD
