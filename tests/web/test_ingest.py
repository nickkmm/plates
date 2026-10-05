"""Integration tests for app/web/blueprints/ingest.py -- POST /ingest (order
header + plate-line capture): duplicate-plate and duplicate-order-number
warnings, the required payment_method field, customer capture/
autocomplete, per-line reason/vehicle_brand/unit_price persistence, and
the sticky-header round trip when a submission bounces back with an
error.

Needs both an authenticated client and a real Postgres connection
(`db_conn`/`clean_db`) -- see tests/web/test_review.py's module
docstring for the full separate-connection-per-checkout caveat."""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

import pytest
from psycopg.rows import tuple_row

from app.core import db
from tests.web._helpers import _insert_order, make_row


class TestIngestPostRoute:
    def test_valid_single_plate_submission_inserts_and_shows_summary(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3000", "order_date": "2026-01-01",
            "requested_by": "PEDRO",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["AAA111"], "plate_to": [""], "quantity": ["1"],
            "city": ["BOGOTA"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT order_number, requested_by FROM production_orders")
            assert cur.fetchall() == [("3000", "PEDRO")]
            cur.execute("SELECT plate_number, plate_type, quantity, city FROM plates")
            assert cur.fetchall() == [("AAA111", "CARRO PARTICULAR", 1, "BOGOTA")]

    def test_successful_submission_is_logged(self, authenticated_client, clean_db, caplog):
        with caplog.at_level(logging.INFO):
            authenticated_client.post("/ingest", data={
                "payment_method": "OTROS",
                "order_number": "3001", "order_date": "2026-01-01",
                "requested_by": "PEDRO",
                "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
                "plate_number": ["AAA112"], "plate_to": [""], "quantity": ["1"],
                "city": ["BOGOTA"],
            })
        assert any(
            r.message == "order created: user=testuser order_number=3001 plates=1"
            for r in caplog.records
        )

    def test_two_lines_in_one_order_keep_their_own_cities(self, authenticated_client, clean_db):
        """The regression test for the original bug: city is per plate, so two
        lines of one order must NOT collapse onto the first line's city."""
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3010", "order_date": "2026-01-01",
            "requested_by": "PEDRO",
            "line_type": ["placa", "placa"],
            "plate_type": ["CARRO PARTICULAR", "CARRO PARTICULAR"],
            "plate_number": ["AAA111", "BBB222"], "plate_to": ["", ""],
            "quantity": ["1", "1"],
            "city": ["CALI", "PASTO"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders")
            assert cur.fetchone() == (1,)  # still ONE order
            cur.execute("SELECT plate_number, city FROM plates ORDER BY id")
            assert cur.fetchall() == [("AAA111", "CALI"), ("BBB222", "PASTO")]

    def test_blank_city_on_a_moto_line_is_saved_as_colombia(self, authenticated_client, clean_db):
        """MOTO PARTICULAR is nationwide, so a blank Ciudad means COLOMBIA."""
        authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3011", "order_date": "2026-01-01",
            "requested_by": "PEDRO",
            "line_type": ["placa", "placa"],
            "plate_type": ["MOTO PARTICULAR", "CARRO PARTICULAR"],
            "plate_number": ["ABC12D", "BBB222"], "plate_to": ["", ""],
            "quantity": ["1", "1"],
            "city": ["", ""],
        })
        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number, city FROM plates ORDER BY id")
            # the carro line stays blank -- only nationwide types get the default
            assert cur.fetchall() == [("ABC12D", "COLOMBIA"), ("BBB222", None)]

    def test_lines_survive_a_post_that_omits_the_city_field_entirely(
        self, authenticated_client, clean_db
    ):
        """City is optional, so a payload without it must still save the lines
        (the per-line zips would otherwise silently truncate to nothing)."""
        authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3012", "order_date": "2026-01-01",
            "requested_by": "PEDRO",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["AAA111"], "plate_to": [""], "quantity": ["1"],
        })
        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number, city FROM plates")
            assert cur.fetchall() == [("AAA111", None)]

    def test_rango_line_type_inserts_desde_hasta(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3001", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["rango"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["AAA100"], "plate_to": ["AAA105"], "quantity": ["6"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number, plate_to FROM plates")
            assert cur.fetchall() == [("AAA100", "AAA105")]

    def test_no_plate_lines_shows_error_and_inserts_nothing(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3002", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": [], "plate_type": [], "plate_number": [], "plate_to": [], "quantity": [],
        })
        assert resp.status_code == 200
        assert "Agregá al menos una placa".encode() in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders")
            assert cur.fetchone()[0] == 0

    def test_requested_by_with_digits_is_rejected(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3003", "order_date": "2026-01-01",
            "requested_by": "PEDRO123", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"Error" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders")
            assert cur.fetchone()[0] == 0

    def test_future_order_date_is_rejected(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3004", "order_date": "2999-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"futura" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders")
            assert cur.fetchone()[0] == 0

    def test_rango_missing_hasta_is_rejected(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3005", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["rango"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["AAA100"], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"desde" in resp.data and b"hasta" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders")
            assert cur.fetchone()[0] == 0

    def test_reposicion_line_type_flags_batch_replacement_with_no_plate_number(
        self, authenticated_client, clean_db
    ):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3006", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["reposicion"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["50"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT plate_number, plate_to, is_batch_replacement, is_voided FROM plates"
            )
            assert cur.fetchall() == [(None, None, True, False)]

    def test_anulada_orden_line_type_flags_voided_with_no_plate_number(
        self, authenticated_client, clean_db
    ):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3007", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["anulada_orden"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT plate_number, plate_to, is_batch_replacement, is_voided FROM plates"
            )
            assert cur.fetchall() == [(None, None, False, True)]

    def test_muestra_line_type_defaults_plate_number_when_blank(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3008", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["muestra"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number, plate_to FROM plates")
            assert cur.fetchall() == [("MUESTRA", None)]

    def test_placa_anulada_line_type_flags_plate_annulled_and_validates_format(
        self, authenticated_client, clean_db
    ):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3009", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa_anulada"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["AAA200"], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number, plate_to, is_plate_annulled FROM plates")
            assert cur.fetchall() == [("AAA200", None, True)]

    def test_placa_anulada_with_blank_plate_number_skips_validation(self, authenticated_client, clean_db):
        """placa_anulada records a lost blank as waste -- the specific plate
        identifier is sometimes simply unknown, so (unlike 'rango') a blank
        plate_number here is valid and skips validate_plate() entirely."""
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3012", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa_anulada"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number, is_plate_annulled FROM plates")
            assert cur.fetchall() == [(None, True)]

    def test_placa_line_type_with_blank_plate_number_skips_validation(self, authenticated_client, clean_db):
        """The 'placa' (single) branch only calls validate_plate() when a
        plate_number was actually given -- an intentionally blank plate is
        valid (e.g. a line whose plate is genuinely unknown yet)."""
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3010", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number FROM plates")
            assert cur.fetchall() == [(None,)]

    def test_line_with_blank_plate_type_is_skipped_entirely(self, authenticated_client, clean_db):
        """A row with no plate_type selected (e.g. a leftover empty line in
        the form) is dropped before validation/insert -- only the other,
        properly-filled line in the same submission gets inserted."""
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "3011", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa", "placa"], "plate_type": ["", "CARRO PARTICULAR"],
            "plate_number": ["", "AAA300"], "plate_to": ["", ""], "quantity": ["1", "1"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number FROM plates")
            assert cur.fetchall() == [("AAA300",)]  # only the filled-in line landed



class TestIngestDuplicateWarning:
    def test_plate_already_in_db_blocks_save_and_shows_warning_with_link(
        self, authenticated_client, clean_db
    ):
        authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "4000", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["DUP001"], "plate_to": [""], "quantity": ["1"],
        })
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "4001", "order_date": "2026-01-02",
            "requested_by": "MARIA", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["DUP001"], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"ya existen" in resp.data
        assert b"4000" in resp.data  # points to the order where it already exists
        assert b'value="MARIA"' in resp.data  # typed header data preserved

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '4001'")
            assert cur.fetchone()[0] == 0  # second order was NOT saved

    def test_confirm_duplicate_forces_the_save_through(self, authenticated_client, clean_db):
        authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "4002", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["DUP002"], "plate_to": [""], "quantity": ["1"],
        })
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "4003", "order_date": "2026-01-02",
            "requested_by": "MARIA", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["DUP002"], "plate_to": [""], "quantity": ["1"],
            "confirm_duplicate": "1",
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT is_possible_duplicate FROM plates p "
                "JOIN production_orders o ON o.id = p.order_id WHERE o.order_number = '4003'"
            )
            assert cur.fetchall() == [(True,)]  # saved, and still flagged for the review queue

    def test_plate_repeated_within_the_same_submission_blocks_save(
        self, authenticated_client, clean_db
    ):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "4004", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa", "placa"], "plate_type": ["CARRO PARTICULAR", "CARRO PARTICULAR"],
            "plate_number": ["DUP003", "DUP003"], "plate_to": ["", ""], "quantity": ["1", "1"],
        })
        assert resp.status_code == 200
        assert b"ya existen" in resp.data
        assert "mismo envío".encode() in resp.data  # no prior order to link to, just a same-batch repeat

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '4004'")
            assert cur.fetchone()[0] == 0



