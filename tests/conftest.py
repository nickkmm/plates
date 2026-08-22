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

# A second user with the read-only 'consulta' role, for testing the
# default-deny role restrictions (see app.web.auth.consulta_allowed).
# TEST_USERNAME deliberately gets NO entry in AUTH_ROLES below -- it must
# keep defaulting to admin (full access), matching production today, where
# AUTH_ROLES is entirely unset.
CONSULTA_USERNAME = "consultauser"
CONSULTA_PASSWORD = "consultapass123"

os.environ["SECRET_KEY"] = "test-secret-key-do-not-use-in-production"
os.environ["AUTH_USERS"] = (
    f"{TEST_USERNAME}:{generate_password_hash(TEST_PASSWORD)},"
    f"{CONSULTA_USERNAME}:{generate_password_hash(CONSULTA_PASSWORD)}"
)
os.environ["AUTH_ROLES"] = f"{CONSULTA_USERNAME}:consulta"

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

from app.core import db  # noqa: E402

TABLES = ("plates", "production_orders", "blank_preparations", "inventory_categories")
_SCHEMA_PATH = os.path.join(_REPO_ROOT, "init.sql")


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


def _schema_applied(conn: psycopg.Connection) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.plates') IS NOT NULL")
        row = cur.fetchone()
        return bool(row and row[0])


def _apply_schema(conn: psycopg.Connection) -> None:
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        schema_sql = f.read()
    with conn.cursor() as cur:
        cur.execute(schema_sql)
    conn.commit()


@pytest.fixture(scope="session")
def _db_ready() -> bool:
    """Session-scoped: ensure `plates_test` exists with the schema applied.

    Returns True if the test database is reachable and ready, False
    otherwise (e.g. no POSTGRES_PASSWORD, or Postgres not reachable) so
    DB-dependent tests can skip cleanly instead of erroring the whole run.
    """
    if not os.environ.get("POSTGRES_PASSWORD"):
        return False
    try:
        _create_database_if_missing()
        conn = db.get_conn()
        try:
            if not _schema_applied(conn):
                _apply_schema(conn)
        finally:
            conn.close()
        return True
    except psycopg.Error:
        return False


@pytest.fixture
def clean_db(_db_ready: bool):
    """Truncate all app tables (identities reset) before the test runs.

    Skips the test if the shared Postgres / `plates_test` isn't reachable,
    rather than failing outright -- keeps `app/core` pure-logic tests runnable
    with zero DB dependency while still exercising real DB code when
    available (see task notes: no per-test transaction rollback here because
    `db.get_conn()` opens a fresh connection per call with no injection
    point, so tests instead get a truncated table set).
    """
    if not _db_ready:
        pytest.skip("Postgres test database not reachable at 127.0.0.1:5433 (plates_test)")
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
        conn.commit()
    finally:
        conn.close()
    yield


@pytest.fixture
def db_conn(clean_db: None):
    """A live connection to the clean test database, for test setup/assertions."""
    conn = db.get_conn()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def test_credentials() -> tuple[str, str]:
    """The (username, password) pair baked into AUTH_USERS for this test run."""
    return TEST_USERNAME, TEST_PASSWORD


@pytest.fixture
def consulta_credentials() -> tuple[str, str]:
    """The (username, password) pair for the read-only 'consulta'-role test
    user, baked into AUTH_USERS/AUTH_ROLES for this test run."""
    return CONSULTA_USERNAME, CONSULTA_PASSWORD
