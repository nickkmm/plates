"""Tests for app.schema_migrate.

Four things are covered:
  1. The customers/users backfill algorithm (backfill_customers/
     backfill_users, Fase 1d names -- see app/schema_migrate.py's module
     docstring for why their BACKFILL_*_VERSION string values stay the
     original Spanish-named literals even though the Python identifiers
     don't), against small synthetic data (never real production numbers --
     see the Fase 1 plan's hard constraint about not fabricating/fetching
     real data).
  2. Idempotency: re-running apply_sql_migrations()/backfill_*()/main() must
     be a no-op the second time.
  3. The schema invariant: a fresh database built from the CURRENT init.sql
     and an "old" (pre-Fase-1) database with every app/migrations/*.sql
     applied on top must end up with an identical information_schema. This
     is the only thing that will catch the two schema sources drifting
     apart later.
  4. Fase 1c specifically: app/migrations/0003_rename_to_english.sql renames
     `clientes`/`usuarios` (table + columns, plus the objects Postgres
     auto-named after them) to `customers`/`users` over real data, without
     losing rows, and running it twice is a no-op.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import tuple_row

from app import schema_migrate
from app.core.normalize import INVENTORY_CATEGORIES, PLATE_TYPES

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
    "customers", "users", "plate_type_prices", "schema_migrations", "plate_types",
)


def _insert_order(conn: psycopg.Connection, requested_by: str, order_number: str) -> int:
    with conn.cursor(row_factory=tuple_row) as cur:
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
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT version FROM schema_migrations WHERE version LIKE %s", ("%.sql",))
            applied = {row[0] for row in cur.fetchall()}
        assert applied == expected

    def test_0001_pos_fields_sql_is_safe_to_apply_twice_directly(self, db_conn):
        """The raw SQL text itself must be idempotent (IF NOT EXISTS
        everywhere), independent of the schema_migrations bookkeeping that
        normally prevents a second run."""
        sql = (_MIGRATIONS_DIR / "0001_pos_fields.sql").read_text(encoding="utf-8")
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(sql)
            cur.execute(sql)

    def test_0002_reference_integrity_sql_is_safe_to_apply_twice_directly(self, db_conn):
        """Same idempotency guarantee as 0001, for the Fase 1b migration:
        CREATE TABLE IF NOT EXISTS, INSERT ... ON CONFLICT DO NOTHING, and
        each ADD CONSTRAINT guarded by a DO block (Postgres has no ADD
        CONSTRAINT IF NOT EXISTS)."""
        sql = (_MIGRATIONS_DIR / "0002_reference_integrity.sql").read_text(encoding="utf-8")
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(sql)
            cur.execute(sql)

    def test_0003_rename_to_english_sql_is_safe_to_apply_twice_directly(self, db_conn):
        """Same idempotency guarantee as 0001/0002, for the Fase 1c rename.

        By the time this runs, db_conn's database already has `customers`/
        `users` under their final names (built straight from the CURRENT
        init.sql by _db_ready) and no `clientes`/`usuarios` at all -- every
        guard in the file should see "already done" and no-op both times.
        The actual rename-over-real-data behavior is covered separately by
        TestRenameToEnglish below, in a throwaway schema seeded with the
        OLD Fase-1-era names.
        """
        sql = (_MIGRATIONS_DIR / "0003_rename_to_english.sql").read_text(encoding="utf-8")
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(sql)
            cur.execute(sql)


# --------------------------------------------------------------------------
# backfill_customers
# --------------------------------------------------------------------------


class TestBackfillCustomers:
    def test_two_spelling_variants_collapse_into_one_customer(self, db_conn):
        """PTOV and PUNTODEVENTA both normalize to PTO.V (normalize.py's
        curated map) -- the grouping key must be the CANONICAL form, not the
        raw string, so both orders end up pointing at the SAME client."""
        _insert_order(db_conn, "PTOV", "1")
        _insert_order(db_conn, "PUNTODEVENTA", "2")
        db_conn.commit()

        schema_migrate.backfill_customers(db_conn)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT name FROM customers")
            assert [row[0] for row in cur.fetchall()] == ["PTO.V"]
            cur.execute("SELECT DISTINCT customer_id FROM production_orders")
            rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] is not None

    def test_junk_value_without_alphanumerics_is_left_unresolved(self, db_conn):
        """A requested_by with no alphanumeric character (e.g. '//') is
        skipped entirely: no junk client gets created, and the order is left
        with customer_id NULL for manual review."""
        _insert_order(db_conn, "//", "1")
        db_conn.commit()

        schema_migrate.backfill_customers(db_conn)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM customers")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT customer_id FROM production_orders WHERE order_number = '1'")
            assert cur.fetchone()[0] is None

    def test_distinct_real_clients_are_never_merged(self, db_conn):
        """MUVA GRANADA and MUVA SERGIO are genuinely different intermediaries
        in the curated map (normalize._REQUESTED_BY_MAP_RAW's own docstring
        example) -- the backfill must not accidentally collapse them."""
        _insert_order(db_conn, "MUVA GRANADA", "1")
        _insert_order(db_conn, "MUVA SERGIO", "2")
        db_conn.commit()

        schema_migrate.backfill_customers(db_conn)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT name FROM customers ORDER BY name")
            assert [row[0] for row in cur.fetchall()] == ["MUVA GRANADA", "MUVA SERGIO"]

    def test_requested_by_is_never_rewritten(self, db_conn):
        _insert_order(db_conn, "PTOV", "1")
        db_conn.commit()

        schema_migrate.backfill_customers(db_conn)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT requested_by FROM production_orders WHERE order_number = '1'")
            assert cur.fetchone()[0] == "PTOV"

    def test_rerun_creates_no_duplicate_customers(self, db_conn):
        _insert_order(db_conn, "PEDRO", "1")
        db_conn.commit()

        schema_migrate.backfill_customers(db_conn)
        schema_migrate.backfill_customers(db_conn)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM customers")
            assert cur.fetchone()[0] == 1

    def test_rerun_does_not_touch_an_already_resolved_order(self, db_conn):
        """A customer_id already set by other means (e.g. the future /ingest
        flow) must never be silently overwritten by a later backfill run."""
        order_id = _insert_order(db_conn, "PEDRO", "1")
        db_conn.commit()
        schema_migrate.backfill_customers(db_conn)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT id FROM customers WHERE name = 'OTRO'")
            row = cur.fetchone()
            if row is None:
                cur.execute("INSERT INTO customers (name) VALUES ('OTRO') RETURNING id")
                row = cur.fetchone()
            other_customer_id = row[0]
            cur.execute("UPDATE production_orders SET customer_id = %s WHERE id = %s",
                        (other_customer_id, order_id))
        db_conn.commit()

        schema_migrate.backfill_customers(db_conn)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT customer_id FROM production_orders WHERE id = %s", (order_id,))
            assert cur.fetchone()[0] == other_customer_id


# --------------------------------------------------------------------------
# backfill_users
# --------------------------------------------------------------------------


class TestBackfillUsers:
    def test_creates_one_user_per_auth_user_with_matching_role(self, db_conn, monkeypatch):
        monkeypatch.setenv("AUTH_USERS", "alice:pbkdf2:sha256:abc$def,bob:pbkdf2:sha256:xyz")
        monkeypatch.setenv("AUTH_ROLES", "bob:consulta")

        schema_migrate.backfill_users(db_conn)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT username, password_hash, role FROM users ORDER BY username")
            rows = {username: (pw_hash, role) for username, pw_hash, role in cur.fetchall()}
        # alice has no AUTH_ROLES entry -> defaults to admin, same as auth.role_for().
        assert rows["alice"] == ("pbkdf2:sha256:abc$def", "admin")
        assert rows["bob"] == ("pbkdf2:sha256:xyz", "consulta")

    def test_rerun_creates_no_duplicate_users(self, db_conn, monkeypatch):
        monkeypatch.setenv("AUTH_USERS", "alice:hash1")
        monkeypatch.setenv("AUTH_ROLES", "")

        schema_migrate.backfill_users(db_conn)
        schema_migrate.backfill_users(db_conn)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM users")
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

        with schema_migrate.db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("DELETE FROM schema_migrations")

        schema_migrate.main()
        schema_migrate.main()

        with schema_migrate.db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM users")
            row = cur.fetchone()
            assert row is not None and row[0] == 1
            cur.execute("SELECT version, count(*) FROM schema_migrations GROUP BY version")
            counts: dict[str, int] = dict(cur.fetchall())
        assert all(n == 1 for n in counts.values())


# --------------------------------------------------------------------------
# Schema invariant: fresh init.sql vs. old schema + migrations
# --------------------------------------------------------------------------


def _columns(conn: psycopg.Connection, schema: str) -> set[tuple]:
    with conn.cursor(row_factory=tuple_row) as cur:
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
    with conn.cursor(row_factory=tuple_row) as cur:
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
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {fresh} CASCADE")
            cur.execute(f"DROP SCHEMA IF EXISTS {migrated} CASCADE")
            cur.execute(f"CREATE SCHEMA {fresh}")
            cur.execute(f"CREATE SCHEMA {migrated}")
        db_conn.commit()
        try:
            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute(f"SET search_path TO {fresh}")
                cur.execute(_INIT_SQL)
            db_conn.commit()

            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute(f"SET search_path TO {migrated}")
                cur.execute(_OLD_INIT_SQL)
                for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
                    cur.execute(path.read_text(encoding="utf-8"))
            db_conn.commit()

            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SET search_path TO public")
            db_conn.commit()

            assert _columns(db_conn, fresh) == _columns(db_conn, migrated)
            assert _constraints(db_conn, fresh) == _constraints(db_conn, migrated)
        finally:
            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SET search_path TO public")
                cur.execute(f"DROP SCHEMA IF EXISTS {fresh} CASCADE")
                cur.execute(f"DROP SCHEMA IF EXISTS {migrated} CASCADE")
            db_conn.commit()


# --------------------------------------------------------------------------
# Fase 1c: full rename to English (clientes/usuarios AND the Spanish-named
# POS columns Fase 1 also added to production_orders/plates)
# --------------------------------------------------------------------------

# Byte-for-byte copy of the Spanish-named schema exactly as Fase 1 shipped it
# (init.sql / app/migrations/0001_pos_fields.sql, before Fase 1c) -- table
# and column names only, columns trimmed to the ones this migration touches
# plus the NOT NULL ones needed to satisfy production_orders' own
# constraints -- so Postgres auto-names the PK/UNIQUE constraints, the `id`
# sequences, and the two production_orders FKs the same way real
# production's tables were named (clientes_pkey/clientes_nombre_key/
# clientes_id_seq, usuarios_pkey/usuarios_username_key/usuarios_id_seq,
# production_orders_cliente_id_fkey, production_orders_creado_por_fkey).
# Standing in for a real, already-migrated Fase-1/1b production database --
# NOT for a pre-Fase-1 database (that's what _OLD_INIT_SQL/TestSchemaInvariant
# cover).
_FASE1_SPANISH_SQL = """
CREATE TABLE clientes (
    id SERIAL PRIMARY KEY,
    nombre TEXT NOT NULL UNIQUE,
    tipo TEXT,
    documento_tipo TEXT,
    documento_numero TEXT,
    documento_relacion TEXT,
    telefono TEXT,
    correo_electronico TEXT,
    licencia_transito TEXT,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE usuarios (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    rol TEXT NOT NULL,
    activo BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE production_orders (
    id SERIAL PRIMARY KEY,
    order_number TEXT NOT NULL,
    order_date DATE NOT NULL,
    requested_by TEXT NOT NULL,
    comprobante_tipo TEXT,
    comprobante_numero TEXT,
    cliente_id INTEGER REFERENCES clientes(id),
    forma_pago TEXT,
    guia_numero TEXT,
    pedido_numero TEXT,
    fecha_entrega DATE,
    fecha_transferencia_pago DATE,
    responsable_despacho TEXT,
    nombre_recibe TEXT,
    observaciones TEXT,
    creado_por INTEGER REFERENCES usuarios(id),
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE plates (
    id SERIAL PRIMARY KEY,
    order_id INTEGER REFERENCES production_orders(id),
    plate_type TEXT NOT NULL,
    motivo TEXT,
    marca_vehiculo TEXT,
    created_at TIMESTAMP DEFAULT now()
);
"""

_0003_SQL = (_MIGRATIONS_DIR / "0003_rename_to_english.sql").read_text(encoding="utf-8")

# The Spanish-named POS columns this migration renames, grouped by table --
# used below to assert every old name is gone after the rename.
_OLD_PRODUCTION_ORDERS_POS_COLUMNS = [
    "comprobante_tipo", "comprobante_numero", "cliente_id", "forma_pago",
    "guia_numero", "pedido_numero", "fecha_entrega", "fecha_transferencia_pago",
    "responsable_despacho", "nombre_recibe", "observaciones", "creado_por",
]
_OLD_PLATES_POS_COLUMNS = ["motivo", "marca_vehiculo"]


class TestRenameToEnglish:
    """app/migrations/0003_rename_to_english.sql, exercised against a
    throwaway schema seeded with the OLD (Fase 1/1b) Spanish names -- both
    `clientes`/`usuarios` and the Spanish-named POS columns Fase 1 also
    added to `production_orders`/`plates` -- and a couple of hand-made rows.
    Proves the rename preserves data, drops every old name, renames the
    auto-generated PK/UNIQUE/sequence/FK objects to match what a fresh
    install would have (see TestSchemaInvariant), and that running it twice
    is a no-op.

    Uses its own throwaway schema (same technique as TestSchemaInvariant)
    rather than the shared `public` schema, which by this point in the test
    session already has the real, already-English schema (built straight
    from the CURRENT init.sql) -- this test needs to start from the OLD
    names instead.
    """

    SCHEMA = "fase1c_rename_test"

    def _seed_old_schema(self, conn: psycopg.Connection) -> None:
        with conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {self.SCHEMA} CASCADE")
            cur.execute(f"CREATE SCHEMA {self.SCHEMA}")
            cur.execute(f"SET search_path TO {self.SCHEMA}")
            cur.execute(_FASE1_SPANISH_SQL)
            cur.execute(
                "INSERT INTO clientes (nombre, tipo, telefono) VALUES "
                "('PTO.V', 'EMPRESA', '3001234567'), ('MUVA GRANADA', NULL, NULL)"
            )
            cur.execute(
                "INSERT INTO usuarios (username, password_hash, rol, activo) "
                "VALUES ('alice', 'pbkdf2:sha256:abc', 'admin', true)"
            )
            # PTO.V and alice are each the first (only) row inserted into
            # their table above, so their ids are deterministically 1 -- used
            # below instead of a subquery, to keep the expected row tuple
            # readable.
            cur.execute(
                """
                INSERT INTO production_orders (
                    order_number, order_date, requested_by, comprobante_tipo,
                    comprobante_numero, cliente_id, forma_pago, guia_numero,
                    pedido_numero, fecha_entrega, fecha_transferencia_pago,
                    responsable_despacho, nombre_recibe, observaciones, creado_por
                ) VALUES (
                    '5001', '2026-01-15', 'PTO.V', 'FACTURA', 'F-001',
                    1, 'EFECTIVO', 'G-777', '28', '2026-01-20',
                    '2026-01-18', 'JUAN', 'MARIA', 'Entrega urgente', 1
                )
                """
            )
            cur.execute(
                """
                INSERT INTO plates (order_id, plate_type, motivo, marca_vehiculo)
                VALUES (1, 'CARRO PARTICULAR', 'DETERIORO', 'CHEVROLET')
                """
            )
        conn.commit()

    def _teardown_schema(self, conn: psycopg.Connection) -> None:
        with conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SET search_path TO public")
            cur.execute(f"DROP SCHEMA IF EXISTS {self.SCHEMA} CASCADE")
        conn.commit()

    def _old_pos_columns_remaining(self, conn: psycopg.Connection) -> list[str]:
        """Any of the OLD Spanish POS column names still present (not
        dropped) on production_orders/plates -- empty once the rename is
        complete."""
        with conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT attname FROM pg_attribute "
                "WHERE attrelid = to_regclass('production_orders') "
                "AND attname = ANY(%s) AND NOT attisdropped",
                (_OLD_PRODUCTION_ORDERS_POS_COLUMNS,),
            )
            remaining = [row[0] for row in cur.fetchall()]
            cur.execute(
                "SELECT attname FROM pg_attribute "
                "WHERE attrelid = to_regclass('plates') "
                "AND attname = ANY(%s) AND NOT attisdropped",
                (_OLD_PLATES_POS_COLUMNS,),
            )
            remaining += [row[0] for row in cur.fetchall()]
        return remaining

    def test_rename_preserves_data_and_drops_old_names(self, db_conn):
        self._seed_old_schema(db_conn)
        try:
            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute(_0003_SQL)
            db_conn.commit()

            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SELECT name, type, phone FROM customers ORDER BY name")
                customer_rows = cur.fetchall()
                cur.execute("SELECT username, password_hash, role, active FROM users")
                user_rows = cur.fetchall()
                cur.execute(
                    """
                    SELECT order_number, receipt_type, receipt_number, customer_id,
                           payment_method, tracking_number, client_order_number,
                           delivery_date, payment_date, dispatched_by, received_by,
                           notes, created_by
                    FROM production_orders WHERE order_number = '5001'
                    """
                )
                order_row = cur.fetchone()
                cur.execute("SELECT reason, vehicle_brand FROM plates WHERE order_id = 1")
                plate_row = cur.fetchone()
                cur.execute("SELECT to_regclass('clientes'), to_regclass('usuarios')")
                old_table_names = cur.fetchone()
                old_pos_columns = self._old_pos_columns_remaining(db_conn)

            assert customer_rows == [
                ("MUVA GRANADA", None, None),
                ("PTO.V", "EMPRESA", "3001234567"),
            ]
            assert user_rows == [("alice", "pbkdf2:sha256:abc", "admin", True)]
            assert order_row == (
                "5001", "FACTURA", "F-001", 1, "EFECTIVO", "G-777", "28",
                date(2026, 1, 20), date(2026, 1, 18), "JUAN", "MARIA",
                "Entrega urgente", 1,
            )
            assert plate_row == ("DETERIORO", "CHEVROLET")
            assert old_table_names == (None, None)
            assert old_pos_columns == []
        finally:
            self._teardown_schema(db_conn)

    def test_renamed_objects_match_what_a_fresh_table_would_auto_name(self, db_conn):
        """The PK/UNIQUE/FK constraint names and the id sequences must end
        up named exactly as a fresh install would auto-name them --
        otherwise a migrated old database's information_schema would differ
        from a fresh install's (see TestSchemaInvariant, which is the
        automated guard for this in general; this test pins down the exact
        expected names)."""
        self._seed_old_schema(db_conn)
        try:
            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute(_0003_SQL)
            db_conn.commit()

            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute(
                    "SELECT conname FROM pg_constraint WHERE conrelid = to_regclass('customers')"
                )
                customer_constraints = {row[0] for row in cur.fetchall()}
                cur.execute(
                    "SELECT conname FROM pg_constraint WHERE conrelid = to_regclass('users')"
                )
                user_constraints = {row[0] for row in cur.fetchall()}
                cur.execute(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = to_regclass('production_orders') AND contype = 'f'"
                )
                production_orders_fks = {row[0] for row in cur.fetchall()}
                cur.execute(
                    "SELECT pg_get_serial_sequence('customers', 'id'), "
                    "pg_get_serial_sequence('users', 'id')"
                )
                customer_seq, user_seq = cur.fetchone()
        finally:
            self._teardown_schema(db_conn)

        assert customer_constraints == {"customers_pkey", "customers_name_key"}
        assert user_constraints == {"users_pkey", "users_username_key"}
        assert production_orders_fks == {
            "production_orders_customer_id_fkey", "production_orders_created_by_fkey",
        }
        assert customer_seq is not None and customer_seq.endswith(".customers_id_seq")
        assert user_seq is not None and user_seq.endswith(".users_id_seq")

    def test_rerun_is_a_no_op(self, db_conn):
        """Applying the raw SQL a second time must change nothing and raise
        nothing -- Postgres has no RENAME ... IF EXISTS, so this is only
        true because of the guards in the file."""
        self._seed_old_schema(db_conn)
        try:
            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute(_0003_SQL)
            db_conn.commit()

            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SELECT name FROM customers ORDER BY name")
                customers_before = cur.fetchall()
                cur.execute(
                    "SELECT customer_id, payment_method, created_by "
                    "FROM production_orders WHERE order_number = '5001'"
                )
                order_before = cur.fetchone()

            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute(_0003_SQL)
            db_conn.commit()

            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SELECT name FROM customers ORDER BY name")
                customers_after = cur.fetchall()
                cur.execute(
                    "SELECT customer_id, payment_method, created_by "
                    "FROM production_orders WHERE order_number = '5001'"
                )
                order_after = cur.fetchone()
        finally:
            self._teardown_schema(db_conn)

        assert customers_before == customers_after == [("MUVA GRANADA",), ("PTO.V",)]
        assert order_before == order_after == (1, "EFECTIVO", 1)


# --------------------------------------------------------------------------
# Reference integrity (Fase 1b): plate_types / inventory_categories FKs
# --------------------------------------------------------------------------


class TestPlateTypesAndInventoryCategoriesSeed:
    """`_db_ready` (tests/conftest.py) has already applied init.sql +
    app/migrations/*.sql once this session, and `clean_db` reseeds
    inventory_categories after every TRUNCATE (see tests/conftest.py) -- so
    by the time a test runs, both reference tables must hold exactly the
    values in the matching normalize.py constant, no more, no less. This is
    the guard against the hand-copied seed SQL (init.sql AND
    0002_reference_integrity.sql) drifting from PLATE_TYPES/
    INVENTORY_CATEGORIES.
    """

    def test_plate_types_matches_normalize_plate_types_exactly(self, db_conn):
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_type FROM plate_types")
            seeded = {row[0] for row in cur.fetchall()}
        assert seeded == set(PLATE_TYPES)

    def test_inventory_categories_matches_normalize_inventory_categories_exactly(self, db_conn):
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT category FROM inventory_categories")
            seeded = {row[0] for row in cur.fetchall()}
        assert seeded == set(INVENTORY_CATEGORIES)


class TestReferenceIntegrityConstraints:
    """Fase 1b: plates.plate_type / plate_type_prices.plate_type are now real
    FKs to plate_types(plate_type), and blank_preparations.category is a real
    FK to inventory_categories(category) -- no longer just a text convention
    checked in Python (normalize.PLATE_TYPES/INVENTORY_CATEGORIES). These
    prove the FK actually rejects a bad value at the database layer, and that
    a valid value still works exactly as before.
    """

    def test_plate_with_invalid_plate_type_is_rejected(self, db_conn):
        order_id = _insert_order(db_conn, "PEDRO", "1")
        db_conn.commit()
        with pytest.raises(psycopg.errors.ForeignKeyViolation), db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "INSERT INTO plates (order_id, plate_type) VALUES (%s, %s)",
                (order_id, "NOT A REAL TYPE"),
            )
        db_conn.rollback()

    def test_plate_with_valid_plate_type_still_works(self, db_conn):
        order_id = _insert_order(db_conn, "PEDRO", "1")
        db_conn.commit()
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "INSERT INTO plates (order_id, plate_type) VALUES (%s, %s) RETURNING id",
                (order_id, "CARRO PARTICULAR"),
            )
            assert cur.fetchone() is not None
        db_conn.commit()

    def test_plate_type_prices_with_invalid_plate_type_is_rejected(self, db_conn):
        with pytest.raises(psycopg.errors.ForeignKeyViolation), db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "INSERT INTO plate_type_prices (plate_type, unit_price) VALUES (%s, %s)",
                ("NOT A REAL TYPE", 1000),
            )
        db_conn.rollback()

    def test_plate_type_prices_with_valid_plate_type_still_works(self, db_conn):
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "INSERT INTO plate_type_prices (plate_type, unit_price) VALUES (%s, %s)",
                ("CARRO PARTICULAR", 1000),
            )
        db_conn.commit()
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT unit_price FROM plate_type_prices WHERE plate_type = 'CARRO PARTICULAR'"
            )
            assert cur.fetchone()[0] == 1000

    def test_blank_preparation_with_invalid_category_is_rejected(self, db_conn):
        with pytest.raises(psycopg.errors.ForeignKeyViolation), db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "INSERT INTO blank_preparations (category, quantity) VALUES (%s, %s)",
                ("NOT A REAL CATEGORY", 10),
            )
        db_conn.rollback()

    def test_blank_preparation_with_valid_category_still_works(self, db_conn):
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "INSERT INTO blank_preparations (category, quantity) VALUES (%s, %s) RETURNING id",
                ("CARRO PARTICULAR", 10),
            )
            assert cur.fetchone() is not None
        db_conn.commit()
