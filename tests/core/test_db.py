"""Unit tests for app.core.db that don't need a real Postgres connection.

_conninfo_kwargs() reads DB_HOST/DB_PORT/POSTGRES_* straight from os.environ
on every call (see module docstring in app/core/db/__init__.py) and returns
them as the kwargs get_conn()'s connection pool connects with. These tests
call _conninfo_kwargs() directly to verify exactly which arguments it builds,
including its DB_HOST/DB_PORT fallback defaults -- which tests/conftest.py
always overrides for safety, so nothing else in this suite exercises that
fallback path. (Pre-Fase-1d, get_conn() itself called psycopg.connect()
directly and these tests mocked that; since the pool, get_conn() is a
@contextmanager generator that doesn't build the pool -- or call
psycopg.connect() at all -- until entered with `with`, and the pool is built
once and reused rather than reading the environment per connection, so
_conninfo_kwargs() is now the right unit to test this against.)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import dict_row, tuple_row

from app.core import db
from app.core.models import ExtractedRow
from app.core.normalize import INVENTORY_CATEGORIES, PLATE_TYPES


class TestConninfoKwargs:
    def test_passes_through_all_env_vars(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "myhost")
        monkeypatch.setenv("DB_PORT", "1234")
        monkeypatch.setenv("POSTGRES_DB", "mydb")
        monkeypatch.setenv("POSTGRES_USER", "myuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "mypassword")

        assert db._conninfo_kwargs() == {
            "host": "myhost", "port": "1234", "dbname": "mydb",
            "user": "myuser", "password": "mypassword",
            "row_factory": dict_row,
        }

    def test_host_defaults_to_db_when_unset(self, monkeypatch):
        monkeypatch.delenv("DB_HOST", raising=False)
        monkeypatch.setenv("POSTGRES_DB", "mydb")
        monkeypatch.setenv("POSTGRES_USER", "myuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "mypassword")

        assert db._conninfo_kwargs()["host"] == "db"

    def test_port_defaults_to_5432_when_unset(self, monkeypatch):
        monkeypatch.delenv("DB_PORT", raising=False)
        monkeypatch.setenv("POSTGRES_DB", "mydb")
        monkeypatch.setenv("POSTGRES_USER", "myuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "mypassword")

        assert db._conninfo_kwargs()["port"] == "5432"

    def test_missing_required_postgres_db_raises(self, monkeypatch):
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        monkeypatch.setenv("POSTGRES_USER", "myuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "mypassword")

        with pytest.raises(KeyError):
            db._conninfo_kwargs()


# ----------------------------------------------------------------------------
# Everything below needs a real Postgres connection (see tests/conftest.py:
# `db_conn` truncates all 4 tables via `clean_db`, then hands back a live
# connection). These call db.py's own insert_order/insert_plate/
# insert_blank_preparation for setup -- deliberately bypassing app.core.ingestion
# and app.core.normalize -- so a failure here points at db.py itself, not at
# normalization or the ingestion pipeline (already covered elsewhere).
# ----------------------------------------------------------------------------


def make_row(**overrides: object) -> ExtractedRow:
    defaults: dict[str, object] = {
        "order_number": "1",
        "order_date": date(2026, 1, 1),
        "requested_by": "PEDRO",
        "plate_type": "CARRO PARTICULAR",
    }
    defaults.update(overrides)
    return ExtractedRow(**defaults)


def _insert_order(conn, *, order_number: str, order_date, requested_by: str) -> int:
    """Back-compat shim for this file's many "just need an order header to
    exist" setup calls, written before Fase 2 grew db.insert_order's real
    signature to (row: ExtractedRow, customer_id, created_by) -- see
    app/core/db/orders.py. Builds the minimal ExtractedRow insert_order
    actually needs (plate_type is required by the model but irrelevant to
    production_orders -- insert_order never reads it) so none of this file's
    order-header-only test setup has to change. Tests of insert_order's OWN
    behavior (TestInsertOrder below) call db.insert_order directly instead.
    """
    return db.insert_order(
        conn,
        row=ExtractedRow(order_number=order_number, order_date=order_date, requested_by=requested_by,
                          plate_type="MOTO PARTICULAR"),
        customer_id=None,
        created_by=None,
    )


def _insert_plate_get_id(conn, order_id: int, **row_kwargs: object) -> int:
    """Insert a plate via db.insert_plate and return its new id (insert_plate
    itself returns None -- there's no RETURNING id in its INSERT)."""
    is_dup = bool(row_kwargs.pop("is_possible_duplicate", False))
    row = make_row(**row_kwargs)
    db.insert_plate(conn, order_id=order_id, row=row, is_possible_duplicate=is_dup)
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT id FROM plates WHERE order_id = %s ORDER BY id DESC LIMIT 1", (order_id,))
        return cur.fetchone()[0]


class TestInsertOrder:
    """insert_order's Fase 2 signature: takes the full row + customer_id +
    created_by, instead of order_number/order_date/requested_by kwargs."""

    def test_inserts_the_header_fields_from_the_row(self, db_conn):
        order_id = db.insert_order(
            db_conn, row=make_row(order_number="2000", requested_by="PEDRO"),
            customer_id=None, created_by=None,
        )
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT order_number, order_date, requested_by FROM production_orders "
                        "WHERE id = %s", (order_id,))
            assert cur.fetchone() == ("2000", date(2026, 1, 1), "PEDRO")

    def test_ignores_plate_level_fields_on_the_row(self, db_conn):
        """plate_type/quantity/etc. are irrelevant to production_orders --
        insert_order must not choke on (or otherwise use) them."""
        order_id = db.insert_order(
            db_conn, row=make_row(plate_type="MOTO PARTICULAR", quantity=99),
            customer_id=None, created_by=None,
        )
        assert order_id is not None

    def test_stores_customer_id(self, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "PTO.V")
        order_id = db.insert_order(
            db_conn, row=make_row(requested_by="PTO.V"), customer_id=customer_id, created_by=None,
        )
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT customer_id FROM production_orders WHERE id = %s", (order_id,))
            assert cur.fetchone() == (customer_id,)

    def test_customer_id_may_be_null(self, db_conn):
        order_id = db.insert_order(db_conn, row=make_row(), customer_id=None, created_by=None)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT customer_id FROM production_orders WHERE id = %s", (order_id,))
            assert cur.fetchone() == (None,)

    def test_stores_created_by(self, db_conn):
        user_id = db.create_user(db_conn, username="alice", password_hash="h", role="admin")
        order_id = db.insert_order(db_conn, row=make_row(), customer_id=None, created_by=user_id)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT created_by FROM production_orders WHERE id = %s", (order_id,))
            assert cur.fetchone() == (user_id,)

    def test_stores_every_pos_field(self, db_conn):
        row = make_row(
            receipt_type="FACTURA", receipt_number="F-100", payment_method="EFECTIVO",
            tracking_number="G-1", client_order_number="55", delivery_date=date(2026, 2, 1),
            payment_date=date(2026, 1, 20), dispatched_by="JUAN", received_by="MARIA",
            notes="Entrega urgente",
        )
        order_id = db.insert_order(db_conn, row=row, customer_id=None, created_by=None)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                """
                SELECT receipt_type, receipt_number, payment_method, tracking_number,
                       client_order_number, delivery_date, payment_date, dispatched_by,
                       received_by, notes
                FROM production_orders WHERE id = %s
                """,
                (order_id,),
            )
            assert cur.fetchone() == (
                "FACTURA", "F-100", "EFECTIVO", "G-1", "55",
                date(2026, 2, 1), date(2026, 1, 20), "JUAN", "MARIA", "Entrega urgente",
            )

    def test_pos_fields_default_null(self, db_conn):
        order_id = db.insert_order(db_conn, row=make_row(), customer_id=None, created_by=None)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                """
                SELECT receipt_type, receipt_number, payment_method, tracking_number,
                       client_order_number, delivery_date, payment_date, dispatched_by,
                       received_by, notes, created_by
                FROM production_orders WHERE id = %s
                """,
                (order_id,),
            )
            assert cur.fetchone() == (None,) * 11