class TestIngestOrderDuplicateWarning:
    """The order-number collision check (db.find_orders_by_number) runs
    BEFORE the plate-number dup check in ingest_post -- a reposición
    placeholder order has plate_number=NULL, so it would otherwise sail
    straight through the plate check and silently create a second
    production_orders row under the same order_number (see
    app.core.ingestion.replace_order's docstring for the real WCY449/pedido
    1030 case)."""

    def test_colliding_order_number_blocks_save_and_shows_warning(
        self, authenticated_client, clean_db
    ):
        authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "5000", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["reposicion"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["50"],
        })
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "5000", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["WCY449"], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"ya existe" in resp.data
        assert b'value="PEDRO"' in resp.data  # typed header data preserved

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '5000'")
            assert cur.fetchone()[0] == 1  # still just the original placeholder order
            cur.execute(
                "SELECT count(*) FROM plates p JOIN production_orders o ON o.id = p.order_id "
                "WHERE o.order_number = '5000'"
            )
            assert cur.fetchone()[0] == 1  # the new plate line was NOT saved

    def test_confirm_order_duplicate_saves_as_a_genuinely_new_second_order(
        self, authenticated_client, clean_db
    ):
        """Today's (bad, but explicitly chosen) behavior, fully preserved:
        confirming creates a SECOND production_orders row sharing the number."""
        authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "5001", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["reposicion"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["50"],
        })
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "5001", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["WCY450"], "plate_to": [""], "quantity": ["1"],
            "confirm_order_duplicate": "1",
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '5001'")
            assert cur.fetchone()[0] == 2  # two orders now share this number
            cur.execute(
                "SELECT count(*) FROM plates p JOIN production_orders o ON o.id = p.order_id "
                "WHERE o.order_number = '5001'"
            )
            assert cur.fetchone()[0] == 2  # both the old reposición row and the new plate exist

    def test_replace_order_fuses_a_single_existing_order_into_one(
        self, authenticated_client, clean_db
    ):
        authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "5002", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["reposicion"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": [""], "plate_to": [""], "quantity": ["50"],
        })
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "5002", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["WCY451"], "plate_to": [""], "quantity": ["1"],
            "replace_order": "1",
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '5002'")
            assert cur.fetchone()[0] == 1  # exactly one order, not two
            cur.execute(
                "SELECT p.plate_number FROM plates p JOIN production_orders o ON o.id = p.order_id "
                "WHERE o.order_number = '5002'"
            )
            assert cur.fetchall() == [("WCY451",)]  # only the newly submitted plate remains

    def test_replace_order_fuses_two_pre_existing_colliding_orders_into_one(
        self, authenticated_client, db_conn
    ):
        """Proves the fuse behavior when the collision is already 2 orders
        deep (order_number has no unique constraint, so this really happens
        -- see WCY449/pedido 1030)."""
        first_id = _insert_order(db_conn, order_number="5003", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=first_id,
                         row=make_row(order_number="5003", plate_type="CARRO PARTICULAR",
                                      is_batch_replacement=True, quantity=30),
                         is_possible_duplicate=False)
        second_id = _insert_order(db_conn, order_number="5003", order_date=date(2026, 1, 2),
                                     requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=second_id,
                         row=make_row(order_number="5003", plate_type="CARRO PARTICULAR",
                                      plate_number="OLD999"),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "5003", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa", "placa"],
            "plate_type": ["CARRO PARTICULAR", "CARRO PARTICULAR"],
            "plate_number": ["NEW100", "NEW101"], "plate_to": ["", ""], "quantity": ["1", "1"],
            "replace_order": "1",
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '5003'")
            assert cur.fetchone()[0] == 1  # both pre-existing orders fused into one
            cur.execute(
                "SELECT p.plate_number FROM plates p JOIN production_orders o ON o.id = p.order_id "
                "WHERE o.order_number = '5003' ORDER BY p.plate_number"
            )
            assert cur.fetchall() == [("NEW100",), ("NEW101",)]
            cur.execute("SELECT count(*) FROM plates WHERE plate_number = 'OLD999'")
            assert cur.fetchone()[0] == 0  # the old plate from the second fused order is gone too

    def test_no_collision_path_is_completely_unaffected(self, authenticated_client, clean_db):
        """Regression coverage: an ordinary, no-collision /ingest submission
        must save immediately, with no order-duplicate warning at all."""
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "5004", "order_date": "2026-01-01",
            "requested_by": "PEDRO", "city": "",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["FRE599"], "plate_to": [""], "quantity": ["1"],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data
        assert b"ya existe" not in resp.data

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '5004'")
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT plate_number FROM plates WHERE order_id = ("
                        "SELECT id FROM production_orders WHERE order_number = '5004')")
            assert cur.fetchall() == [("FRE599",)]



class TestIngestPaymentMethodRequired:
    def test_blank_payment_method_is_rejected_and_saves_nothing(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "order_number": "6500", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["PAY100"], "plate_to": [""], "quantity": ["1"], "city": [""],
        })
        assert resp.status_code == 200
        assert b"forma de pago" in resp.data.lower()

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '6500'")
            assert cur.fetchone()[0] == 0

    def test_otros_is_accepted_for_genuinely_unknown_cases(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/ingest", data={
            "payment_method": "OTROS",
            "order_number": "6501", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["PAY101"], "plate_to": [""], "quantity": ["1"], "city": [""],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data



class TestIngestCustomerCapture:
    def test_new_customer_attributes_are_persisted(self, authenticated_client, clean_db):
        authenticated_client.post("/ingest", data={
            "payment_method": "EFECTIVO",
            "order_number": "6600", "order_date": "2026-01-01", "requested_by": "CLIENTE UNO",
            "customer_type": "CLIENTE", "customer_document_type": "CÉDULA",
            "customer_document_number": "999", "customer_document_relation": "DUEÑO",
            "customer_phone": "3001112222", "customer_email": "uno@x.com",
            "customer_address": "CALLE 10 # 20-30", "customer_neighborhood": "CENTRO",
            "customer_municipality": "CALI",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["CUS100"], "plate_to": [""], "quantity": ["1"], "city": [""],
        })
        with db.get_conn() as conn:
            customer = db.get_customer_by_name(conn, "CLIENTE UNO")
        assert customer is not None
        assert customer.type == "CLIENTE"
        assert customer.document_type == "CÉDULA"
        assert customer.document_number == "999"
        assert customer.document_relation == "DUEÑO"
        assert customer.phone == "3001112222"
        assert customer.email == "uno@x.com"
        assert customer.address == "CALLE 10 # 20-30"
        assert customer.neighborhood == "CENTRO"
        assert customer.municipality == "CALI"

    def test_gestor_is_saved_with_person_fields_but_no_document_relation(
        self, authenticated_client, clean_db
    ):
        authenticated_client.post("/ingest", data={
            "payment_method": "EFECTIVO",
            "order_number": "6604", "order_date": "2026-01-01", "requested_by": "GESTOR UNO",
            "customer_type": "GESTOR", "customer_document_type": "CÉDULA DE EXTRANJERÍA",
            "customer_document_number": "E123", "customer_document_relation": "DUEÑO",
            "customer_address": "AV 3", "customer_neighborhood": "NORTE",
            "customer_municipality": "PALMIRA",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["CUS104"], "plate_to": [""], "quantity": ["1"], "city": [""],
        })
        with db.get_conn() as conn:
            customer = db.get_customer_by_name(conn, "GESTOR UNO")
        assert customer is not None
        assert customer.type == "GESTOR"
        assert customer.document_type == "CÉDULA DE EXTRANJERÍA"
        assert customer.document_relation is None
        assert (customer.address, customer.neighborhood, customer.municipality) == (
            "AV 3", "NORTE", "PALMIRA",
        )

    def test_document_relation_is_forced_null_unless_cliente(self, authenticated_client, clean_db):
        """Server-side enforcement: even if a raw POST carries a
        document_relation, it must never be stored unless the customer type
        is genuinely CLIENTE -- the HTML disables the field for anything
        else, but the server never trusts that alone."""
        authenticated_client.post("/ingest", data={
            "payment_method": "EFECTIVO",
            "order_number": "6601", "order_date": "2026-01-01", "requested_by": "CLIENTE DOS",
            "customer_type": "EMPRESA", "customer_document_type": "NIT",
            "customer_document_relation": "DUEÑO",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["CUS101"], "plate_to": [""], "quantity": ["1"], "city": [""],
        })
        with db.get_conn() as conn:
            customer = db.get_customer_by_name(conn, "CLIENTE DOS")
        assert customer is not None
        assert customer.document_type == "NIT"
        assert customer.document_relation is None

    @pytest.mark.parametrize("customer_type", ["EMPRESA", "TRÁNSITO"])
    def test_person_fields_are_forced_null_for_empresa_and_transito(
        self, authenticated_client, clean_db, customer_type
    ):
        authenticated_client.post("/ingest", data={
            "payment_method": "EFECTIVO",
            "order_number": "6602", "order_date": "2026-01-01", "requested_by": "CLIENTE TRES",
            "customer_type": customer_type, "customer_phone": "300",
            "customer_document_relation": "POSEEDOR", "customer_address": "CALLE VIEJA",
            "customer_neighborhood": "BARRIO VIEJO", "customer_municipality": "CALI",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["CUS102"], "plate_to": [""], "quantity": ["1"], "city": [""],
        })
        with db.get_conn() as conn:
            customer = db.get_customer_by_name(conn, "CLIENTE TRES")
        assert customer is not None
        assert customer.type == customer_type
        assert customer.phone == "300"  # still kept
        assert customer.document_relation is None
        assert customer.address is None
        assert customer.neighborhood is None
        assert customer.municipality is None

    def test_existing_customer_matched_by_requested_by_is_not_overwritten(
        self, authenticated_client, clean_db
    ):
        with db.get_conn() as conn:
            db.resolve_or_create_customer(conn, "CLIENTE CUATRO", type="EMPRESA", phone="111")
            conn.commit()
        authenticated_client.post("/ingest", data={
            "payment_method": "EFECTIVO",
            "order_number": "6603", "order_date": "2026-01-01", "requested_by": "CLIENTE CUATRO",
            "customer_type": "CLIENTE", "customer_phone": "999",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["CUS103"], "plate_to": [""], "quantity": ["1"], "city": [""],
        })
        with db.get_conn() as conn:
            customer = db.get_customer_by_name(conn, "CLIENTE CUATRO")
        assert customer is not None
        assert customer.type == "EMPRESA"
        assert customer.phone == "111"



class TestIngestCustomerFieldset:
    def test_form_has_person_fields_and_no_transit_license(self, authenticated_client, clean_db):
        html = authenticated_client.get("/ingest").data.decode()
        for name in ("customer_address", "customer_neighborhood", "customer_municipality"):
            assert f'name="{name}"' in html
        assert "customer_transit_license" not in html
        assert "Licencia de tránsito" not in html
        for ct in ("TRÁNSITO", "EMPRESA", "GESTOR", "CLIENTE"):
            assert f'<option value="{ct}"' in html
        assert 'value="PERSONA NATURAL"' not in html

    def test_embedded_customer_lookup_carries_person_fields(self, authenticated_client, clean_db):
        with db.get_conn() as conn:
            db.resolve_or_create_customer(
                conn, "LOOKUP UNO", type="CLIENTE", address="CALLE 1", neighborhood="CENTRO",
                municipality="CALI",
            )
            conn.commit()
        html = authenticated_client.get("/ingest").data.decode()
        assert '"address": "CALLE 1"' in html
        assert '"neighborhood": "CENTRO"' in html
        assert '"municipality": "CALI"' in html
        assert "transit_license" not in html


class TestIngestPlateLineFieldsPersist:
    def test_reason_vehicle_brand_unit_price_are_saved(self, authenticated_client, clean_db):
        authenticated_client.post("/ingest", data={
            "payment_method": "EFECTIVO",
            "order_number": "6700", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["LIN100"], "plate_to": [""], "quantity": ["1"], "city": [""],
            "reason": ["DETERIORO"], "vehicle_brand": ["TOYOTA"], "unit_price": ["180000"],
        })
        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT reason, vehicle_brand, unit_price FROM plates WHERE plate_number = 'LIN100'"
            )
            assert cur.fetchone() == ("DETERIORO", "TOYOTA", Decimal("180000"))



