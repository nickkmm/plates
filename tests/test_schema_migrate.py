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
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from app import schema_migrate
from app.auth.permissions import PERMISSIONS
from app.orders import repository as orders_repository
from app.shared.models import ExtractedRow
from app.shared.normalize import INVENTORY_CATEGORIES, PLATE_TYPES

_REPO_ROOT = Path(__file__).resolve().parent.parent
_INIT_SQL = (_REPO_ROOT / "init.sql").read_text(encoding="utf-8")
_MIGRATIONS_DIR = _REPO_ROOT / "app" / "migrations"
_0015_SQL = (_MIGRATIONS_DIR / "0015_roles_and_permissions.sql").read_text(encoding="utf-8")
_0016_SQL = (_MIGRATIONS_DIR / "0016_production_status.sql").read_text(encoding="utf-8")
_0017_SQL = (_MIGRATIONS_DIR / "0017_permissions.sql").read_text(encoding="utf-8")
_0018_SQL = (_MIGRATIONS_DIR / "0018_user_roles.sql").read_text(encoding="utf-8")
_0019_SQL = (_MIGRATIONS_DIR / "0019_refresh_tokens.sql").read_text(encoding="utf-8")
_0004_SQL = (_MIGRATIONS_DIR / "0004_translate_role_values_to_english.sql").read_text(encoding="utf-8")

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
    "ownership_cards", "declarations", "declaration_cards", "roles", "role_permissions",
    "production_order_events", "permissions", "user_roles", "refresh_tokens",
)