class TestUpdateOrder:
    """customer_id defaults to a private sentinel (NOT None) -- omitting it
    must leave the column completely untouched, since app/web/blueprints/
    records.py's record_save (Fase 3's job) doesn't pass it yet and must not
    have every existing Registro-detail save silently wipe customer_id."""

    def test_updates_header_fields(self, db_conn):
        order_id = _insert_order(db_conn, order_number="2001", order_date=date(2026, 1, 1),
                                  requested_by="PEDRO")
        db.update_order(db_conn, order_id, order_number="2001-B", order_date=date(2026, 2, 1),
                        requested_by="JUAN")
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT order_number, order_date, requested_by FROM production_orders "
                        "WHERE id = %s", (order_id,))
            assert cur.fetchone() == ("2001-B", date(2026, 2, 1), "JUAN")

    def test_omitting_customer_id_leaves_it_untouched(self, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "PTO.V")
        order_id = db.insert_order(
            db_conn, row=make_row(requested_by="PTO.V"), customer_id=customer_id, created_by=None,
        )
        db.update_order(db_conn, order_id, order_number="2002", order_date=date(2026, 1, 1),
                        requested_by="PTO.V")
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT customer_id FROM production_orders WHERE id = %s", (order_id,))
            assert cur.fetchone() == (customer_id,)

    def test_explicit_customer_id_updates_it(self, db_conn):
        order_id = _insert_order(db_conn, order_number="2003", order_date=date(2026, 1, 1),
                                  requested_by="PEDRO")
        new_customer_id = db.resolve_or_create_customer(db_conn, "MARIA")
        db.update_order(db_conn, order_id, order_number="2003", order_date=date(2026, 1, 1),
                        requested_by="MARIA", customer_id=new_customer_id)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT customer_id FROM production_orders WHERE id = %s", (order_id,))
            assert cur.fetchone() == (new_customer_id,)

    def test_pos_fields_default_to_null_when_omitted(self, db_conn):
        """Unlike customer_id, the 10 Fase 3 POS fields have no sentinel --
        record_save (their only real caller) always submits the whole form,
        so omitting one here really does mean 'clear it'."""
        order_id = _insert_order(db_conn, order_number="2005", order_date=date(2026, 1, 1),
                                  requested_by="PEDRO")
        with db_conn.cursor() as cur:
            cur.execute(
                "UPDATE production_orders SET payment_method = 'EFECTIVO', notes = 'algo' WHERE id = %s",
                (order_id,),
            )
        db.update_order(db_conn, order_id, order_number="2005", order_date=date(2026, 1, 1),
                        requested_by="PEDRO")
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT payment_method, notes FROM production_orders WHERE id = %s", (order_id,))
            assert cur.fetchone() == (None, None)

    def test_pos_fields_are_written_when_given(self, db_conn):
        order_id = _insert_order(db_conn, order_number="2006", order_date=date(2026, 1, 1),
                                  requested_by="PEDRO")
        db.update_order(
            db_conn, order_id, order_number="2006", order_date=date(2026, 1, 1), requested_by="PEDRO",
            receipt_type="FACTURA", receipt_number="F-1", payment_method="TRANSFERENCIA",
            tracking_number="G-1", client_order_number="P-1", delivery_date=date(2026, 1, 5),
            payment_date=date(2026, 1, 6), dispatched_by="ANA", received_by="LUIS", notes="ok",
        )
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT receipt_type, receipt_number, payment_method, tracking_number, "
                "client_order_number, delivery_date, payment_date, dispatched_by, received_by, notes "
                "FROM production_orders WHERE id = %s",
                (order_id,),
            )
            assert cur.fetchone() == (
                "FACTURA", "F-1", "TRANSFERENCIA", "G-1", "P-1",
                date(2026, 1, 5), date(2026, 1, 6), "ANA", "LUIS", "ok",
            )

    def test_explicit_none_clears_customer_id(self, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "PTO.V")
        order_id = db.insert_order(
            db_conn, row=make_row(requested_by="PTO.V"), customer_id=customer_id, created_by=None,
        )
        db.update_order(db_conn, order_id, order_number="2004", order_date=date(2026, 1, 1),
                        requested_by="//", customer_id=None)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT customer_id FROM production_orders WHERE id = %s", (order_id,))
            assert cur.fetchone() == (None,)


class TestInsertPlate:
    """City is stored per PLATE (plates.city), not per order."""

    def test_stores_the_rows_own_city(self, db_conn):
        order_id = _insert_order(db_conn, order_number="10", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(city="MEDELLIN"),
                        is_possible_duplicate=False)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT city FROM plates WHERE order_id = %s", (order_id,))
            assert cur.fetchone()[0] == "MEDELLIN"

    def test_city_may_be_null(self, db_conn):
        order_id = _insert_order(db_conn, order_number="11", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(city=None),
                        is_possible_duplicate=False)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT city FROM plates WHERE order_id = %s", (order_id,))
            assert cur.fetchone()[0] is None

    def test_two_plates_in_one_order_keep_different_cities(self, db_conn):
        order_id = _insert_order(db_conn, order_number="12", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        for city in ("CALI", "PASTO"):
            db.insert_plate(db_conn, order_id=order_id, row=make_row(city=city),
                            is_possible_duplicate=False)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT city FROM plates WHERE order_id = %s ORDER BY id", (order_id,))
            assert [r[0] for r in cur.fetchall()] == ["CALI", "PASTO"]

    def test_stores_unit_price_reason_and_vehicle_brand(self, db_conn):
        order_id = _insert_order(db_conn, order_number="13", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(
            db_conn, order_id=order_id,
            row=make_row(unit_price=Decimal("150000"), reason="DETERIORO", vehicle_brand="CHEVROLET"),
            is_possible_duplicate=False,
        )
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT unit_price, reason, vehicle_brand FROM plates WHERE order_id = %s",
                        (order_id,))
            assert cur.fetchone() == (Decimal("150000"), "DETERIORO", "CHEVROLET")

    def test_unit_price_reason_vehicle_brand_default_null(self, db_conn):
        order_id = _insert_order(db_conn, order_number="14", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(), is_possible_duplicate=False)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT unit_price, reason, vehicle_brand FROM plates WHERE order_id = %s",
                        (order_id,))
            assert cur.fetchone() == (None, None, None)


class TestInventoryView:
    def test_computes_available_from_prepared_and_consumed(self, db_conn):
        db.insert_blank_preparation(db_conn, prep_date=date(2026, 1, 1), category="CARRO PARTICULAR",
                                     quantity=100, prepared_by="GILMER")
        order_id = _insert_order(db_conn, order_number="1", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_type="CARRO PARTICULAR", quantity=30),
                         is_possible_duplicate=False)
        db.recalc_inventory(db_conn)

        rows = {r["category"]: r for r in db.inventory_view(db_conn)}
        assert rows["CARRO PARTICULAR"]["prepared"] == 100
        assert rows["CARRO PARTICULAR"]["consumed"] == 30
        assert rows["CARRO PARTICULAR"]["available"] == 70
        assert rows["CARRO PARTICULAR"]["wasted"] == 0

    def test_wasted_counts_only_plate_annulled_rows_but_they_still_count_as_consumed(self, db_conn):
        db.insert_blank_preparation(db_conn, prep_date=date(2026, 1, 1), category="MOTO PARTICULAR",
                                     quantity=50, prepared_by=None)
        order_id = _insert_order(db_conn, order_number="2", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="MOTO PARTICULAR", quantity=5, is_plate_annulled=True),
                         is_possible_duplicate=False)
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="MOTO PARTICULAR", quantity=10, is_plate_annulled=False),
                         is_possible_duplicate=False)
        db.recalc_inventory(db_conn)

        rows = {r["category"]: r for r in db.inventory_view(db_conn)}
        assert rows["MOTO PARTICULAR"]["consumed"] == 15  # both count toward consumed
        assert rows["MOTO PARTICULAR"]["wasted"] == 5  # only the annulled one

    def test_category_with_no_annulled_plates_defaults_wasted_to_zero(self, db_conn):
        db.insert_blank_preparation(db_conn, prep_date=date(2026, 1, 1), category="PÚBLICO",
                                     quantity=20, prepared_by=None)
        db.recalc_inventory(db_conn)
        rows = {r["category"]: r for r in db.inventory_view(db_conn)}
        assert rows["PÚBLICO"]["wasted"] == 0

    def test_no_activity_returns_the_3_seeded_categories_all_zeroed(self, db_conn):
        """Since Fase 1b, inventory_categories is seeded with the 3 fixed
        materials from birth (required so blank_preparations' FK to it never
        blocks the very first ALISTAMIENTO -- see tests/conftest.py's
        clean_db) -- so an otherwise-untouched DB no longer returns an empty
        list, but the 3 rows with everything at zero."""
        rows = {r["category"]: r for r in db.inventory_view(db_conn)}
        assert set(rows) == set(INVENTORY_CATEGORIES)
        for row in rows.values():
            assert (row["prepared"], row["consumed"], row["available"], row["wasted"]) == (0, 0, 0, 0)


