"""Integration tests for app/core/db/pricing.py -- current_prices,
price_list, set_price. Needs a real Postgres connection (see
tests/conftest.py's `db_conn`/`clean_db`)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from psycopg.rows import tuple_row

from app.core import db
from app.core.normalize import PLATE_TYPES


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