@contextmanager
def _throwaway_schema(conn: psycopg.Connection, schema: str, *setup_sql: str) -> Iterator[None]:
    """Create `schema`, point the connection's search_path at it, run
    `setup_sql` there and commit; always drop it again afterwards. Migrations
    that read or write the historical users.role column (0004, 0015, 0018)
    can't run on `public`, which has the current schema."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"SET search_path TO {schema}")
        for sql in setup_sql:
            cur.execute(sql)
    conn.commit()
    try:
        yield
    finally:
        with conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SET search_path TO public")
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.commit()


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

    def test_0004_translate_role_values_sql_is_safe_to_apply_twice_directly(self, db_conn):
        """Same idempotency guarantee as 0001/0002/0003 -- a plain UPDATE ...
        WHERE naturally matches zero rows on the second run. Run in a
        throwaway schema with the historical users.role column (gone from
        `public` since 0018)."""
        with _throwaway_schema(db_conn, "role_values_0004_twice", _PRE_0015_USERS_SQL, _0015_SQL):
            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute(_0004_SQL)
                cur.execute(_0004_SQL)

    def test_0005_customer_types_renames_clears_and_is_safe_to_apply_twice(self, db_conn):
        """PERSONA NATURAL -> CLIENTE, OTROS -> NULL, other types untouched;
        the new person columns exist and transit_license is gone (it already
        is on the fresh test schema, so DROP COLUMN IF EXISTS is a no-op)."""
        sql = (_MIGRATIONS_DIR / "0005_customer_types_and_person_fields.sql").read_text(encoding="utf-8")
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "INSERT INTO customers (name, type) VALUES "
                "('A', 'PERSONA NATURAL'), ('B', 'OTROS'), ('C', 'EMPRESA'), ('D', NULL)"
            )
            cur.execute(sql)
            cur.execute(sql)
            cur.execute("SELECT name, type FROM customers ORDER BY name")
            assert cur.fetchall() == [("A", "CLIENTE"), ("B", None), ("C", "EMPRESA"), ("D", None)]
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'customers' AND table_schema = current_schema()"
            )
            columns = {row[0] for row in cur.fetchall()}
        assert {"address", "neighborhood", "municipality"} <= columns
        assert "transit_license" not in columns

    def test_0008_plates_ownership_card_id_backfills_eligible_plates_and_is_idempotent(self, db_conn):
        """Column + index exist; the backfill links an eligible plate (CLIENTE,
        GESTOR, NULL-type customer or no customer) and skips ranges, voided,
        annulled, replacement plates and EMPRESA/TRÁNSITO orders; a second
        apply changes nothing."""
        sql = (_MIGRATIONS_DIR / "0008_plates_ownership_card_id.sql").read_text(encoding="utf-8")
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("INSERT INTO customers (name, type) VALUES ('TIT', 'CLIENTE') RETURNING id")
            owner = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO ownership_cards (customer_id, transit_license, plate_number) "
                "VALUES (%s, 'L1', 'AAA111') RETURNING id", (owner,),
            )
            card_id = cur.fetchone()[0]
            order_ids = {}
            for i, ctype in enumerate(("CLIENTE", "GESTOR", None, "EMPRESA", "TRÁNSITO")):
                cur.execute(
                    "INSERT INTO customers (name, type) VALUES (%s, %s) RETURNING id", (f"C{i}", ctype),
                )
                customer_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO production_orders (order_number, order_date, requested_by, customer_id) "
                    "VALUES (%s, '2026-01-01', %s, %s) RETURNING id", (str(i), f"C{i}", customer_id),
                )
                order_ids[ctype] = cur.fetchone()[0]
            no_customer = _insert_order(db_conn, "SINCLIENTE", "99")

            def add(order_id, tag, **cols):
                columns = {"plate_number": "AAA111", **cols}
                names = ", ".join(["order_id", "plate_type", *columns])
                marks = ", ".join(["%s"] * (len(columns) + 2))
                cur.execute(
                    f"INSERT INTO plates ({names}) VALUES ({marks}) RETURNING id",
                    (order_id, "CARRO PARTICULAR", *columns.values()),
                )
                return tag, cur.fetchone()[0]

            plates = dict([
                add(order_ids["CLIENTE"], "cliente"),
                add(order_ids["GESTOR"], "gestor"),
                add(order_ids[None], "null_type"),
                add(no_customer, "no_customer"),
                add(order_ids["EMPRESA"], "empresa"),
                add(order_ids["TRÁNSITO"], "transito"),
                add(order_ids["CLIENTE"], "range", plate_to="AAA120"),
                add(order_ids["CLIENTE"], "voided", is_voided=True),
                add(order_ids["CLIENTE"], "annulled", is_plate_annulled=True),
                add(order_ids["CLIENTE"], "replacement", is_batch_replacement=True),
                add(order_ids["CLIENTE"], "other_number", plate_number="ZZZ999"),
            ])
            cur.execute(sql)
            cur.execute(sql)
            cur.execute("SELECT id, ownership_card_id FROM plates")
            linked = dict(cur.fetchall())
            cur.execute(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'plates' AND schemaname = current_schema()"
            )
            indexes = {row[0] for row in cur.fetchall()}
        for tag in ("cliente", "gestor", "null_type", "no_customer"):
            assert linked[plates[tag]] == card_id, tag
        for tag in ("empresa", "transito", "range", "voided", "annulled", "replacement", "other_number"):
            assert linked[plates[tag]] is None, tag
        assert "plates_ownership_card_id_idx" in indexes


    def test_0009_declaration_cards_backfills_old_one_card_declarations_and_is_idempotent(self, db_conn):
        """Rebuild the OLD `declarations` shape (inside the test's rolled-back
        transaction), insert an approved PROPIETARIO and a pending GESTOR row,
        then apply 0009 twice: each becomes one declaration_cards row, the
        signature is the gestor's (else the owner's), submitted_data takes the
        canonical shape, and the per-card columns are gone from declarations."""
        sql = (_MIGRATIONS_DIR / "0009_declaration_cards.sql").read_text(encoding="utf-8")
        person = lambda name: {  # noqa: E731
            "name": name, "document_type": "CÉDULA", "document_number": "1", "address": "a",
            "neighborhood": "b", "municipality": "c", "phone": "3", "email": ""}
        vehicle = lambda plate, licence: {  # noqa: E731
            "plate_number": plate, "transit_license": licence, "engine_number": "E",
            "chassis_number": "", "vehicle_type": "CARRO PARTICULAR", "registered_in": "BOGOTÁ",
            "vehicle_brand": "KIA"}
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("DROP TABLE declaration_cards")
            cur.execute("DROP TABLE declarations")
            cur.execute(
                """
                CREATE TABLE declarations (
                    id SERIAL PRIMARY KEY, created_at TIMESTAMP DEFAULT now(),
                    reason TEXT NOT NULL, plate_quantity INTEGER NOT NULL, acting_as TEXT NOT NULL,
                    customer_id INTEGER REFERENCES customers(id),
                    ownership_card_id INTEGER REFERENCES ownership_cards(id),
                    gestor_id INTEGER REFERENCES customers(id), status TEXT NOT NULL,
                    review_flags TEXT[] NOT NULL DEFAULT '{}', reviewed_by INTEGER REFERENCES users(id),
                    reviewed_at TIMESTAMP, review_note TEXT, submitted_data JSONB NOT NULL,
                    owner_signature_path TEXT, gestor_signature_path TEXT, notarized_pdf_path TEXT,
                    submitter_ip TEXT, user_agent TEXT, accepted_terms_at TIMESTAMP
                )
                """
            )
            cur.execute("CREATE INDEX declarations_customer_id_idx ON declarations (customer_id)")
            cur.execute("INSERT INTO customers (name, type) VALUES ('ANA', 'CLIENTE') RETURNING id")
            owner = cur.fetchone()[0]
            cur.execute("INSERT INTO customers (name, type) VALUES ('GESTORIA', 'GESTOR') RETURNING id")
            cur.fetchone()
            cur.execute(
                "INSERT INTO ownership_cards (customer_id, transit_license, plate_number) "
                "VALUES (%s, 'L1', 'AAA111') RETURNING id", (owner,),
            )
            card = cur.fetchone()[0]
            insert = (
                "INSERT INTO declarations (reason, plate_quantity, acting_as, customer_id, "
                "ownership_card_id, gestor_id, status, review_flags, submitted_data, "
                "owner_signature_path, gestor_signature_path, notarized_pdf_path) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id"
            )
            cur.execute(insert, (
                "DETERIORO", 2, "PROPIETARIO", owner, card, None, "approved", [],
                Jsonb({"reason": "DETERIORO", "plate_quantity": 2, "acting_as": "PROPIETARIO",
                       "owner": person("ANA"), "owner_relation": "", "gestor": None,
                       "vehicle": vehicle("AAA111", "L1"), "raw": {"x": 1}}),
                "owner.png", None, None))
            approved = cur.fetchone()[0]
            cur.execute(insert, (
                "PÉRDIDA", 1, "GESTOR", None, None, None, "pending_review", ["plate_made_recently"],
                Jsonb({"reason": "PÉRDIDA", "plate_quantity": 1, "acting_as": "GESTOR",
                       "owner": person("BETO"), "owner_relation": "POSEEDOR",
                       "gestor": person("GESTORIA"), "vehicle": vehicle("BBB222", "L2")}),
                "owner2.png", "gestor2.png", "n.pdf"))
            pending = cur.fetchone()[0]
            cur.execute(sql)
            cur.execute(sql)
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'declarations' AND table_schema = current_schema()"
            )
            columns = {row[0] for row in cur.fetchall()}
            cur.execute(
                "SELECT declaration_id, position, reason, plate_quantity, plate_number, transit_license, "
                "engine_number, chassis_number, vehicle_type, owner_relation, owner_data ->> 'name', "
                "customer_id, ownership_card_id, review_flags "
                "FROM declaration_cards ORDER BY declaration_id"
            )
            cards = cur.fetchall()
            cur.execute("SELECT id, declarant_signature_path, notarized_pdf_path, gestor_id, review_flags, "
                        "submitted_data FROM declarations ORDER BY id")
            declarations = {row[0]: row[1:] for row in cur.fetchall()}
        assert columns.isdisjoint({"reason", "plate_quantity", "customer_id", "ownership_card_id",
                                   "owner_signature_path", "gestor_signature_path"})
        assert {"declarant_signature_path", "gestor_id", "review_flags"} <= columns
        assert cards == [
            (approved, 0, "DETERIORO", 2, "AAA111", "L1", "E", None, "CARRO PARTICULAR", "DUEÑO", "ANA",
             owner, card, []),
            (pending, 0, "PÉRDIDA", 1, "BBB222", "L2", "E", None, "CARRO PARTICULAR", "POSEEDOR", "BETO",
             None, None, ["plate_made_recently"]),
        ]
        assert declarations[approved][:4] == ("owner.png", None, None, [])
        assert declarations[pending][:4] == ("gestor2.png", "n.pdf", None, ["plate_made_recently"])
        data = declarations[approved][4]
        assert set(data) == {"acting_as", "declarant", "cards", "raw"}
        assert data["declarant"]["name"] == "ANA" and data["raw"] == {"x": 1}
        (data_card,) = data["cards"]
        assert data_card["plate_number"] == "AAA111" and data_card["reason"] == "DETERIORO"
        assert data_card["plate_quantity"] == 2 and data_card["owner_relation"] == "DUEÑO"
        assert data_card["owner"]["name"] == "ANA"
        pending_data = declarations[pending][4]
        assert "raw" not in pending_data
        assert pending_data["declarant"]["name"] == "GESTORIA"          # the gestor is the declarant
        assert pending_data["cards"][0]["owner"]["name"] == "BETO"
        assert pending_data["cards"][0]["owner_relation"] == "POSEEDOR"

    def test_0009_is_safe_to_apply_twice_directly_on_the_new_schema(self, db_conn):
        sql = (_MIGRATIONS_DIR / "0009_declaration_cards.sql").read_text(encoding="utf-8")
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(sql)
            cur.execute(sql)

    def test_0014_used_plates_photo_sql_is_safe_to_apply_twice_directly(self, db_conn):
        sql = (_MIGRATIONS_DIR / "0014_declaration_used_plates_photo.sql").read_text(encoding="utf-8")
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(sql)
            cur.execute(sql)
            cur.execute(
                "SELECT data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'declaration_cards' AND column_name = 'used_plates_photo_path' "
                "AND table_schema = current_schema()"
            )
            assert cur.fetchall() == [("text", "YES")]

    def test_0015_roles_and_permissions_sql_is_safe_to_apply_twice_directly(self, db_conn):
        """Re-applying it on a schema it already migrated raises nothing and
        adds no roles/role_permissions rows. Run in a throwaway schema with
        the historical users.role column (gone from `public` since 0018)."""

        def counts() -> tuple[int, int]:
            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SELECT (SELECT count(*) FROM roles), (SELECT count(*) FROM role_permissions)")
                row = cur.fetchone()
                assert row is not None
                return row[0], row[1]

        with _throwaway_schema(db_conn, "roles_0015_twice", _PRE_0015_USERS_SQL, _0015_SQL):
            before = counts()
            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute(_0015_SQL)
                cur.execute(_0015_SQL)
            assert counts() == before

    def test_0016_production_status_backfills_old_orders_as_delivered_and_new_ones_pending(self, db_conn):
        """An order that exists when 0016 runs becomes delivered; one inserted
        afterwards through orders_repository.insert_order starts pending; a second apply
        changes neither."""

        def status(order_id: int) -> str:
            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SELECT production_status FROM production_orders WHERE id = %s", (order_id,))
                row = cur.fetchone()
                assert row is not None
                return row[0]

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("DROP TABLE production_order_events")
            cur.execute("ALTER TABLE production_orders DROP COLUMN production_status")
        old_id = _insert_order(db_conn, "PEDRO", "1600")
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(_0016_SQL)
        assert status(old_id) == "delivered"

        new_id = orders_repository.insert_order(
            db_conn,
            row=ExtractedRow(order_number="1601", order_date=date(2026, 1, 2), requested_by="PEDRO",
                             plate_type="CARRO PARTICULAR"),
            customer_id=None, created_by=None,
        )
        assert status(new_id) == "pending"

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(_0016_SQL)
        assert (status(old_id), status(new_id)) == ("delivered", "pending")

    def test_0016_production_status_sql_is_safe_to_apply_twice_directly(self, db_conn):
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(_0016_SQL)
            cur.execute(_0016_SQL)
            cur.execute(
                "SELECT column_default, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'production_orders' AND column_name = 'production_status' "
                "AND table_schema = current_schema()"
            )
            assert cur.fetchall() == [("'pending'::text", "NO")]

    def test_0017_permissions_sql_is_safe_to_apply_twice_directly(self, db_conn):
        """Re-applying it on the already-migrated schema raises nothing and
        changes no permissions/role_permissions row counts."""

        def counts() -> tuple[int, int]:
            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SELECT (SELECT count(*) FROM permissions), (SELECT count(*) FROM role_permissions)")
                row = cur.fetchone()
                assert row is not None
                return row[0], row[1]

        before = counts()
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(_0017_SQL)
            cur.execute(_0017_SQL)
        assert counts() == before


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
        monkeypatch.setenv("AUTH_ROLES", "bob:production")

        schema_migrate.backfill_users(db_conn)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT u.username, u.password_hash, ur.role "
                "FROM users u JOIN user_roles ur ON ur.user_id = u.id ORDER BY u.username"
            )
            rows = {username: (pw_hash, role) for username, pw_hash, role in cur.fetchall()}
        # alice has no AUTH_ROLES entry -> defaults to admin, same as auth.role_for().
        assert rows["alice"] == ("pbkdf2:sha256:abc$def", "admin")
        assert rows["bob"] == ("pbkdf2:sha256:xyz", "production")

    def test_rerun_creates_no_duplicate_users(self, db_conn, monkeypatch):
        """Counts only 'alice' specifically (not `SELECT count(*) FROM
        users`): Fase 2's `clean_db` fixture (db_conn's own dependency) now
        pre-seeds its own 3 test identities into `users`, so a bare total
        count would no longer isolate what this test actually checks (no
        DUPLICATE alice rows from re-running the backfill)."""
        monkeypatch.setenv("AUTH_USERS", "alice:hash1")
        monkeypatch.setenv("AUTH_ROLES", "")

        schema_migrate.backfill_users(db_conn)
        schema_migrate.backfill_users(db_conn)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM users WHERE username = 'alice'")
            assert cur.fetchone()[0] == 1
            cur.execute(
                "SELECT ur.role FROM user_roles ur JOIN users u ON u.id = ur.user_id WHERE u.username = 'alice'"
            )
            assert cur.fetchall() == [("admin",)]


    def test_runs_on_a_fresh_init_sql_database(self, db_conn, monkeypatch):
        """On a brand-new database built from the CURRENT init.sql the roles
        are already seeded, so user_roles_role_fkey accepts the default admin
        role and the two other system roles. Built in a throwaway schema (same
        technique as TestSchemaInvariant)."""
        schema = "fresh_backfill_users_test"
        monkeypatch.setenv("AUTH_USERS", "alice:hash1,bob:hash2,carol:hash3")
        monkeypatch.setenv("AUTH_ROLES", "bob:production,carol:point_of_sale")
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            cur.execute(f"CREATE SCHEMA {schema}")
            cur.execute(f"SET search_path TO {schema}")
            cur.execute(_INIT_SQL)
        db_conn.commit()
        try:
            schema_migrate.backfill_users(db_conn)
            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute(
                    "SELECT u.username, ur.role FROM users u JOIN user_roles ur ON ur.user_id = u.id "
                    "ORDER BY u.username"
                )
                rows = cur.fetchall()
        finally:
            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SET search_path TO public")
                cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            db_conn.commit()
        assert rows == [("alice", "admin"), ("bob", "production"), ("carol", "point_of_sale")]


# --------------------------------------------------------------------------
# main() end to end
# --------------------------------------------------------------------------


class TestMain:
    def test_rerun_is_a_no_op(self, clean_db, monkeypatch):
        """Simulates the real-world scenario end to end: an old database that
        has never seen schema_migrations at all gets `python -m
        app.schema_migrate` run twice in a row. The second run must change
        nothing.

        Built in a throwaway schema from _OLD_INIT_SQL (same technique as
        TestSchemaInvariant), not by wiping schema_migrations on `public`:
        replaying 0004/0015 there would fail, since `public` has no
        users.role column anymore (0018).
        """
        monkeypatch.setenv("AUTH_USERS", "alice:hash1")
        monkeypatch.setenv("AUTH_ROLES", "")

        def user_count() -> int:
            with schema_migrate.db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SELECT count(*) FROM users")
                row = cur.fetchone()
                assert row is not None
                return row[0]

        with schema_migrate.db.get_conn() as conn, _throwaway_schema(conn, "main_rerun_test", _OLD_INIT_SQL):
            schema_migrate.main()
            after_first_run = user_count()

            schema_migrate.main()

            assert user_count() == after_first_run == 1
            with schema_migrate.db.get_conn() as conn2, conn2.cursor(row_factory=tuple_row) as cur:
                cur.execute("SELECT version, count(*) FROM schema_migrations GROUP BY version")
                counts: dict[str, int] = dict(cur.fetchall())
            assert all(n == 1 for n in counts.values())
            assert {os.path.basename(p) for p in schema_migrate._migration_files()} <= set(counts)


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
# Role-value translation to English (0004)
# --------------------------------------------------------------------------


class TestTranslateRoleValuesToEnglish:
    """app/migrations/0004_translate_role_values_to_english.sql, exercised
    against real `users` rows with the OLD Spanish role values, in a
    throwaway schema with the historical users.role column (gone from
    `public` since 0018): the pre-0015 users table plus 0015's roles."""

    @staticmethod
    def _allow_legacy_spanish_roles(cur: psycopg.Cursor) -> None:
        """users.role is a FK to roles(name) since 0015, so the old Spanish
        values need a roles row before a user can hold them."""
        cur.execute(
            "INSERT INTO roles (name, label) VALUES "
            "('produccion', 'produccion'), ('punto_venta', 'punto_venta') "
            "ON CONFLICT DO NOTHING"
        )

    def test_translates_produccion_and_punto_venta_leaves_admin_alone(self, db_conn):
        with _throwaway_schema(db_conn, "role_values_0004_test", _PRE_0015_USERS_SQL, _0015_SQL):
            with db_conn.cursor(row_factory=tuple_row) as cur:
                self._allow_legacy_spanish_roles(cur)
                cur.execute(
                    "INSERT INTO users (username, password_hash, role) VALUES "
                    "('old_prod', 'h1', 'produccion'), "
                    "('old_pos', 'h2', 'punto_venta'), "
                    "('old_admin', 'h3', 'admin')"
                )
            db_conn.commit()

            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute(_0004_SQL)
            db_conn.commit()

            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SELECT username, role FROM users")
                rows = dict(cur.fetchall())
        assert rows == {
            "old_prod": "production",
            "old_pos": "point_of_sale",
            "old_admin": "admin",
        }

    def test_rerun_is_a_no_op(self, db_conn):
        with _throwaway_schema(db_conn, "role_values_0004_test", _PRE_0015_USERS_SQL, _0015_SQL):
            with db_conn.cursor(row_factory=tuple_row) as cur:
                self._allow_legacy_spanish_roles(cur)
                cur.execute(
                    "INSERT INTO users (username, password_hash, role) VALUES "
                    "('old_prod2', 'h1', 'produccion')"
                )
            db_conn.commit()

            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute(_0004_SQL)
                cur.execute(_0004_SQL)
            db_conn.commit()

            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SELECT role FROM users WHERE username = 'old_prod2'")
                role = cur.fetchone()[0]
        assert role == "production"