class TestOrnamentalCount:
    def test_returns_zero_when_no_ornamental_plates(self, db_conn):
        assert db.ornamental_count(db_conn) == 0

    def test_sums_quantity_across_plates(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_type="ORNAMENTAL", quantity=4),
                         is_possible_duplicate=False)
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_type="ORNAMENTAL", quantity=6),
                         is_possible_duplicate=False)
        assert db.ornamental_count(db_conn) == 10

    def test_excludes_voided_plates(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="ORNAMENTAL", quantity=4, is_voided=True),
                         is_possible_duplicate=False)
        assert db.ornamental_count(db_conn) == 0

    def test_non_ornamental_plates_not_counted(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_type="CARRO PARTICULAR", quantity=9),
                         is_possible_duplicate=False)
        assert db.ornamental_count(db_conn) == 0


class TestDistinctRequestedBy:
    def test_orders_by_frequency_then_alphabetically(self, db_conn):
        _insert_order(db_conn, order_number="1", order_date=date(2026, 1, 1), requested_by="PEDRO")
        _insert_order(db_conn, order_number="2", order_date=date(2026, 1, 1), requested_by="PEDRO")
        _insert_order(db_conn, order_number="3", order_date=date(2026, 1, 1), requested_by="JUAN")
        assert db.distinct_requested_by(db_conn) == ["PEDRO", "JUAN"]

    def test_drops_punctuation_only_junk(self, db_conn):
        _insert_order(db_conn, order_number="1", order_date=date(2026, 1, 1), requested_by="//")
        _insert_order(db_conn, order_number="2", order_date=date(2026, 1, 1), requested_by="PEDRO")
        assert db.distinct_requested_by(db_conn) == ["PEDRO"]

    def test_empty_db_returns_empty_list(self, db_conn):
        assert db.distinct_requested_by(db_conn) == []


class TestDistinctCities:
    """Cities come from PLATES now (production_orders has no city column)."""

    @staticmethod
    def _order_with_cities(db_conn, order_number: str, *cities: str | None) -> int:
        order_id = _insert_order(db_conn, order_number=order_number,
                                   order_date=date(2026, 1, 1), requested_by="PEDRO")
        for c in cities:
            db.insert_plate(db_conn, order_id=order_id, row=make_row(city=c),
                            is_possible_duplicate=False)
        return order_id

    def test_orders_by_frequency_then_alphabetically(self, db_conn):
        self._order_with_cities(db_conn, "1", "BOGOTA")
        self._order_with_cities(db_conn, "2", "BOGOTA")
        self._order_with_cities(db_conn, "3", "CALI")
        assert db.distinct_cities(db_conn) == ["BOGOTA", "CALI"]

    def test_reads_from_plates_not_orders(self, db_conn):
        """Two cities under ONE order both show up -- the old order-level column
        could only ever have held one of them."""
        self._order_with_cities(db_conn, "1", "CALI", "MEDELLIN")
        assert db.distinct_cities(db_conn) == ["CALI", "MEDELLIN"]

    def test_drops_anulad_marker_entries(self, db_conn):
        self._order_with_cities(db_conn, "1", "ANULADO")
        self._order_with_cities(db_conn, "2", "BOGOTA")
        assert db.distinct_cities(db_conn) == ["BOGOTA"]

    def test_drops_punctuation_only_junk(self, db_conn):
        self._order_with_cities(db_conn, "1", "--")
        self._order_with_cities(db_conn, "2", "CALI")
        assert db.distinct_cities(db_conn) == ["CALI"]

    def test_null_or_blank_city_excluded(self, db_conn):
        self._order_with_cities(db_conn, "1", None)
        assert db.distinct_cities(db_conn) == []


