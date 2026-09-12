"""Tests for app.schema_migrate.

Three things are covered:
  1. The clientes/usuarios backfill algorithm, against small synthetic data
     (never real production numbers -- see the Fase 1 plan's hard
     constraint about not fabricating/fetching real data).
  2. Idempotency: re-running apply_sql_migrations()/backfill_*()/main() must
     be a no-op the second time.
  3. The schema invariant: a fresh database built from the CURRENT init.sql
     and an "old" (pre-Fase-1) database with every app/migrations/*.sql
     applied on top must end up with an identical information_schema. This
     is the only thing that will catch the two schema sources drifting
     apart later.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

import psycopg

from app import schema_migrate

_REPO_ROOT = Path(__file__).resolve().parent.parent
_INIT_SQL = (_REPO_ROOT / "init.sql").read_text(encoding="utf-8")
_MIGRATIONS_DIR = _REPO_ROOT / "app" / "migrations"

# Snapshot of init.sql exactly as it was immediately BEFORE the Fase 1
# POS-fields schema change (production_orders/plates/inventory_categories/
# blank_preparations only -- no clientes/usuarios/plate_type_prices/
# schema_migrations, no new columns, no plates_order_id_idx). This stands in
# for an old, already-initialized production database that has never had
# app/migrations/*.sql applied. Comments are dropped (irrelevant to
# information_schema); every column/constraint is otherwise verbatim.
_OLD_INIT_SQL = """
CREATE TABLE production_orders (
    id SERIAL PRIMARY KEY,
    order_number TEXT NOT NULL,
    order_date DATE NOT NULL,
    requested_by TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE plates (
    id SERIAL PRIMARY KEY,
    order_id INTEGER REFERENCES production_orders(id),
    plate_type TEXT NOT NULL,
    plate_number TEXT,
    plate_to TEXT,
    is_batch_replacement BOOLEAN DEFAULT false,
    is_voided BOOLEAN DEFAULT false,
    is_plate_annulled BOOLEAN DEFAULT false,
    quantity INTEGER NOT NULL DEFAULT 1,
    material TEXT,
    prepared_by TEXT,
    is_possible_duplicate BOOLEAN DEFAULT false,
    city TEXT,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE inventory_categories (
    category TEXT PRIMARY KEY,
    initial_stock INTEGER DEFAULT 0,
    prepared INTEGER DEFAULT 0,
    consumed INTEGER DEFAULT 0
);

CREATE TABLE blank_preparations (
    id SERIAL PRIMARY KEY,
    prep_date DATE,
    category TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    prepared_by TEXT,
    created_at TIMESTAMP DEFAULT now()
);
"""

_APP_TABLES = (
    "production_orders", "plates", "blank_preparations", "inventory_categories",
    "clientes", "usuarios", "plate_type_prices", "schema_migrations",
)


def _insert_order(conn: psycopg.Connection, requested_by: str, order_number: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO production_orders (order_number, order_date, requested_by) "
            "VALUES (%s, %s, %s) RETURNING id",
            (order_number, date(2026, 1, 1), requested_by),
        )
        row = cur.fetchone()
        assert row is not None
        return row[0]


# --------------------------------------------------------------------------
# apply_sql_migrations
# --------------------------------------------------------------------------


class TestApplySqlMigrations:
    def test_rerun_applies_nothing_new(self, db_conn):
        # _db_ready (tests/conftest.py) already ran the full runner once this
        # session, so every *.sql file is already recorded as applied.
        assert schema_migrate.apply_sql_migrations(db_conn) == []

    def test_every_migration_file_is_recorded_as_applied(self, db_conn):
        expected = {os.path.basename(p) for p in schema_migrate._migration_files()}
        with db_conn.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations WHERE version LIKE %s", ("%.sql",))
            applied = {row[0] for row in cur.fetchall()}
        assert applied == expected

    def test_0001_pos_fields_sql_is_safe_to_apply_twice_directly(self, db_conn):
        """The raw SQL text itself must be idempotent (IF NOT EXISTS
        everywhere), independent of the schema_migrations bookkeeping that
        normally prevents a second run."""
        sql = (_MIGRATIONS_DIR / "0001_pos_fields.sql").read_text(encoding="utf-8")
        with db_conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(sql)


# --------------------------------------------------------------------------
# backfill_clientes
# --------------------------------------------------------------------------


class TestBackfillClientes:
    def test_two_spelling_variants_collapse_into_one_cliente(self, db_conn):
        """PTOV and PUNTODEVENTA both normalize to PTO.V (normalize.py's
        curated map) -- the grouping key must be the CANONICAL form, not the
        raw string, so both orders end up pointing at the SAME client."""
        _insert_order(db_conn, "PTOV", "1")
        _insert_order(db_conn, "PUNTODEVENTA", "2")
        db_conn.commit()

        schema_migrate.backfill_clientes(db_conn)

        with db_conn.cursor() as cur:
            cur.execute("SELECT nombre FROM clientes")
            assert [row[0] for row in cur.fetchall()] == ["PTO.V"]
            cur.execute("SELECT DISTINCT cliente_id FROM production_orders")
            rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] is not None

    def test_junk_value_without_alphanumerics_is_left_unresolved(self, db_conn):
        """A requested_by with no alphanumeric character (e.g. '//') is
        skipped entirely: no junk client gets created, and the order is left
        with cliente_id NULL for manual review."""
        _insert_order(db_conn, "//", "1")
        db_conn.commit()

        schema_migrate.backfill_clientes(db_conn)

        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM clientes")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT cliente_id FROM production_orders WHERE order_number = '1'")
            assert cur.fetchone()[0] is None

    def test_distinct_real_clients_are_never_merged(self, db_conn):
        """MUVA GRANADA and MUVA SERGIO are genuinely different intermediaries
        in the curated map (normalize._REQUESTED_BY_MAP_RAW's own docstring
        example) -- the backfill must not accidentally collapse them."""
        _insert_order(db_conn, "MUVA GRANADA", "1")
        _insert_order(db_conn, "MUVA SERGIO", "2")
        db_conn.commit()

        schema_migrate.backfill_clientes(db_conn)

        with db_conn.cursor() as cur:
            cur.execute("SELECT nombre FROM clientes ORDER BY nombre")
            assert [row[0] for row in cur.fetchall()] == ["MUVA GRANADA", "MUVA SERGIO"]

    def test_requested_by_is_never_rewritten(self, db_conn):
        _insert_order(db_conn, "PTOV", "1")
        db_conn.commit()

        schema_migrate.backfill_clientes(db_conn)

        with db_conn.cursor() as cur:
            cur.execute("SELECT requested_by FROM production_orders WHERE order_number = '1'")
            assert cur.fetchone()[0] == "PTOV"

    def test_rerun_creates_no_duplicate_clientes(self, db_conn):
        _insert_order(db_conn, "PEDRO", "1")
        db_conn.commit()

        schema_migrate.backfill_clientes(db_conn)
        schema_migrate.backfill_clientes(db_conn)

        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM clientes")
            assert cur.fetchone()[0] == 1

    def test_rerun_does_not_touch_an_already_resolved_order(self, db_conn):
        """A cliente_id already set by other means (e.g. the future /ingest
        flow) must never be silently overwritten by a later backfill run."""
        order_id = _insert_order(db_conn, "PEDRO", "1")
        db_conn.commit()
        schema_migrate.backfill_clientes(db_conn)

        with db_conn.cursor() as cur:
            cur.execute("SELECT id FROM clientes WHERE nombre = 'OTRO'")
            row = cur.fetchone()
            if row is None:
                cur.execute("INSERT INTO clientes (nombre) VALUES ('OTRO') RETURNING id")
                row = cur.fetchone()
            other_cliente_id = row[0]
            cur.execute("UPDATE production_orders SET cliente_id = %s WHERE id = %s",
                        (other_cliente_id, order_id))
        db_conn.commit()

        schema_migrate.backfill_clientes(db_conn)

        with db_conn.cursor() as cur:
            cur.execute("SELECT cliente_id FROM production_orders WHERE id = %s", (order_id,))
            assert cur.fetchone()[0] == other_cliente_id


# --------------------------------------------------------------------------
# backfill_usuarios
# --------------------------------------------------------------------------


class TestBackfillUsuarios:
    def test_creates_one_usuario_per_auth_user_with_matching_role(self, db_conn, monkeypatch):
        monkeypatch.setenv("AUTH_USERS", "alice:pbkdf2:sha256:abc$def,bob:pbkdf2:sha256:xyz")
        monkeypatch.setenv("AUTH_ROLES", "bob:consulta")

        schema_migrate.backfill_usuarios(db_conn)

        with db_conn.cursor() as cur:
            cur.execute("SELECT username, password_hash, rol FROM usuarios ORDER BY username")
            rows = {username: (pw_hash, rol) for username, pw_hash, rol in cur.fetchall()}
        # alice has no AUTH_ROLES entry -> defaults to admin, same as auth.role_for().
        assert rows["alice"] == ("pbkdf2:sha256:abc$def", "admin")
        assert rows["bob"] == ("pbkdf2:sha256:xyz", "consulta")

    def test_rerun_creates_no_duplicate_usuarios(self, db_conn, monkeypatch):
        monkeypatch.setenv("AUTH_USERS", "alice:hash1")
        monkeypatch.setenv("AUTH_ROLES", "")

        schema_migrate.backfill_usuarios(db_conn)
        schema_migrate.backfill_usuarios(db_conn)

        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM usuarios")
            assert cur.fetchone()[0] == 1


# --------------------------------------------------------------------------
# main() end to end
# --------------------------------------------------------------------------


class TestMain:
    def test_rerun_is_a_no_op(self, clean_db, monkeypatch):
        """Simulates the real-world scenario end to end: an old database that
        has never seen schema_migrations at all (bookkeeping wiped here to
        stand in for that, since clean_db doesn't touch it -- see
        tests/conftest.py's TABLES) gets `python -m app.schema_migrate` run
        twice in a row. The second run must change nothing.
        """
        monkeypatch.setenv("AUTH_USERS", "alice:hash1")
        monkeypatch.setenv("AUTH_ROLES", "")

        conn = schema_migrate.db.get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM schema_migrations")
            conn.commit()
        finally:
            conn.close()

        schema_migrate.main()
        schema_migrate.main()

        conn = schema_migrate.db.get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM usuarios")
                row = cur.fetchone()
                assert row is not None and row[0] == 1
                cur.execute("SELECT version, count(*) FROM schema_migrations GROUP BY version")
                counts: dict[str, int] = dict(cur.fetchall())
        finally:
            conn.close()
        assert all(n == 1 for n in counts.values())


# --------------------------------------------------------------------------
# Schema invariant: fresh init.sql vs. old schema + migrations
# --------------------------------------------------------------------------


def _columns(conn: psycopg.Connection, schema: str) -> set[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name, data_type, is_nullable, column_default,
                   character_maximum_length, numeric_precision, numeric_scale
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = ANY(%s)
            """,
            (schema, list(_APP_TABLES)),
        )
        rows = cur.fetchall()
    # column_default for a SERIAL column embeds the sequence's OWN schema-
    # qualified name (nextval('schema.table_id_seq'::regclass)) -- strip the
    # schema prefix so two schemas with identical tables under different
    # names compare equal.
    return {
        (table, col, dtype, nullable,
         default.replace(f"{schema}.", "") if default else default,
         charlen, numprec, numscale)
        for table, col, dtype, nullable, default, charlen, numprec, numscale in rows
    }


_AUTO_NOT_NULL_CHECK = re.compile(r"^\d+_\d+_\d+_not_null$")


def _constraints(conn: psycopg.Connection, schema: str) -> set[tuple]:
    """PK/UNIQUE/FK constraint identity per table, excluding Postgres's
    auto-generated NOT NULL pseudo-CHECK constraints.

    Since Postgres 12, every NOT NULL column also gets a synthetic
    constraint_type='CHECK' row here, named after internal OIDs (e.g.
    '20968_20981_1_not_null') -- those OIDs differ between the fresh and
    migrated schema/relations even when the columns are identical, which
    would make this comparison flag a false mismatch. is_nullable in
    _columns() already captures this information per-column, so these are
    filtered out rather than compared by (OID-derived) name.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, constraint_type, constraint_name
            FROM information_schema.table_constraints
            WHERE table_schema = %s AND table_name = ANY(%s)
            """,
            (schema, list(_APP_TABLES)),
        )
        rows = cur.fetchall()
    return {
        (table, ctype, cname)
        for table, ctype, cname in rows
        if not (ctype == "CHECK" and _AUTO_NOT_NULL_CHECK.match(cname))
    }


class TestSchemaInvariant:
    def test_migrated_old_schema_matches_fresh_init_sql(self, db_conn):
        """A brand-new database built from the CURRENT init.sql, and an OLD
        (pre-Fase-1) database with every app/migrations/*.sql applied on top,
        must end up with the exact same columns and constraints. This is the
        only thing that will catch the two schema sources drifting apart as
        the schema evolves further.

        Built as two throwaway schemas inside the shared plates_test
        database (rather than two separate databases) so this test needs no
        extra fixtures/lock beyond the usual db_conn.
        """
        fresh, migrated = "schema_invariant_fresh", "schema_invariant_migrated"
        with db_conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {fresh} CASCADE")
            cur.execute(f"DROP SCHEMA IF EXISTS {migrated} CASCADE")
            cur.execute(f"CREATE SCHEMA {fresh}")
            cur.execute(f"CREATE SCHEMA {migrated}")
        db_conn.commit()
        try:
            with db_conn.cursor() as cur:
                cur.execute(f"SET search_path TO {fresh}")
                cur.execute(_INIT_SQL)
            db_conn.commit()

            with db_conn.cursor() as cur:
                cur.execute(f"SET search_path TO {migrated}")
                cur.execute(_OLD_INIT_SQL)
                for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
                    cur.execute(path.read_text(encoding="utf-8"))
            db_conn.commit()

            with db_conn.cursor() as cur:
                cur.execute("SET search_path TO public")
            db_conn.commit()

            assert _columns(db_conn, fresh) == _columns(db_conn, migrated)
            assert _constraints(db_conn, fresh) == _constraints(db_conn, migrated)
        finally:
            with db_conn.cursor() as cur:
                cur.execute("SET search_path TO public")
                cur.execute(f"DROP SCHEMA IF EXISTS {fresh} CASCADE")
                cur.execute(f"DROP SCHEMA IF EXISTS {migrated} CASCADE")
            db_conn.commit()
