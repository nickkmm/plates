"""Schema migration runner: applies app/migrations/*.sql to an already-
initialized database, then backfills customers/users from existing data.

(The backfill functions/constants below are named backfill_customers/
backfill_users/BACKFILL_CUSTOMERS_VERSION/BACKFILL_USERS_VERSION (Fase 1d,
renamed from backfill_clientes/backfill_usuarios/BACKFILL_CLIENTES_VERSION/
BACKFILL_USUARIOS_VERSION -- pure Python-identifier rename, no behavior
change). Their STRING VALUES are a different story and were deliberately
NOT touched by that rename: BACKFILL_CUSTOMERS_VERSION is still the literal
"0002_backfill_clientes", and BACKFILL_USERS_VERSION is still
"0003_backfill_usuarios" -- those exact strings are already written into
production's `schema_migrations` table by the original Fase 1 run, and
changing either one here would make this runner think that backfill had
never happened, running it again unnecessarily against real production.
This is unrelated to Fase 1c's `app/migrations/0003_rename_to_english.sql`,
which renamed the `clientes`/`usuarios` tables themselves -- plus the
Spanish-named POS columns Fase 1 also added to production_orders/plates
(cliente_id -> customer_id, creado_por -> created_by, etc.; see that
migration file for the full map) -- to their English equivalents at the SQL
level; that rename was already complete before this one.)

Deliberately named `schema_migrate.py`, NOT `migrate.py` -- `app/migrate.py`
already exists and does something completely different (one-off historical
Excel import, run once against production data). Confusing the two commands
would be an expensive mistake.

Usage:
    python -m app.schema_migrate

Safe to run repeatedly (idempotent): every `.sql` file under app/migrations/
is applied and recorded in `schema_migrations` inside the SAME transaction
(so a file that fails partway through is never marked applied), and already-
applied files/backfill steps are skipped on the next run. The customers/
users backfill is plain Python (not a `.sql` file) because grouping
requires app.core.normalize.normalize_requested_by -- it is tracked in the
same `schema_migrations` table under its own synthetic version names, and its
SQL is additionally written to be idempotent on its own (ON CONFLICT DO
NOTHING / UPDATE ... WHERE customer_id IS NULL) as defense in depth.

Does NOT create the base schema (production_orders/plates/...) -- that is
init.sql's job, applied automatically by Postgres only the first time a data
volume is initialized (see docker-compose.yml). This runner only ever ALTERs/
ADDs onto an existing schema.
"""

from __future__ import annotations

import glob
import os
from collections import defaultdict

import psycopg
from psycopg.rows import DictRow

from app.core import db
from app.core.normalize import normalize_requested_by
from app.web.auth import ROLE_ADMIN, load_roles, load_users

_MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")

# Synthetic "versions" for the Python backfill steps, recorded in
# schema_migrations alongside the real *.sql filenames so a second run skips
# them. Numbered to sort right after the schema migration that creates the
# tables/columns they depend on.
BACKFILL_CUSTOMERS_VERSION = "0002_backfill_clientes"
BACKFILL_USERS_VERSION = "0003_backfill_usuarios"


def _migration_files() -> list[str]:
    """All app/migrations/*.sql files, in filename order (0001, 0002, ...)."""
    return sorted(glob.glob(os.path.join(_MIGRATIONS_DIR, "*.sql")))


def _ensure_schema_migrations_table(conn: psycopg.Connection[DictRow]) -> None:
    """Create the bookkeeping table if this is the first time the runner sees
    this database (an old production DB predates schema_migrations entirely).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT now()
            )
            """
        )
    conn.commit()


def _applied_versions(conn: psycopg.Connection[DictRow]) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations")
        return {row["version"] for row in cur.fetchall()}


def _record_version(cur: psycopg.Cursor[DictRow], version: str) -> None:
    cur.execute(
        "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT (version) DO NOTHING",
        (version,),
    )


def apply_sql_migrations(conn: psycopg.Connection[DictRow]) -> list[str]:
    """Apply every not-yet-applied app/migrations/*.sql file, in order.

    Each file's SQL and its schema_migrations record are committed together
    as one transaction (explicit conn.commit(), same convention as the rest
    of app/core/db/) -- a file that fails partway through is rolled back
    whole (conn.close()/the next conn.rollback() discards the uncommitted
    work), never left half-applied-but-marked-done. Returns the list of
    versions newly applied (empty on a no-op re-run).
    """
    applied = _applied_versions(conn)
    newly_applied = []
    for path in _migration_files():
        version = os.path.basename(path)
        if version in applied:
            continue
        with open(path, encoding="utf-8") as f:
            sql = f.read()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                _record_version(cur, version)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        newly_applied.append(version)
    return newly_applied