# --------------------------------------------------------------------------
# Roles and permissions (0015)
# --------------------------------------------------------------------------

_SEEDED_ROLES = {
    ("admin", "Administrador", True),
    ("point_of_sale", "Punto de venta", True),
    ("production", "Producción", True),
}

_SEEDED_ROLE_PERMISSIONS = {
    "admin": {
        "orders.capture", "cash_summary.view", "declarations.review", "production.view",
        "production.manage", "production.deliver", "records.view", "records.delete",
        "duplicates.review", "inventory.manage", "pricing.manage", "customers.manage",
        "users.manage",
    },
    "point_of_sale": {
        "orders.capture", "cash_summary.view", "declarations.review", "records.view",
        "production.view", "production.deliver",
    },
    "production": {"records.view", "production.view", "production.manage", "production.deliver"},
}

# The users table exactly as it was before 0015 (no FK on role).
_PRE_0015_USERS_SQL = """
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT now()
);
"""


class TestRolesAndPermissions:
    """app/migrations/0015_roles_and_permissions.sql: the seed, the backfill
    of unknown users.role values, and the users_role_fkey FK."""

    SCHEMA = "roles_0015_test"

    def test_seeded_roles_and_permissions_match_exactly(self, db_conn):
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT name, label, is_system FROM roles")
            roles = set(cur.fetchall())
            cur.execute("SELECT role, permission FROM role_permissions")
            permissions: dict[str, set[str]] = {}
            for role, permission in cur.fetchall():
                permissions.setdefault(role, set()).add(permission)
        assert roles == _SEEDED_ROLES
        assert permissions == _SEEDED_ROLE_PERMISSIONS

    def test_user_roles_role_fkey_cascades_on_update(self, db_conn):
        """The FK that replaced users_role_fkey (0018) keeps its rule."""
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'user_roles_role_fkey' AND conrelid = to_regclass('public.user_roles')"
            )
            row = cur.fetchone()
        assert row is not None
        assert row[0] == "FOREIGN KEY (role) REFERENCES roles(name) ON UPDATE CASCADE"

    def test_unknown_role_is_backfilled_and_fk_added(self, db_conn):
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {self.SCHEMA} CASCADE")
            cur.execute(f"CREATE SCHEMA {self.SCHEMA}")
            cur.execute(f"SET search_path TO {self.SCHEMA}")
            cur.execute(_PRE_0015_USERS_SQL)
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES "
                "('alice', 'h1', 'admin'), ('old', 'h2', 'legacy_role')"
            )
        db_conn.commit()
        try:
            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute(_0015_SQL)
            db_conn.commit()

            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SELECT label, is_system FROM roles WHERE name = 'legacy_role'")
                legacy = cur.fetchone()
                cur.execute("SELECT count(*) FROM role_permissions WHERE role = 'legacy_role'")
                legacy_permissions = cur.fetchone()
                cur.execute(
                    "SELECT count(*) FROM pg_constraint "
                    "WHERE conname = 'users_role_fkey' AND conrelid = to_regclass('users')"
                )
                fkeys = cur.fetchone()
                # A second run raises nothing.
                cur.execute(_0015_SQL)
            db_conn.commit()
        finally:
            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SET search_path TO public")
                cur.execute(f"DROP SCHEMA IF EXISTS {self.SCHEMA} CASCADE")
            db_conn.commit()

        assert legacy == ("legacy_role", False)
        assert legacy_permissions == (0,)
        assert fkeys == (1,)


