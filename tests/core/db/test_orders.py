"""Integration tests for app/core/db/orders.py -- insert_order,
update_order, distinct_requested_by, recent_orders,
find_orders_by_number, search_orders_by_number, order_detail,
delete_order.

Needs a real Postgres connection (see tests/conftest.py's
`db_conn`/`clean_db`). Setup here calls db.py's own
insert_order/insert_plate/insert_blank_preparation directly --
deliberately bypassing app.core.ingestion and app.core.normalize -- so
a failure in one of these tests points at db.py itself, not at
normalization or the ingestion pipeline (covered elsewhere)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from psycopg.rows import tuple_row

from app.core import db
from tests.core.db._helpers import _insert_order, make_row


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