class TestIngestStickyRoundTrip:
    """Item 5 of the Fase 3 plan: every new POS field (order-level, cliente,
    and per-line) must survive a duplicate-order bounce via the shared
    _sticky_header_from_form/_sticky_lines_from_form helpers."""

    def test_every_new_field_survives_a_duplicate_order_bounce(self, authenticated_client, clean_db):
        payload = {
            "order_number": "6800", "order_date": "2026-01-01",
            "requested_by": "CLIENTE ABC",
            "receipt_type": "FACTURA", "receipt_number": "FAC-1",
            "customer_type": "CLIENTE", "customer_document_type": "CÉDULA",
            "customer_document_number": "111", "customer_document_relation": "DUEÑO",
            "customer_phone": "3000000000", "customer_email": "abc@x.com",
            "customer_address": "CALLE 8 # 9-10", "customer_neighborhood": "LA FLORA",
            "customer_municipality": "YUMBO",
            "payment_method": "TRANSFERENCIA",
            "tracking_number": "GUIA-1", "client_order_number": "PED-1",
            "delivery_date": "2026-01-10", "payment_date": "2026-01-02",
            "dispatched_by": "ANA", "received_by": "LUIS", "notes": "obs",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["AAA900"], "plate_to": [""], "quantity": ["3"],
            "city": ["CALI"], "reason": ["DETERIORO"], "vehicle_brand": ["MAZDA"],
            "unit_price": ["150000"],
        }
        first = authenticated_client.post("/ingest", data=payload)
        assert b"Registrado" in first.data

        resp = authenticated_client.post("/ingest", data=payload)  # same order_number -> bounce
        assert resp.status_code == 200
        assert b"ya existe" in resp.data
        html = resp.data.decode()

        for expected in [
            'value="6800"', 'value="CLIENTE ABC"', 'value="FAC-1"',
            'value="111"', 'value="3000000000"', 'value="abc@x.com"',
            'value="CALLE 8 # 9-10"', 'value="LA FLORA"', 'value="YUMBO"', 'value="GUIA-1"', 'value="PED-1"',
            'value="2026-01-10"', 'value="2026-01-02"', 'value="ANA"', 'value="LUIS"',
            'value="obs"', 'value="AAA900"', 'value="CALI"', 'value="MAZDA"', 'value="150000"',
        ]:
            assert expected in html, f"missing round-tripped field: {expected}"

        assert '<option value="FACTURA" selected>' in html
        assert '<option value="TRANSFERENCIA" selected>' in html
        assert '<option value="CLIENTE" selected>' in html
        assert '<option value="CÉDULA" selected>' in html
        assert '<option value="DUEÑO" selected>' in html
        assert '<option value="DETERIORO" selected>' in html

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '6800'")
            assert cur.fetchone()[0] == 1  # second post was NOT saved