class TestPermissions:
    """app/migrations/0017_permissions.sql: the `permissions` catalog seed
    and the role_permissions_permission_fkey FK."""

    SCHEMA = "permissions_0017_test"

    def test_permissions_table_matches_permissions_catalog_exactly(self, db_conn):
        """Guard against the hand-copied seed SQL (init.sql AND
        0017_permissions.sql) drifting from app/auth/permissions.py's
        PERMISSIONS: same codes, same labels, same order."""
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT code, label FROM permissions ORDER BY sort_order")
            seeded = cur.fetchall()
        assert seeded == list(PERMISSIONS)

    def test_unknown_permission_is_deleted_and_fk_added(self, db_conn):
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {self.SCHEMA} CASCADE")
            cur.execute(f"CREATE SCHEMA {self.SCHEMA}")
            cur.execute(f"SET search_path TO {self.SCHEMA}")
            cur.execute(_PRE_0015_USERS_SQL)
            cur.execute(_0015_SQL)
            cur.execute("SELECT role, permission FROM role_permissions")
            known_before = set(cur.fetchall())
            cur.execute("INSERT INTO role_permissions (role, permission) VALUES ('production', 'gone.code')")
        db_conn.commit()
        try:
            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute(_0017_SQL)
            db_conn.commit()

            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SELECT role, permission FROM role_permissions")
                after = set(cur.fetchall())
                cur.execute(
                    "SELECT count(*) FROM pg_constraint "
                    "WHERE conname = 'role_permissions_permission_fkey' "
                    "AND conrelid = to_regclass('role_permissions')"
                )
                fkeys = cur.fetchone()
                # A second run raises nothing.
                cur.execute(_0017_SQL)
            db_conn.commit()
        finally:
            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SET search_path TO public")
                cur.execute(f"DROP SCHEMA IF EXISTS {self.SCHEMA} CASCADE")
            db_conn.commit()

        assert ("production", "gone.code") not in after
        assert after == known_before
        assert fkeys == (1,)


