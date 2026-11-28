"""The connection pool (psycopg 3) every package reaches the database through.

Connection settings come from environment variables (same ones the Postgres
container uses, plus DB_HOST/DB_PORT for where to reach it):
    DB_HOST (default "db"), DB_PORT (default "5432"),
    POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
    DB_POOL_MIN_SIZE (default 1), DB_POOL_MAX_SIZE (default 5)

Connections are opened with row_factory=dict_row, so every SELECT in the repositories
comes back as a dict keyed by column name (or list of such dicts) -- never a plain
positional tuple.

**Transaction contract**: every repository function receives an already-
open connection (`conn`) and never calls commit()/rollback() itself -- whoever
calls it is the one that opened the connection (`with db.get_conn() as conn:
...`) and is responsible for its transaction. This is deliberate: passing
`conn` explicitly, instead of hiding it behind an interface, keeps visible in
every function's signature who actually owns the commit -- see
docs/DOMAINS.md's "Contrato de transacción" for the real bug (several functions
in app/orders/service.py each opening their own connection) that made this
worth writing down instead of leaving implicit.

**Connection pooling** (Fase 1d): `get_conn()` is a `@contextmanager`
generator wrapping a lazily-built `psycopg_pool.ConnectionPool`, not a bare
`psycopg.connect()` per call. The pool is built on the FIRST real call to
`get_conn()`, never at import time -- tests/conftest.py imports this module
unconditionally, including when POSTGRES_PASSWORD isn't set (to run
the pure-logic tests with zero DB dependency), so building the pool at
import time would break that. `_conninfo_kwargs()` is split out as a pure
function purely so it can be tested without mocking psycopg.connect or
spinning up a real pool (see tests/core/db/test_connection.py::TestConninfoKwargs).
`with db.get_conn() as conn:` behaves exactly as before at all ~21 existing
call sites: `pool.connection()` wraps the checked-out connection in `with
conn:` internally, so the usual commit-on-success/rollback-on-exception
behavior is unchanged, just backed by a reused connection instead of a fresh
socket every time.

Gunicorn (see web.Dockerfile) runs `--workers 2` WITHOUT `--preload`, so each
worker process imports the app fresh AFTER forking and builds its own pool
lazily on its own first request -- no `post_fork` hook needed. If `--preload`
is ever added to the Dockerfile, this reasoning breaks and pool construction
needs to move into a gunicorn `post_fork` hook instead.

Callers import this module (`from app.shared import db`) and call
`db.get_conn()` through it, so tests/conftest.py's patch of `get_conn` here
reaches every call site.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool


def _conninfo_kwargs() -> dict[str, object]:
    """Connection kwargs read fresh from the environment on every call --
    split out from pool construction (_get_pool) so this can be unit-tested
    directly (tests/core/db/test_connection.py::TestConninfoKwargs) without mocking
    psycopg.connect or building a real ConnectionPool."""
    return {
        "host": os.environ.get("DB_HOST", "db"),
        "port": os.environ.get("DB_PORT", "5432"),
        "dbname": os.environ["POSTGRES_DB"],
        "user": os.environ["POSTGRES_USER"],
        "password": os.environ["POSTGRES_PASSWORD"],
        "row_factory": dict_row,
    }


_pool: ConnectionPool[psycopg.Connection[DictRow]] | None = None


def _get_pool() -> ConnectionPool[psycopg.Connection[DictRow]]:
    """Build the pool on first use (see the module docstring for why this
    must stay lazy) and cache it for the lifetime of this process/worker."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            kwargs=_conninfo_kwargs(),
            min_size=int(os.environ.get("DB_POOL_MIN_SIZE", "1")),
            max_size=int(os.environ.get("DB_POOL_MAX_SIZE", "5")),
            open=True,
        )
    return _pool


@contextmanager
def get_conn() -> Iterator[psycopg.Connection[DictRow]]:
    """A connection checked out from the pool, as a context manager.

    `with db.get_conn() as conn:` behaves exactly as it did before the pool
    (see the module docstring): commits on a clean exit, rolls back on an
    exception, then returns the connection to the pool instead of closing
    the socket.
    """
    with _get_pool().connection() as conn:
        yield conn
