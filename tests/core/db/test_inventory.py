"""Integration tests for app/core/db/inventory.py -- inventory_view and
ornamental_count.

Needs a real Postgres connection (see tests/conftest.py's
`db_conn`/`clean_db`). Setup here calls db.py's own
insert_order/insert_plate directly -- deliberately bypassing
app.core.ingestion and app.core.normalize."""

from __future__ import annotations

from datetime import date

from app.core import db
from app.core.normalize import INVENTORY_CATEGORIES
from tests.core.db._helpers import _insert_order, make_row


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