class TestRecentOrders:
    def test_summarizes_plate_labels_per_row_kind(self, db_conn):
        order_id = _insert_order(db_conn, order_number="500", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="AAA111"),
                         is_possible_duplicate=False)
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", is_batch_replacement=True),
                         is_possible_duplicate=False)
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", is_voided=True),
                         is_possible_duplicate=False)
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_type="CARRO PARTICULAR"),
                         is_possible_duplicate=False)  # no plate_number, not batch/voided -> 'sin placa'

        rows = db.recent_orders(db_conn)
        assert len(rows) == 1
        row = rows[0]
        assert row.order_number == "500"
        assert row.n_plates == 4
        assert row.total_qty == 4
        assert row.plates == "AAA111, reposición, anulada, sin placa"

    def test_order_with_no_plates_shows_zero_counts_and_no_plates_label(self, db_conn):
        """A LEFT JOIN with zero matching plates still produces exactly one
        (all-NULL) joined row. The CASE's first branch (p.id IS NULL) catches
        that phantom row and yields NULL instead of falling through to a real
        plate-describing branch, so string_agg has nothing to aggregate and
        'plates' reads NULL -- distinct from a real 'sin placa' row (a plate
        line that was recorded but has no plate_number typed in, covered by
        test_summarizes_plate_labels_per_row_kind below)."""
        _insert_order(db_conn, order_number="501", order_date=date(2026, 1, 1), requested_by="PEDRO")
        rows = db.recent_orders(db_conn)
        assert rows[0].n_plates == 0
        assert rows[0].total_qty == 0
        assert rows[0].plates is None

    def test_cities_aggregates_the_orders_distinct_plate_cities(self, db_conn):
        order_id = _insert_order(db_conn, order_number="510", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        for city in ("CALI", "COLOMBIA", "CALI"):  # repeat -> deduped
            db.insert_plate(db_conn, order_id=order_id, row=make_row(city=city),
                            is_possible_duplicate=False)
        assert sorted(db.recent_orders(db_conn)[0].cities) == ["CALI", "COLOMBIA"]

    def test_sorted_by_order_date_not_by_when_it_was_inserted(self, db_conn):
        """A correction entered today for a months-old order (e.g. fixing a
        duplicate found during a review pass) must not jump to the top and
        bury today's real activity -- insertion order (created_at/id) is
        only a tiebreaker for orders sharing the same order_date."""
        _insert_order(db_conn, order_number="520", order_date=date(2026, 1, 1), requested_by="PEDRO")
        _insert_order(db_conn, order_number="521", order_date=date(2026, 6, 15), requested_by="PEDRO")
        assert [r.order_number for r in db.recent_orders(db_conn)] == ["521", "520"]


class TestFindOrdersByNumber:
    """Same row shape as recent_orders(), but WHERE order_number = %s exactly
    -- used to warn /ingest before it silently creates a second
    production_orders row under a number that already exists."""

    def test_no_match_returns_empty_list(self, db_conn):
        assert db.find_orders_by_number(db_conn, "ZZZ999") == []

    def test_exact_match_only_not_partial(self, db_conn):
        """A stray ILIKE-style partial match must never trigger this warning
        -- order_number '99' must not match an existing '1099'."""
        _insert_order(db_conn, order_number="1099", order_date=date(2026, 1, 1),
                         requested_by="PEDRO")
        assert db.find_orders_by_number(db_conn, "99") == []
        assert len(db.find_orders_by_number(db_conn, "1099")) == 1

    def test_finds_the_matching_order_with_its_plates_summarized(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1100", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="FON111"),
                         is_possible_duplicate=False)

        matches = db.find_orders_by_number(db_conn, "1100")
        assert len(matches) == 1
        assert matches[0].id == order_id
        assert matches[0].order_number == "1100"
        assert matches[0].requested_by == "PEDRO"
        assert matches[0].n_plates == 1
        assert matches[0].total_qty == 1
        assert matches[0].plates == "FON111"

    def test_returns_every_match_when_two_or_more_orders_share_the_number(self, db_conn):
        """This already happens for real (WCY449 / pedido 1030, see
        data/duplicados_revision_2026-08-15.md) -- no LIMIT, no picking one."""
        first_id = _insert_order(db_conn, order_number="1101", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        second_id = _insert_order(db_conn, order_number="1101", order_date=date(2026, 2, 1),
                                     requested_by="MARIA")

        matches = db.find_orders_by_number(db_conn, "1101")
        assert {m.id for m in matches} == {first_id, second_id}

    def test_other_order_numbers_are_not_returned(self, db_conn):
        _insert_order(db_conn, order_number="1102", order_date=date(2026, 1, 1),
                         requested_by="PEDRO")
        _insert_order(db_conn, order_number="1103", order_date=date(2026, 1, 1),
                         requested_by="PEDRO")
        matches = db.find_orders_by_number(db_conn, "1102")
        assert [m.order_number for m in matches] == ["1102"]


class TestSearchPlatesByNumber:
    """Finds a plate anywhere in the DB, not just the 50 most recent orders --
    the point is checking whether a plate was ever made before."""

    def test_exact_match(self, db_conn):
        order_id = _insert_order(db_conn, order_number="600", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="ARN911"),
                         is_possible_duplicate=False)
        results = db.search_plates_by_number(db_conn, "ARN911")
        assert len(results) == 1
        assert results[0]["plate_number"] == "ARN911"
        assert results[0]["order_number"] == "600"

    def test_partial_match(self, db_conn):
        order_id = _insert_order(db_conn, order_number="601", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="ARN911"),
                         is_possible_duplicate=False)
        assert [r["plate_number"] for r in db.search_plates_by_number(db_conn, "RN91")] == ["ARN911"]

    def test_query_is_cleaned_like_a_stored_plate_number(self, db_conn):
        """'arn 911' (lowercase, stray space) must still find 'ARN911'."""
        order_id = _insert_order(db_conn, order_number="602", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="ARN911"),
                         is_possible_duplicate=False)
        assert [r["plate_number"] for r in db.search_plates_by_number(db_conn, "arn 911")] == ["ARN911"]

    def test_matches_plate_to_for_ranges(self, db_conn):
        order_id = _insert_order(db_conn, order_number="603", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(
            db_conn, order_id=order_id,
            row=make_row(plate_number="HMF900", plate_to="HMF930"),
            is_possible_duplicate=False,
        )
        assert [r["plate_number"] for r in db.search_plates_by_number(db_conn, "HMF930")] == ["HMF900"]

    def test_not_limited_to_the_50_most_recent_orders(self, db_conn):
        """recent_orders() caps at 50; search must not inherit that limit --
        it exists specifically to reach plates recent_orders can't show."""
        for i in range(55):
            order_id = _insert_order(db_conn, order_number=str(700 + i), order_date=date(2026, 1, 1),
                                        requested_by="PEDRO")
            db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number=f"OLD{i:03d}"),
                             is_possible_duplicate=False)
        assert len(db.search_plates_by_number(db_conn, "OLD")) == 55

    def test_no_match_returns_empty_list(self, db_conn):
        assert db.search_plates_by_number(db_conn, "ZZZ999") == []

    def test_most_recent_order_first(self, db_conn):
        older = _insert_order(db_conn, order_number="610", order_date=date(2026, 1, 1),
                                 requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=older, row=make_row(plate_number="DUP001"),
                         is_possible_duplicate=False)
        newer = _insert_order(db_conn, order_number="611", order_date=date(2026, 2, 1),
                                 requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=newer, row=make_row(plate_number="DUP001"),
                         is_possible_duplicate=False)
        results = db.search_plates_by_number(db_conn, "DUP001")
        assert [r["order_number"] for r in results] == ["611", "610"]

    def test_cities_is_a_single_element_list_for_a_one_city_order(self, db_conn):
        order_id = _insert_order(db_conn, order_number="511", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(city="PASTO"),
                        is_possible_duplicate=False)
        assert db.recent_orders(db_conn)[0].cities == ["PASTO"]

    def test_cities_is_empty_when_no_plate_has_one(self, db_conn):
        order_id = _insert_order(db_conn, order_number="512", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(city=None),
                        is_possible_duplicate=False)
        assert db.recent_orders(db_conn)[0].cities == []

    def test_cities_is_empty_for_an_order_with_no_plates_at_all(self, db_conn):
        _insert_order(db_conn, order_number="513", order_date=date(2026, 1, 1),
                        requested_by="PEDRO")
        assert db.recent_orders(db_conn)[0].cities == []

    def test_most_recently_inserted_order_first(self, db_conn):
        _insert_order(db_conn, order_number="600", order_date=date(2026, 1, 1), requested_by="PEDRO")
        _insert_order(db_conn, order_number="601", order_date=date(2026, 1, 1), requested_by="PEDRO")
        rows = db.recent_orders(db_conn)
        assert [r.order_number for r in rows] == ["601", "600"]

    def test_limit_caps_number_of_rows_returned(self, db_conn):
        for i in range(5):
            _insert_order(db_conn, order_number=str(700 + i), order_date=date(2026, 1, 1),
                             requested_by="PEDRO")
        rows = db.recent_orders(db_conn, limit=2)
        assert len(rows) == 2


class TestSearchOrdersByNumber:
    """Finds an order anywhere in the DB by (partial) order_number -- the
    counterpart to TestSearchPlatesByNumber, but for looking up a specific
    old order directly instead of a plate."""

    def test_exact_match(self, db_conn):
        _insert_order(db_conn, order_number="800", order_date=date(2026, 1, 1),
                         requested_by="PEDRO")
        results = db.search_orders_by_number(db_conn, "800")
        assert len(results) == 1
        assert results[0].order_number == "800"
        assert results[0].requested_by == "PEDRO"

    def test_partial_match(self, db_conn):
        _insert_order(db_conn, order_number="80042", order_date=date(2026, 1, 1),
                         requested_by="PEDRO")
        assert [r.order_number for r in db.search_orders_by_number(db_conn, "004")] == ["80042"]

    def test_query_is_not_cleaned_like_a_plate_number(self, db_conn):
        """order_number is plain text/digits, not a plate -- unlike
        search_plates_by_number this must NOT run the query through
        clean_plate(), so a query with internal spaces only matches an
        order_number that literally contains them."""
        _insert_order(db_conn, order_number="80 1", order_date=date(2026, 1, 1),
                         requested_by="PEDRO")
        assert [r.order_number for r in db.search_orders_by_number(db_conn, "80 1")] == ["80 1"]
        assert db.search_orders_by_number(db_conn, "801") == []

    def test_leading_and_trailing_whitespace_is_stripped(self, db_conn):
        _insert_order(db_conn, order_number="802", order_date=date(2026, 1, 1),
                         requested_by="PEDRO")
        assert [r.order_number for r in db.search_orders_by_number(db_conn, "  802  ")] == ["802"]

    def test_case_insensitive(self, db_conn):
        """Most order_number values are digits, but ILIKE is used (not LIKE)
        so a non-numeric order_number still matches regardless of case."""
        _insert_order(db_conn, order_number="ABC-803", order_date=date(2026, 1, 1),
                         requested_by="PEDRO")
        assert [r.order_number for r in db.search_orders_by_number(db_conn, "abc-803")] == ["ABC-803"]

    def test_no_match_returns_empty_list(self, db_conn):
        assert db.search_orders_by_number(db_conn, "ZZZ999") == []

    def test_summarizes_plates_same_as_recent_orders(self, db_conn):
        """Same row shape as recent_orders() -- cities/n_plates/total_qty/
        plates are all derived the same way so the result can be rendered
        with the same table partial."""
        order_id = _insert_order(db_conn, order_number="804", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="QRS111", city="CALI"),
                         is_possible_duplicate=False)
        results = db.search_orders_by_number(db_conn, "804")
        assert results[0].n_plates == 1
        assert results[0].total_qty == 1
        assert results[0].plates == "QRS111"
        assert results[0].cities == ["CALI"]

    def test_most_recent_order_first(self, db_conn):
        _insert_order(db_conn, order_number="900-A", order_date=date(2026, 1, 1),
                         requested_by="PEDRO")
        _insert_order(db_conn, order_number="900-B", order_date=date(2026, 2, 1),
                         requested_by="PEDRO")
        assert [r.order_number for r in db.search_orders_by_number(db_conn, "900")] == \
            ["900-B", "900-A"]

    def test_limit_caps_number_of_rows_returned(self, db_conn):
        for i in range(5):
            _insert_order(db_conn, order_number=f"910-{i}", order_date=date(2026, 1, 1),
                             requested_by="PEDRO")
        assert len(db.search_orders_by_number(db_conn, "910", limit=2)) == 2


