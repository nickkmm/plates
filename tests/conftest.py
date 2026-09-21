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
from contextlib import contextmanager

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


_CLEAN_DB_SAVEPOINT = "clean_db_sp"


@pytest.fixture
def clean_db(_db_ready: bool, monkeypatch: pytest.MonkeyPatch):
    """Reset all app tables (identities reset) before the test runs, via a
    SAVEPOINT-based rollback instead of TRUNCATE -- much faster (no disk/WAL
    work per test) and what the Fase 1d plan's step 10 fast-follow asked for.
    NOT a real parallelism enabler though (verified before writing this):
    every test still shares the ONE physical `plates_test` database, so two
    tests/workers TRUNCATing (or even just DELETE-ing) the same tables
    concurrently would just serialize on Postgres's own locks -- true
    multi-worker parallelism would need a separate schema/DB per pytest-xdist
    worker, out of scope here.

    Skips the test if the shared Postgres / `plates_test` isn't reachable,
    rather than failing outright -- keeps `app/core` pure-logic tests runnable
    with zero DB dependency while still exercising real DB code when
    available.

    **How the rollback works**: checks out ONE raw connection directly from
    the pool (`db._get_pool()`, bypassing `db.get_conn()`'s own context
    manager) and holds it for the WHOLE test. `app.core.db.get_conn` is
    monkeypatched to always yield this SAME connection instead of checking
    out a (possibly different) one from the pool -- this is what makes every
    `with db.get_conn() as conn:` call anywhere (test code AND, critically,
    every Flask route the test drives through `authenticated_client`/etc.)
    operate on one shared Postgres session/transaction, so they all see each
    other's uncommitted writes (same-session MVCC visibility needs no commit
    at all) and none of it can leak to another test.

    A real top-level `conn.commit()` would durably write to the actual
    `plates_test` database -- unacceptable, since nothing must survive past
    this fixture's teardown. But `conn.commit()`/`conn.rollback()` are called
    explicitly, pervasively, by both real app code (`records.py`, `review.py`,
    `pricing.py`, `inventory.py`) and ~100 existing test call sites -- so
    they can't just be disabled; they have to keep meaning "durably keep what
    I've done so far" / "discard what I've done since the last checkpoint",
    just scoped to a SAVEPOINT inside the outer (never-committed) transaction
    instead of the whole database. Verified empirically (against real
    Postgres, not assumed) before writing this:
      - `RELEASE SAVEPOINT` finalizes it (can't ROLLBACK TO it afterward);
        `ROLLBACK TO SAVEPOINT` discards everything since it but leaves the
        savepoint itself intact and reusable -- including recovering a
        connection stuck in Postgres's aborted-transaction state after a
        real error (psycopg.errors.*Violation), the same way a real
        `conn.rollback()` would, which is exactly what several
        `tests/test_schema_migrate.py` tests rely on after an expected FK
        violation.
      - `psycopg`'s own `conn.transaction()` context manager was considered
        and rejected: it raises `ProgrammingError` if `conn.commit()`/
        `.rollback()` are called while one is open -- incompatible with the
        pervasive explicit-commit pattern above. Plain SQL
        `SAVEPOINT`/`RELEASE`/`ROLLBACK TO` has no such restriction.
    So `conn.commit`/`conn.rollback` are reassigned (plain instance
    attributes, restored via the captured originals in `finally`, not via
    `monkeypatch` -- ordering relative to this fixture's own final real
    rollback needs to be exact, not left to monkeypatch's generic LIFO
    teardown) to instead RELEASE+re-SAVEPOINT / ROLLBACK TO the CURRENT
    (innermost) savepoint. `db.get_conn()` itself is reassigned (via
    `monkeypatch`, the ordinary case it's built for) to a context manager
    that PUSHES a new savepoint on entry and pops it on exit (RELEASE on a
    clean exit, ROLLBACK TO + RELEASE on an exception) -- replicating the
    original pool's own "commit on success, rollback on exception" `with`
    behavior (see `app/core/db/__init__.py::get_conn`), which
    `tests/core/test_ingestion.py::TestAtomicity` depends on.

    **A real STACK of savepoints, not just one** -- `with db.get_conn()`
    blocks genuinely nest (a test's own `db_conn` fixture holds one open
    around a whole test; an HTTP request the test drives through
    `authenticated_client` opens another one inside it). A single shared
    savepoint was tried first and is wrong: an exception in the INNER block
    would roll back the OUTER block's earlier work too, even work that
    logically has nothing to do with the failure -- caught by a real test
    that set up two customers via `db_conn` with no explicit commit (correct
    under this design -- same-connection visibility needs none), then
    POSTed a request expected to fail; the failure's rollback wiped out
    BOTH customers, not just the failed request's own attempted write, and
    the next `db.get_conn()` call (inside the route's own error-handling
    re-render) couldn't find the still-should-exist row. Pushing/popping a
    real stack fixes this: an inner block's failure only unwinds ITS OWN
    savepoint, never a savepoint an outer, still-open block already
    established.

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

    pool = db._get_pool()
    conn = pool.getconn()
    real_commit, real_rollback = conn.commit, conn.rollback
    # A STACK of savepoints, not just one: `with db.get_conn() as conn:`
    # blocks nest for real (e.g. db_conn's fixture holds one open around a
    # whole test, and an HTTP request the test drives opens another one
    # inside it) -- a single shared savepoint would make an exception in the
    # INNER block roll back the OUTER block's earlier, already-"successful"
    # work too (discovered via a real test failure: a test's own setup
    # through `db_conn`, never explicitly committed, disappeared after a
    # LATER route call in the same test failed on purpose). Each `with
    # db.get_conn()` push/pops its own savepoint; conn.commit()/.rollback()
    # (called explicitly, mid-block, by both real app code and test code)
    # only ever touch the CURRENT (innermost) one, replacing it with a fresh
    # savepoint of the same "level" so the block's own eventual pop still
    # has something to act on.
    savepoints: list[str] = []
    _counter = {"n": 0}

    def _new_savepoint_name() -> str:
        _counter["n"] += 1
        return f"{_CLEAN_DB_SAVEPOINT}_{_counter['n']}"

    def _execute(sql: str) -> None:
        with conn.cursor() as cur:
            cur.execute(sql)

    def _push_savepoint() -> None:
        name = _new_savepoint_name()
        _execute(f"SAVEPOINT {name}")
        savepoints.append(name)

    try:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
            cur.executemany(
                "INSERT INTO inventory_categories (category) VALUES (%s)",
                [(category,) for category in INVENTORY_CATEGORIES],
            )
            cur.executemany(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
                _TEST_USERS,
            )
        _push_savepoint()

        def _checkpoint_commit() -> None:
            # A real conn.commit() on an aborted transaction is one of
            # Postgres's special always-allowed exceptions -- it's silently
            # treated as a rollback, no error -- which is what let some
            # tests get away with never explicitly recovering from an
            # expected error (e.g. a UNIQUE violation from
            # db.create_user()) under the old TRUNCATE design, where this
            # function's job was done by a real conn.commit()/rollback() at
            # the pool's own `with` exit. RELEASE SAVEPOINT has no such
            # exception -- it errors out on an aborted connection -- so that
            # same "commit on aborted = implicit rollback" leniency has to
            # be reproduced by hand here.
            if conn.info.transaction_status == psycopg.pq.TransactionStatus.INERROR:
                _checkpoint_rollback()
                return
            _execute(f"RELEASE SAVEPOINT {savepoints[-1]}")
            savepoints[-1] = _new_savepoint_name()
            _execute(f"SAVEPOINT {savepoints[-1]}")

        def _checkpoint_rollback() -> None:
            _execute(f"ROLLBACK TO SAVEPOINT {savepoints[-1]}")

        conn.commit = _checkpoint_commit
        conn.rollback = _checkpoint_rollback

        @contextmanager
        def _shared_get_conn():
            _push_savepoint()
            try:
                yield conn
            except BaseException:
                name = savepoints.pop()
                _execute(f"ROLLBACK TO SAVEPOINT {name}")
                _execute(f"RELEASE SAVEPOINT {name}")
                raise
            else:
                name = savepoints.pop()
                if conn.info.transaction_status == psycopg.pq.TransactionStatus.INERROR:
                    _execute(f"ROLLBACK TO SAVEPOINT {name}")
                _execute(f"RELEASE SAVEPOINT {name}")

        monkeypatch.setattr(db, "get_conn", _shared_get_conn)
        yield
    finally:
        conn.commit, conn.rollback = real_commit, real_rollback
        real_rollback()
        pool.putconn(conn)


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