# --------------------------------------------------------------------------
# Several roles per user (0018) and a fresh init.sql
# --------------------------------------------------------------------------

# The users table exactly as it was before 0018: role with users_role_fkey.
_PRE_0018_USERS_SQL = """
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CONSTRAINT users_role_fkey REFERENCES roles(name) ON UPDATE CASCADE,
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT now()
);
"""

# roles + permissions + role_permissions as 0015 and 0017 leave them.
_PRE_0018_ROLES_SQL = """
CREATE TABLE roles (
    name TEXT PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    is_system BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMP DEFAULT now()
);
CREATE TABLE permissions (
    code TEXT PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL
);
CREATE TABLE role_permissions (
    role TEXT NOT NULL REFERENCES roles(name) ON DELETE CASCADE ON UPDATE CASCADE,
    permission TEXT NOT NULL CONSTRAINT role_permissions_permission_fkey
        REFERENCES permissions(code) ON DELETE CASCADE ON UPDATE CASCADE,
    PRIMARY KEY (role, permission)
);
INSERT INTO roles (name, label, is_system) VALUES
    ('admin', 'Administrador', true),
    ('point_of_sale', 'Punto de venta', true),
    ('production', 'Producción', true),
    ('stock', 'Bodega', false);
INSERT INTO permissions (code, label, sort_order) VALUES ('inventory.manage', 'Gestionar inventario', 10);
INSERT INTO role_permissions (role, permission) VALUES ('stock', 'inventory.manage');
"""