class TestOrderDetail:
    def test_returns_order_and_its_plates(self, db_conn):
        order_id = _insert_order(db_conn, order_number="900", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="ZZZ111", quantity=2,
                                      city="BOGOTA"),
                         is_possible_duplicate=False)

        order, plates = db.order_detail(db_conn, order_id)
        assert order["order_number"] == "900"
        assert order["requested_by"] == "PEDRO"
        assert "city" not in order  # city lives on the plate, not the order
        assert len(plates) == 1
        assert plates[0]["plate_number"] == "ZZZ111"
        assert plates[0]["quantity"] == 2
        assert plates[0]["city"] == "BOGOTA"

    def test_plates_keep_their_own_distinct_cities(self, db_conn):
        """The original bug: two plates in ONE order kept only the first city."""
        order_id = _insert_order(db_conn, order_number="901", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        for plate, city in (("AAA111", "MEDELLIN"), ("BBB222", "PASTO")):
            db.insert_plate(db_conn, order_id=order_id,
                             row=make_row(plate_number=plate, city=city),
                             is_possible_duplicate=False)

        _order, plates = db.order_detail(db_conn, order_id)
        assert [(p["plate_number"], p["city"]) for p in plates] == [
            ("AAA111", "MEDELLIN"), ("BBB222", "PASTO"),
        ]

    def test_nonexistent_order_id_returns_none_and_empty_list(self, db_conn):
        order, plates = db.order_detail(db_conn, 999999)
        assert order is None
        assert plates == []

    def test_plates_ordered_by_id(self, db_conn):
        order_id = _insert_order(db_conn, order_number="901", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="A1"), is_possible_duplicate=False)
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="A2"), is_possible_duplicate=False)
        _, plates = db.order_detail(db_conn, order_id)
        assert [p["plate_number"] for p in plates] == ["A1", "A2"]

    def test_plates_include_the_fase_3_per_line_pos_fields(self, db_conn):
        order_id = _insert_order(db_conn, order_number="903", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(
            db_conn, order_id=order_id,
            row=make_row(plate_number="POS111", reason="DETERIORO", vehicle_brand="RENAULT",
                         unit_price=Decimal("150000")),
            is_possible_duplicate=False,
        )
        _, plates = db.order_detail(db_conn, order_id)
        assert plates[0]["reason"] == "DETERIORO"
        assert plates[0]["vehicle_brand"] == "RENAULT"
        assert plates[0]["unit_price"] == Decimal("150000")

    def test_joins_the_resolved_customers_attributes(self, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "PTO.V")
        with db_conn.cursor() as cur:
            cur.execute(
                "UPDATE customers SET type = %s, phone = %s WHERE id = %s",
                ("EMPRESA", "3001234567", customer_id),
            )
        order_id = db.insert_order(
            db_conn, row=make_row(requested_by="PTO.V"), customer_id=customer_id, created_by=None,
        )
        order, _plates = db.order_detail(db_conn, order_id)
        assert order is not None
        assert order["customer_id"] == customer_id
        assert order["customer_name"] == "PTO.V"
        assert order["customer_type"] == "EMPRESA"
        assert order["customer_phone"] == "3001234567"

    def test_null_customer_id_still_returns_the_order_via_left_join(self, db_conn):
        """The Fase 1 backfill left exactly one historical order with
        customer_id NULL -- order_detail must not silently drop it (which a
        plain JOIN, instead of LEFT JOIN, would do)."""
        order_id = _insert_order(db_conn, order_number="902", order_date=date(2026, 1, 1),
                                    requested_by="//")
        order, _plates = db.order_detail(db_conn, order_id)
        assert order is not None
        assert order["customer_id"] is None
        assert order["customer_name"] is None


class TestDeleteOrder:
    def test_removes_order_and_its_plates(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1000", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="B1"), is_possible_duplicate=False)

        db.delete_order(db_conn, order_id)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE id = %s", (order_id,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM plates WHERE order_id = %s", (order_id,))
            assert cur.fetchone()[0] == 0

    def test_does_not_affect_other_orders(self, db_conn):
        keep_id = _insert_order(db_conn, order_number="1001", order_date=date(2026, 1, 1),
                                   requested_by="PEDRO")
        delete_id = _insert_order(db_conn, order_number="1002", order_date=date(2026, 1, 1),
                                     requested_by="PEDRO")
        db.delete_order(db_conn, delete_id)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE id = %s", (keep_id,))
            assert cur.fetchone()[0] == 1

    def test_nonexistent_order_id_is_a_silent_noop(self, db_conn):
        db.delete_order(db_conn, 999999)  # must not raise


class TestReviewRows:
    def test_returns_only_flagged_rows(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1100", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="FLAG1"),
                         is_possible_duplicate=True)
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="CLEAN1"),
                         is_possible_duplicate=False)

        rows = db.review_rows(db_conn)
        assert [r["plate_number"] for r in rows] == ["FLAG1"]
        assert rows[0]["order_number"] == "1100"

    def test_includes_the_plates_city(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1103", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_number="FLAG2", city="NEIVA"),
                         is_possible_duplicate=True)
        assert db.review_rows(db_conn)[0]["city"] == "NEIVA"

    def test_ordered_by_plate_number_then_order_date(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1101", order_date=date(2026, 1, 5),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="ZZZ"),
                         is_possible_duplicate=True)
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="AAA"),
                         is_possible_duplicate=True)
        rows = db.review_rows(db_conn)
        assert [r["plate_number"] for r in rows] == ["AAA", "ZZZ"]

    def test_empty_when_nothing_flagged(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1102", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="CLEAN2"),
                         is_possible_duplicate=False)
        assert db.review_rows(db_conn) == []