def _distinct_requested_by(conn: psycopg.Connection[DictRow]) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT requested_by FROM production_orders")
        return [row["requested_by"] for row in cur.fetchall()]


def backfill_customers(conn: psycopg.Connection[DictRow]) -> None:
    """One-time backfill of `customers` from historical `requested_by` values.

    Algorithm (see the Fase 1 plan for the exact rationale):
      1. Read every distinct requested_by value.
      2. Discard values with no alphanumeric character at all (e.g. '//') --
         they're left with customer_id NULL, never turned into a junk client.
      3. Group the rest by normalize_requested_by(value) -- the CANONICAL
         form, not the raw string, so two spelling variants of the same
         intermediary collapse into one client.
      4. One customers row per group (attributes NULL -- unknown for
         historical clients); ON CONFLICT so a re-run never duplicates.
      5. Point every production_orders row whose requested_by is in that
         group at the new customer_id -- but only where customer_id is still
         NULL, so a re-run never overwrites anything already resolved
         (including rows resolved some other way after this first ran).
      requested_by itself is never rewritten here -- that's a separate
      decision, out of scope for this backfill.
    """
    raw_values = _distinct_requested_by(conn)
    groups: dict[str, list[str]] = defaultdict(list)
    skipped: list[str] = []
    for raw in raw_values:
        if raw is None or not any(ch.isalnum() for ch in raw):
            skipped.append(raw)
            continue
        groups[normalize_requested_by(raw)].append(raw)

    try:
        with conn.cursor() as cur:
            for canonical, raws in groups.items():
                cur.execute(
                    """
                    INSERT INTO customers (name) VALUES (%s)
                    ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                    """,
                    (canonical,),
                )
                row = cur.fetchone()
                assert row is not None, "INSERT ... RETURNING id always yields a row"
                customer_id = row["id"]
                for raw in raws:
                    cur.execute(
                        """
                        UPDATE production_orders SET customer_id = %s
                        WHERE requested_by = %s AND customer_id IS NULL
                        """,
                        (customer_id, raw),
                    )
            _record_version(cur, BACKFILL_CUSTOMERS_VERSION)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    if skipped:
        print(
            f"backfill_customers: {len(skipped)} requested_by value(s) had no alphanumeric "
            f"characters and were left with customer_id NULL for manual review: {skipped!r}"
        )


def backfill_users(conn: psycopg.Connection[DictRow]) -> None:
    """One-time backfill of `users` from the AUTH_USERS/AUTH_ROLES env vars.

    Same username/password_hash pairs as today (werkzeug hashing is
    unchanged) -- nobody's password is reset. A username with no AUTH_ROLES
    entry defaults to admin, matching app.web.auth.role_for()'s existing
    behavior. ON CONFLICT (username) DO NOTHING makes a re-run a no-op even
    without the schema_migrations guard (defense in depth).
    """
    users = load_users()
    roles = load_roles()
    try:
        with conn.cursor() as cur:
            for username, password_hash in users.items():
                cur.execute(
                    """
                    INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)
                    ON CONFLICT (username) DO NOTHING
                    """,
                    (username, password_hash, roles.get(username, ROLE_ADMIN)),
                )
            _record_version(cur, BACKFILL_USERS_VERSION)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def run_backfills(conn: psycopg.Connection[DictRow]) -> None:
    """Run each backfill step that hasn't been recorded as applied yet."""
    applied = _applied_versions(conn)
    if BACKFILL_CUSTOMERS_VERSION not in applied:
        backfill_customers(conn)
    if BACKFILL_USERS_VERSION not in applied:
        backfill_users(conn)


def main() -> None:
    with db.get_conn() as conn:
        _ensure_schema_migrations_table(conn)
        newly_applied = apply_sql_migrations(conn)
        for version in newly_applied:
            print(f"applied {version}")
        run_backfills(conn)
    print("schema_migrate: up to date.")


if __name__ == "__main__":
    main()
