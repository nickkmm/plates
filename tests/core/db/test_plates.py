"""Integration tests for app/core/db/plates.py -- insert_plate,
distinct_cities, search_plates_by_number, review_rows, update_plate,
update_plate_full, resolve_plate, delete_plate.

Needs a real Postgres connection (see tests/conftest.py's
`db_conn`/`clean_db`). Setup here calls db.py's own
insert_order/insert_plate directly -- deliberately bypassing
app.core.ingestion and app.core.normalize -- so a failure in one of
these tests points at db.py itself, not at normalization or the
ingestion pipeline (covered elsewhere)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from psycopg.rows import tuple_row

from app.core import db
from tests.core.db._helpers import _insert_order, _insert_plate_get_id, make_row


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