class TestUpdatePlate:
    def test_edits_fields_and_clears_review_flag(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1200", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        plate_id = _insert_plate_get_id(db_conn, order_id, plate_number="OLD111",
                                         quantity=1, is_possible_duplicate=True)

        db.update_plate(db_conn, plate_id, plate_type="MOTO PARTICULAR", plate_number="NEW222",
                         quantity=9, is_voided=True)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT plate_type, plate_number, quantity, is_voided, is_possible_duplicate "
                "FROM plates WHERE id = %s",
                (plate_id,),
            )
            assert cur.fetchone() == ("MOTO PARTICULAR", "NEW222", 9, True, False)

    def test_plate_number_can_be_cleared_to_null(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1201", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        plate_id = _insert_plate_get_id(db_conn, order_id, plate_number="HAS111")

        db.update_plate(db_conn, plate_id, plate_type="CARRO PARTICULAR", plate_number=None,
                         quantity=1, is_voided=False)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number FROM plates WHERE id = %s", (plate_id,))
            assert cur.fetchone() == (None,)

    def test_unchanged_plate_type_leaves_unit_price_untouched_even_if_quantity_changes(self, db_conn):
        """Case 2 of the re-snapshot rule: plate_type unchanged -> unit_price
        is left exactly as it was, no matter what else changes."""
        order_id = _insert_order(db_conn, order_number="1202", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.set_price(db_conn, "CARRO PARTICULAR", Decimal("999999"), "admin")  # must NOT be used
        plate_id = _insert_plate_get_id(db_conn, order_id, plate_type="CARRO PARTICULAR",
                                         unit_price=Decimal("150000"), quantity=1)

        db.update_plate(db_conn, plate_id, plate_type="CARRO PARTICULAR", plate_number=None,
                         quantity=5, is_voided=False)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT unit_price FROM plates WHERE id = %s", (plate_id,))
            assert cur.fetchone() == (Decimal("150000"),)

    def test_changed_plate_type_resnapshots_the_new_types_current_tariff(self, db_conn):
        """Case 3 of the re-snapshot rule: plate_type changes -> re-take the
        CURRENT tariff for the NEW type, discarding the old unit_price."""
        order_id = _insert_order(db_conn, order_number="1203", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.set_price(db_conn, "MOTO PARTICULAR", Decimal("90000"), "admin")
        plate_id = _insert_plate_get_id(db_conn, order_id, plate_type="CARRO PARTICULAR",
                                         unit_price=Decimal("150000"))

        db.update_plate(db_conn, plate_id, plate_type="MOTO PARTICULAR", plate_number=None,
                         quantity=1, is_voided=False)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT unit_price FROM plates WHERE id = %s", (plate_id,))
            assert cur.fetchone() == (Decimal("90000"),)

    def test_changed_plate_type_with_no_tariff_leaves_unit_price_null_not_zero(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1204", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        plate_id = _insert_plate_get_id(db_conn, order_id, plate_type="CARRO PARTICULAR",
                                         unit_price=Decimal("150000"))

        db.update_plate(db_conn, plate_id, plate_type="ORNAMENTAL", plate_number=None,
                         quantity=1, is_voided=False)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT unit_price FROM plates WHERE id = %s", (plate_id,))
            assert cur.fetchone() == (None,)


class TestUpdatePlateFull:
    def test_edits_every_field_and_clears_review_flag(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1210", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        plate_id = _insert_plate_get_id(db_conn, order_id, plate_number="OLD333",
                                         quantity=1, is_possible_duplicate=True)

        db.update_plate_full(
            db_conn, plate_id, plate_type="MOTO PARTICULAR", plate_number="NEW444",
            plate_to=None, city="CALI", quantity=5, is_voided=True,
            is_plate_annulled=True, is_batch_replacement=False,
        )

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT plate_type, plate_number, city, quantity, is_voided, "
                "is_plate_annulled, is_possible_duplicate FROM plates WHERE id = %s",
                (plate_id,),
            )
            assert cur.fetchone() == (
                "MOTO PARTICULAR", "NEW444", "CALI", 5, True, True, False,
            )

    def test_fixes_stale_is_batch_replacement_when_a_real_plate_number_is_given(self, db_conn):
        """Regression test: editing a reposición row in place to give it a real
        plate_number must actually clear is_batch_replacement in the DB --
        this was silently dropped before (the UPDATE never touched the
        column), leaving the row mislabeled 'reposición' forever even after
        the plate_number was corrected."""
        order_id = _insert_order(db_conn, order_number="1211", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        plate_id = _insert_plate_get_id(db_conn, order_id, is_batch_replacement=True)

        db.update_plate_full(
            db_conn, plate_id, plate_type="CARRO PARTICULAR", plate_number="REP555",
            plate_to=None, city=None, quantity=1, is_voided=False,
            is_plate_annulled=False, is_batch_replacement=False,
        )

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT plate_number, is_batch_replacement FROM plates WHERE id = %s",
                (plate_id,),
            )
            assert cur.fetchone() == ("REP555", False)

    def test_plate_to_and_city_can_be_cleared_to_null(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1212", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        plate_id = _insert_plate_get_id(db_conn, order_id, plate_number="RNG100",
                                         plate_to="RNG105", city="PASTO")

        db.update_plate_full(
            db_conn, plate_id, plate_type="CARRO PARTICULAR", plate_number="RNG100",
            plate_to=None, city=None, quantity=1, is_voided=False,
            is_plate_annulled=False, is_batch_replacement=False,
        )

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_to, city FROM plates WHERE id = %s", (plate_id,))
            assert cur.fetchone() == (None, None)

    def test_unchanged_plate_type_leaves_unit_price_untouched_even_if_quantity_changes(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1213", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.set_price(db_conn, "CARRO PARTICULAR", Decimal("999999"), "admin")  # must NOT be used
        plate_id = _insert_plate_get_id(db_conn, order_id, plate_type="CARRO PARTICULAR",
                                         unit_price=Decimal("150000"), quantity=1)

        db.update_plate_full(
            db_conn, plate_id, plate_type="CARRO PARTICULAR", plate_number=None,
            plate_to=None, city=None, quantity=8, is_voided=False,
            is_plate_annulled=False, is_batch_replacement=False,
        )

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT unit_price FROM plates WHERE id = %s", (plate_id,))
            assert cur.fetchone() == (Decimal("150000"),)

    def test_changed_plate_type_resnapshots_the_new_types_current_tariff(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1214", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.set_price(db_conn, "MOTO PARTICULAR", Decimal("90000"), "admin")
        plate_id = _insert_plate_get_id(db_conn, order_id, plate_type="CARRO PARTICULAR",
                                         unit_price=Decimal("150000"))

        db.update_plate_full(
            db_conn, plate_id, plate_type="MOTO PARTICULAR", plate_number=None,
            plate_to=None, city=None, quantity=1, is_voided=False,
            is_plate_annulled=False, is_batch_replacement=False,
        )

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT unit_price FROM plates WHERE id = %s", (plate_id,))
            assert cur.fetchone() == (Decimal("90000"),)

    def test_changed_plate_type_with_no_tariff_leaves_unit_price_null_not_zero(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1215", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        plate_id = _insert_plate_get_id(db_conn, order_id, plate_type="CARRO PARTICULAR",
                                         unit_price=Decimal("150000"))

        db.update_plate_full(
            db_conn, plate_id, plate_type="ORNAMENTAL", plate_number=None,
            plate_to=None, city=None, quantity=1, is_voided=False,
            is_plate_annulled=False, is_batch_replacement=False,
        )

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT unit_price FROM plates WHERE id = %s", (plate_id,))
            assert cur.fetchone() == (None,)

    def test_writes_reason_and_vehicle_brand(self, db_conn):
        """Fase 3: reason/vehicle_brand are plain per-line attributes with no
        re-snapshot rule of their own -- written verbatim."""
        order_id = _insert_order(db_conn, order_number="1216", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        plate_id = _insert_plate_get_id(db_conn, order_id, plate_type="CARRO PARTICULAR")

        db.update_plate_full(
            db_conn, plate_id, plate_type="CARRO PARTICULAR", plate_number=None,
            plate_to=None, city=None, quantity=1, is_voided=False,
            is_plate_annulled=False, is_batch_replacement=False,
            reason="DETERIORO", vehicle_brand="MAZDA",
        )

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT reason, vehicle_brand FROM plates WHERE id = %s", (plate_id,))
            assert cur.fetchone() == ("DETERIORO", "MAZDA")

    def test_reason_and_vehicle_brand_default_to_null(self, db_conn):
        """Omitting them (existing callers, e.g. /review's plainer edit path
        via update_plate -- a different function -- or a caller that simply
        doesn't have this data) must not raise or silently invent a value."""
        order_id = _insert_order(db_conn, order_number="1217", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        plate_id = _insert_plate_get_id(db_conn, order_id, plate_type="CARRO PARTICULAR")

        db.update_plate_full(
            db_conn, plate_id, plate_type="CARRO PARTICULAR", plate_number=None,
            plate_to=None, city=None, quantity=1, is_voided=False,
            is_plate_annulled=False, is_batch_replacement=False,
        )

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT reason, vehicle_brand FROM plates WHERE id = %s", (plate_id,))
            assert cur.fetchone() == (None, None)


class TestResolvePlate:
    def test_clears_flag_without_touching_other_fields(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1300", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        plate_id = _insert_plate_get_id(db_conn, order_id, plate_number="RES111",
                                         quantity=3, is_possible_duplicate=True)

        db.resolve_plate(db_conn, plate_id)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT plate_number, quantity, is_possible_duplicate FROM plates WHERE id = %s",
                (plate_id,),
            )
            assert cur.fetchone() == ("RES111", 3, False)

    def test_nonexistent_plate_id_is_a_silent_noop(self, db_conn):
        db.resolve_plate(db_conn, 999999)  # must not raise


class TestDeletePlate:
    """delete_plate also auto-clears a surviving sibling's review flag when
    exactly one other flagged row shares its plate_number -- see
    app/core/db/plates.py's docstring. This was a real bug found and fixed during
    manual duplicate-review cleanup; these tests pin down the exact
    threshold (1 remaining -> cleared, 2+ remaining -> left alone)."""

    def test_deletes_the_row(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1400", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        plate_id = _insert_plate_get_id(db_conn, order_id, plate_number="DEL111")

        db.delete_plate(db_conn, plate_id)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM plates WHERE id = %s", (plate_id,))
            assert cur.fetchone()[0] == 0

    def test_clears_sibling_flag_when_exactly_one_remains(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1401", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        deleted_id = _insert_plate_get_id(db_conn, order_id, plate_number="SIB111",
                                           is_possible_duplicate=True)
        sibling_id = _insert_plate_get_id(db_conn, order_id, plate_number="SIB111",
                                           is_possible_duplicate=True)

        db.delete_plate(db_conn, deleted_id)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT is_possible_duplicate FROM plates WHERE id = %s", (sibling_id,))
            assert cur.fetchone() == (False,)

    def test_leaves_flag_when_two_or_more_siblings_remain(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1402", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        deleted_id = _insert_plate_get_id(db_conn, order_id, plate_number="SIB222",
                                           is_possible_duplicate=True)
        s1 = _insert_plate_get_id(db_conn, order_id, plate_number="SIB222", is_possible_duplicate=True)
        s2 = _insert_plate_get_id(db_conn, order_id, plate_number="SIB222", is_possible_duplicate=True)

        db.delete_plate(db_conn, deleted_id)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT is_possible_duplicate FROM plates WHERE id = ANY(%s) ORDER BY id",
                ([s1, s2],),
            )
            assert [r[0] for r in cur.fetchall()] == [True, True]

    def test_does_not_touch_an_already_unflagged_row_with_same_plate_number(self, db_conn):
        order_id = _insert_order(db_conn, order_number="1403", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        deleted_id = _insert_plate_get_id(db_conn, order_id, plate_number="SIB333",
                                           is_possible_duplicate=True)
        clean_id = _insert_plate_get_id(db_conn, order_id, plate_number="SIB333",
                                         is_possible_duplicate=False)

        db.delete_plate(db_conn, deleted_id)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT is_possible_duplicate FROM plates WHERE id = %s", (clean_id,))
            assert cur.fetchone() == (False,)

    def test_deleted_row_without_a_plate_number_does_not_touch_others(self, db_conn):
        """plate_number is None for e.g. reposición rows -- the `if plate_number:`
        guard must skip the sibling lookup entirely rather than matching other
        NULL-plate_number rows against each other."""
        order_id = _insert_order(db_conn, order_number="1404", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        deleted_id = _insert_plate_get_id(db_conn, order_id, is_batch_replacement=True)
        other_id = _insert_plate_get_id(db_conn, order_id, is_batch_replacement=True,
                                         is_possible_duplicate=True)

        db.delete_plate(db_conn, deleted_id)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT is_possible_duplicate FROM plates WHERE id = %s", (other_id,))
            assert cur.fetchone() == (True,)  # untouched -- no plate_number to match on

    def test_nonexistent_plate_id_is_a_silent_noop(self, db_conn):
        db.delete_plate(db_conn, 999999)  # must not raise


# ----------------------------------------------------------------------------
# Fase 2: customers / users / pricing
# ----------------------------------------------------------------------------


class TestResolveOrCreateCustomer:
    def test_creates_a_new_customer_and_returns_its_id(self, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "PTO.V")
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT name FROM customers WHERE id = %s", (customer_id,))
            assert cur.fetchone() == ("PTO.V",)

    def test_new_customer_has_all_attributes_null(self, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "NUEVO CLIENTE")
        customer = db.get_customer(db_conn, customer_id)
        assert customer is not None
        assert customer.type is None
        assert customer.document_type is None
        assert customer.document_number is None
        assert customer.document_relation is None
        assert customer.phone is None
        assert customer.email is None
        assert customer.transit_license is None

    def test_second_call_with_same_name_returns_same_id(self, db_conn):
        first = db.resolve_or_create_customer(db_conn, "MUVA")
        second = db.resolve_or_create_customer(db_conn, "MUVA")
        assert first == second
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM customers")
            assert cur.fetchone()[0] == 1

    def test_spelling_variant_resolves_to_the_same_customer(self, db_conn):
        """PTOV / PTO DE VENTA / PTO.V are curated as the SAME entity in
        normalize._REQUESTED_BY_MAP_RAW -- resolve_or_create_customer must
        canonicalize BEFORE the lookup, not just before the insert, or a
        second spelling would create a near-duplicate customer row."""
        first = db.resolve_or_create_customer(db_conn, "PTOV")
        second = db.resolve_or_create_customer(db_conn, "PTO DE VENTA")
        assert first == second
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT name FROM customers")
            assert cur.fetchall() == [("PTO.V",)]

    def test_distinct_entities_are_not_merged(self, db_conn):
        """MUVA GRANADA and MUVA SERGIO are deliberately kept separate by
        normalize_requested_by (see its module docstring) -- must resolve to
        two different customers."""
        granada = db.resolve_or_create_customer(db_conn, "MUVA GRANADA")
        sergio = db.resolve_or_create_customer(db_conn, "MUVA SERGIO")
        assert granada != sergio

    def test_new_customer_takes_the_given_attributes(self, db_conn):
        """Fase 3: the /ingest 'cliente' fieldset writes attributes for a
        genuinely brand-new customer through these keyword args."""
        customer_id = db.resolve_or_create_customer(
            db_conn, "CLIENTE NUEVO", type="PERSONA NATURAL", document_type="CÉDULA",
            document_number="123456", document_relation="DUEÑO", phone="3001112233",
            email="a@b.com", transit_license="LIC1",
        )
        customer = db.get_customer(db_conn, customer_id)
        assert customer is not None
        assert customer.type == "PERSONA NATURAL"
        assert customer.document_type == "CÉDULA"
        assert customer.document_number == "123456"
        assert customer.document_relation == "DUEÑO"
        assert customer.phone == "3001112233"
        assert customer.email == "a@b.com"
        assert customer.transit_license == "LIC1"

    def test_existing_customer_attributes_are_never_overwritten(self, db_conn):
        """Per the plan's B5: resolving an order to an EXISTING customer may
        never rewrite that customer's own stored attributes, even if the
        caller passes different ones -- editing a customer's own data is out
        of scope (deferred to a future dedicated customers screen)."""
        first_id = db.resolve_or_create_customer(
            db_conn, "PTO.V", type="EMPRESA", phone="3009998877",
        )
        second_id = db.resolve_or_create_customer(
            db_conn, "PTO.V", type="PERSONA NATURAL", phone="3000000000",
        )
        assert first_id == second_id
        customer = db.get_customer(db_conn, first_id)
        assert customer is not None
        assert customer.type == "EMPRESA"
        assert customer.phone == "3009998877"


class TestListCustomers:
    def test_returns_every_customer_alphabetically(self, db_conn):
        db.resolve_or_create_customer(db_conn, "ZETA")
        db.resolve_or_create_customer(db_conn, "ALFA")
        names = [c.name for c in db.list_customers(db_conn)]
        assert names == ["ALFA", "ZETA"]

    def test_empty_when_no_customers(self, db_conn):
        assert db.list_customers(db_conn) == []


class TestGetCustomerByName:
    def test_finds_an_existing_customer_by_canonical_name(self, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "PTO.V")
        found = db.get_customer_by_name(db_conn, "PTO.V")
        assert found is not None
        assert found.id == customer_id

    def test_matches_via_canonicalization_not_just_exact_string(self, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "PTOV")  # stored as "PTO.V"
        found = db.get_customer_by_name(db_conn, "pto de venta")  # same canonical form
        assert found is not None
        assert found.id == customer_id

    def test_unknown_name_returns_none(self, db_conn):
        assert db.get_customer_by_name(db_conn, "NO EXISTE") is None


class TestGetCustomer:
    def test_returns_none_for_nonexistent_id(self, db_conn):
        assert db.get_customer(db_conn, 999999) is None


class TestCreateUser:
    def test_creates_and_returns_a_lookup_up_record(self, db_conn):
        user_id = db.create_user(db_conn, username="alice", password_hash="hash1", role="admin")
        user = db.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert (user.username, user.password_hash, user.role, user.active) == (
            "alice", "hash1", "admin", True,
        )

    def test_active_defaults_to_true(self, db_conn):
        user_id = db.create_user(db_conn, username="bob", password_hash="hash2", role="consulta")
        user = db.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert user.active is True

    def test_active_can_be_set_false(self, db_conn):
        user_id = db.create_user(
            db_conn, username="carol", password_hash="hash3", role="consulta", active=False
        )
        user = db.get_user_by_id(db_conn, user_id)
        assert user is not None
        assert user.active is False

    def test_duplicate_username_raises(self, db_conn):
        db.create_user(db_conn, username="dana", password_hash="hash4", role="admin")
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.create_user(db_conn, username="dana", password_hash="hash5", role="admin")


class TestGetUserByUsername:
    def test_finds_an_existing_user(self, db_conn):
        db.create_user(db_conn, username="erin", password_hash="hash6", role="punto_venta")
        user = db.get_user_by_username(db_conn, "erin")
        assert user is not None
        assert user.role == "punto_venta"

    def test_unknown_username_returns_none(self, db_conn):
        assert db.get_user_by_username(db_conn, "ghost") is None

    def test_returns_inactive_user_too(self, db_conn):
        """Deactivation is the caller's business rule (app.web.auth), not
        this lookup's -- it must hand back an inactive row rather than
        hiding it."""
        db.create_user(db_conn, username="frank", password_hash="hash7", role="admin", active=False)
        user = db.get_user_by_username(db_conn, "frank")
        assert user is not None
        assert user.active is False


class TestListUsers:
    """`users` is never actually empty under `db_conn`/`clean_db` (Fase 2's
    conftest.py pre-seeds its own 4 test identities into it on every test --
    see tests/conftest.py's clean_db docstring) -- these tests check the two
    NEW ones added here appear, not the table's total contents."""

    def test_returns_every_user_ordered_by_id(self, db_conn):
        first = db.create_user(db_conn, username="a1", password_hash="h", role="admin")
        second = db.create_user(db_conn, username="a2", password_hash="h", role="consulta")
        users = db.list_users(db_conn)
        ids = [u.id for u in users]
        assert ids == sorted(ids)  # ordered by id
        assert ids[-2:] == [first, second]  # the two just-created users are last

    def test_seeded_test_users_are_present(self, db_conn):
        usernames = {u.username for u in db.list_users(db_conn)}
        assert {"testuser", "consultauser", "produccionuser", "puntoventauser"} <= usernames


class TestCurrentPrices:
    def test_empty_when_no_prices_set(self, db_conn):
        assert db.current_prices(db_conn) == {}

    def test_reflects_set_prices(self, db_conn):
        db.set_price(db_conn, "CARRO PARTICULAR", Decimal("150000"), "admin")
        db.set_price(db_conn, "MOTO PARTICULAR", Decimal("90000"), "admin")
        assert db.current_prices(db_conn) == {
            "CARRO PARTICULAR": Decimal("150000"),
            "MOTO PARTICULAR": Decimal("90000"),
        }

    def test_type_with_no_row_is_absent_not_zero(self, db_conn):
        db.set_price(db_conn, "CARRO PARTICULAR", Decimal("150000"), "admin")
        prices = db.current_prices(db_conn)
        assert "ORNAMENTAL" not in prices


class TestPriceList:
    def test_one_row_per_plate_type_in_canonical_order(self, db_conn):
        rows = db.price_list(db_conn)
        assert [r.plate_type for r in rows] == list(PLATE_TYPES)

    def test_unpriced_type_has_all_none_fields(self, db_conn):
        rows = db.price_list(db_conn)
        ornamental = next(r for r in rows if r.plate_type == "ORNAMENTAL")
        assert ornamental.unit_price is None
        assert ornamental.updated_at is None
        assert ornamental.updated_by is None

    def test_priced_type_reflects_the_set_value(self, db_conn):
        db.set_price(db_conn, "REMOLQUE", Decimal("200000"), "admin")
        rows = db.price_list(db_conn)
        remolque = next(r for r in rows if r.plate_type == "REMOLQUE")
        assert remolque.unit_price == Decimal("200000")
        assert remolque.updated_by == "admin"
        assert remolque.updated_at is not None


class TestSetPrice:
    def test_creates_a_new_price_row(self, db_conn):
        db.set_price(db_conn, "ORNAMENTAL", Decimal("50000"), "admin")
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT unit_price, updated_by FROM plate_type_prices WHERE plate_type = %s",
                        ("ORNAMENTAL",))
            assert cur.fetchone() == (Decimal("50000"), "admin")

    def test_updates_an_existing_price_row_in_place(self, db_conn):
        db.set_price(db_conn, "ORNAMENTAL", Decimal("50000"), "admin")
        db.set_price(db_conn, "ORNAMENTAL", Decimal("60000"), "ptov")
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM plate_type_prices WHERE plate_type = %s", ("ORNAMENTAL",))
            assert cur.fetchone()[0] == 1  # updated in place, not a second row
            cur.execute("SELECT unit_price, updated_by FROM plate_type_prices WHERE plate_type = %s",
                        ("ORNAMENTAL",))
            assert cur.fetchone() == (Decimal("60000"), "ptov")

    def test_update_bumps_updated_at(self, db_conn):
        db.set_price(db_conn, "ORNAMENTAL", Decimal("50000"), "admin")
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT updated_at FROM plate_type_prices WHERE plate_type = %s", ("ORNAMENTAL",))
            first_updated_at = cur.fetchone()[0]

        db.set_price(db_conn, "ORNAMENTAL", Decimal("60000"), "admin")
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT updated_at FROM plate_type_prices WHERE plate_type = %s", ("ORNAMENTAL",))
            second_updated_at = cur.fetchone()[0]
        assert second_updated_at >= first_updated_at

    def test_invalid_plate_type_raises_value_error(self, db_conn):
        with pytest.raises(ValueError, match="no es un tipo de placa"):
            db.set_price(db_conn, "AVION", Decimal("1"), "admin")