class TestUserRoles:
    """app/migrations/0018_user_roles.sql: every users.role value becomes one
    user_roles row, then the column is dropped."""

    def test_0018_backfills_user_roles_and_drops_users_role(self, db_conn):
        def user_roles() -> list[tuple]:
            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute(
                    "SELECT u.username, ur.role, ur.assigned_by, ur.assigned_at "
                    "FROM user_roles ur JOIN users u ON u.id = ur.user_id ORDER BY u.username, ur.role"
                )
                return cur.fetchall()

        with _throwaway_schema(db_conn, "user_roles_0018_test", _PRE_0018_ROLES_SQL, _PRE_0018_USERS_SQL):
            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute(
                    "INSERT INTO users (username, password_hash, role, active) VALUES "
                    "('ana', 'h1', 'admin', true), ('beto', 'h2', 'point_of_sale', true), "
                    "('caro', 'h3', 'production', false), ('dani', 'h4', 'stock', true)"
                )
                cur.execute(_0018_SQL)
            db_conn.commit()

            first = user_roles()
            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = 'users' AND column_name = 'role'"
                )
                role_columns = cur.fetchone()
                # A second run raises nothing.
                cur.execute(_0018_SQL)
            db_conn.commit()
            second = user_roles()

        assert [(username, role, assigned_by) for username, role, assigned_by, _at in first] == [
            ("ana", "admin", None),
            ("beto", "point_of_sale", None),
            ("caro", "production", None),
            ("dani", "stock", None),
        ]
        assert role_columns == (0,)
        assert second == first

    def test_fresh_init_sql_records_every_migration_and_applies_none(self, db_conn):
        """init.sql already contains every migration's schema, so it records
        each app/migrations/*.sql version and the runner replays none of them
        (0004 and 0015 would fail without users.role)."""
        expected = {os.path.basename(p) for p in schema_migrate._migration_files()}
        with _throwaway_schema(db_conn, "fresh_init_versions_test", _INIT_SQL):
            with db_conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SELECT version FROM schema_migrations")
                recorded = {row[0] for row in cur.fetchall()}
            newly_applied = schema_migrate.apply_sql_migrations(db_conn)
        assert recorded == expected
        assert newly_applied == []


class TestRefreshTokens:
    """app/migrations/0019_refresh_tokens.sql: the refresh_tokens table."""

    def test_0019_refresh_tokens_sql_is_safe_to_apply_twice_directly(self, db_conn):
        """Re-applying it on the current schema (which already has the table)
        raises nothing and keeps the table's rows."""
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "INSERT INTO refresh_tokens (user_id, token_hash, family_id, expires_at) "
                "SELECT id, 'h', gen_random_uuid(), now() FROM users ORDER BY id LIMIT 1"
            )
            cur.execute(_0019_SQL)
            cur.execute(_0019_SQL)
            cur.execute("SELECT count(*) FROM refresh_tokens")
            assert cur.fetchone() == (1,)


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
