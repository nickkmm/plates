"""Integration tests for app/web/blueprints/records.py -- GET /records (the
recent-orders list plus its plate-number/order-number search), GET
/records/<id> (order detail), POST /records/<id>/save (header + line-
item edits, including the Fase 3 POS fields -- see
TestRecordSavePosFields), and POST /records/delete (drops an order and
all its plates).

Needs both an authenticated client and a real Postgres connection
(`db_conn`/`clean_db`) -- see tests/web/test_review.py's module
docstring for the full separate-connection-per-checkout caveat."""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from urllib.parse import urlparse

from psycopg.rows import tuple_row

from app.core import db
from tests.web._helpers import _insert_order, make_row


class TestRecordsRoute:
    def test_renders_recent_orders(self, authenticated_client, db_conn):
        _insert_order(db_conn, order_number="2600", order_date=date(2026, 1, 1),
                         requested_by="PEDRO")
        db_conn.commit()

        resp = authenticated_client.get("/records")
        assert resp.status_code == 200
        assert b"2600" in resp.data
        assert b"PEDRO" in resp.data

    def test_empty_db_shows_no_orders_message(self, authenticated_client, clean_db):
        resp = authenticated_client.get("/records")
        assert resp.status_code == 200
        assert "No hay órdenes registradas todavía".encode() in resp.data

    def test_search_query_finds_a_plate_outside_the_recent_orders_page(
        self, authenticated_client, db_conn
    ):
        """The plate lives in an order old enough that recent_orders() alone
        wouldn't necessarily show it -- the search path is a separate query,
        not a client-side filter of the 50 recent rows."""
        order_id = _insert_order(db_conn, order_number="2601", order_date=date(2020, 1, 1),
                                    requested_by="OLD CLIENT")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SRC123"),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.get("/records?q=SRC123")
        assert resp.status_code == 200
        assert b"SRC123" in resp.data
        assert b"OLD CLIENT" in resp.data
        # the plain recent-orders table must not render alongside search results
        assert b"resultado(s) para" in resp.data

    def test_search_with_no_match_shows_a_clear_message(self, authenticated_client, clean_db):
        resp = authenticated_client.get("/records?q=ZZZ999")
        assert resp.status_code == 200
        assert "No se encontró ninguna placa".encode() in resp.data

    def test_search_by_order_number_finds_the_order(self, authenticated_client, db_conn):
        """A query that matches an order_number (not any plate_number) must
        still surface that order, via the new order_matches section."""
        order_id = _insert_order(db_conn, order_number="OLD-2603", order_date=date(2019, 1, 1),
                                    requested_by="OLD CLIENT 2")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="NOMATCH1"),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.get("/records?q=OLD-2603")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "OLD-2603" in html
        assert "OLD CLIENT 2" in html
        assert "Coincidencias por número de orden" in html
        # the query doesn't match any plate_number, so no plate-results section
        assert "resultado(s) para" not in html

    def test_order_number_search_does_not_affect_plate_search_results(
        self, authenticated_client, db_conn
    ):
        """Regression guard: adding order-number search must not change the
        existing plate-search behavior when a query only matches a plate."""
        order_id = _insert_order(db_conn, order_number="2604", order_date=date(2020, 1, 1),
                                    requested_by="OLD CLIENT 3")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="PLT999"),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.get("/records?q=PLT999")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "PLT999" in html
        assert "resultado(s) para" in html
        # PLT999 doesn't match the order_number "2604", so no order section
        assert "Coincidencias por número de orden" not in html

    def test_query_matching_both_a_plate_and_an_order_number_shows_both_sections(
        self, authenticated_client, db_conn
    ):
        order_id = _insert_order(db_conn, order_number="2605", order_date=date(2021, 1, 1),
                                    requested_by="BOTH CLIENT")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="ABC2605"),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.get("/records?q=2605")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Coincidencias por número de orden" in html
        assert "resultado(s) para" in html

    def test_blank_query_falls_back_to_recent_orders(self, authenticated_client, db_conn):
        _insert_order(db_conn, order_number="2602", order_date=date(2026, 1, 1),
                         requested_by="PEDRO")
        db_conn.commit()

        resp = authenticated_client.get("/records?q=")
        assert resp.status_code == 200
        assert b"2602" in resp.data
        assert b"resultado(s) para" not in resp.data

    def test_plate_search_result_ver_link_carries_the_query_along(
        self, authenticated_client, db_conn
    ):
        """So record_detail's 'Volver a Registros' can send the person back to
        this same search instead of always landing on the unfiltered list."""
        order_id = _insert_order(db_conn, order_number="2606", order_date=date(2021, 1, 1),
                                    requested_by="LINK CLIENT")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="LNK606"),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.get("/records?q=LNK606")
        assert f"/records/{order_id}?q=LNK606" in resp.data.decode()

    def test_order_search_result_ver_link_carries_the_query_along(
        self, authenticated_client, db_conn
    ):
        order_id = _insert_order(db_conn, order_number="OLD-2607", order_date=date(2019, 1, 1),
                                    requested_by="OLD CLIENT 4")
        db_conn.commit()

        resp = authenticated_client.get("/records?q=OLD-2607")
        assert f"/records/{order_id}?q=OLD-2607" in resp.data.decode()

    def test_recent_orders_ver_link_has_no_query(self, authenticated_client, db_conn):
        """Regression guard: the default (unfiltered) list has no search to
        return to, so its 'Ver' link must stay a plain record_detail URL."""
        order_id = _insert_order(db_conn, order_number="2608", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db_conn.commit()

        resp = authenticated_client.get("/records")
        assert f'href="/records/{order_id}"' in resp.data.decode()



class TestRecordDetailRoute:
    def test_existing_order_renders_its_plates(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="2700", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="DET111", quantity=2),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.get(f"/records/{order_id}")
        assert resp.status_code == 200
        assert b"DET111" in resp.data
        assert b"2700" in resp.data

    def test_nonexistent_order_redirects_to_records(self, authenticated_client, clean_db):
        resp = authenticated_client.get("/records/999999")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/records"

    def test_back_link_preserves_the_originating_search_query(self, authenticated_client, db_conn):
        """Reached via a Registros search's 'Ver' link (?q=... passed through)
        -- 'Volver a Registros' must return to that same search, not the bare
        unfiltered list."""
        order_id = _insert_order(db_conn, order_number="2701", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db_conn.commit()

        resp = authenticated_client.get(f"/records/{order_id}?q=PEDRO")
        assert "/records?q=PEDRO" in resp.data.decode()

    def test_back_link_has_no_query_when_reached_without_one(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="2702", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db_conn.commit()

        resp = authenticated_client.get(f"/records/{order_id}")
        assert 'href="/records"' in resp.data.decode()

    def test_plates_are_rendered_as_editable_inline_forms(self, authenticated_client, db_conn):
        """Smoke test for the inline-edit table: each plate row must expose a
        hidden plate_id and an editable plate_number input, so the page really
        offers editing rather than just read-only display."""
        order_id = _insert_order(db_conn, order_number="2701", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="EDT111", quantity=2),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.get(f"/records/{order_id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'name="plate_number"' in html
        assert 'name="plate_id"' in html
        assert f'action="/records/{order_id}/save"' in html
        assert 'name="order_number"' in html  # header is editable too
        assert "+ Agregar placa" in html
        assert "Guardar cambios" in html



class TestRecordSaveRoute:
    def test_get_and_post_require_login(self, client):
        resp = client.post("/records/1/save", data={})
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/login"

        resp = client.get("/records/1/save")
        assert resp.status_code != 200

    def test_updates_the_order_header(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="3000", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV100", quantity=1),
                         is_possible_duplicate=False)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'SAV100'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        resp = authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3000-B", "order_date": "2026-02-02", "requested_by": "MARIA",
            "plate_id": [str(plate_id)], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["SAV100"], "plate_to": [""], "city": [""],
            "quantity": ["1"], "is_plate_annulled": [""],
        })
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == f"/records/{order_id}"

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT order_number, order_date, requested_by FROM production_orders WHERE id = %s",
                (order_id,),
            )
            assert cur.fetchone() == ("3000-B", date(2026, 2, 2), "MARIA")

    def test_successful_save_is_logged(self, authenticated_client, db_conn, caplog):
        order_id = _insert_order(db_conn, order_number="3002", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV300", quantity=1),
                         is_possible_duplicate=False)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'SAV300'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        with caplog.at_level(logging.INFO):
            authenticated_client.post(f"/records/{order_id}/save", data={
                "order_number": "3002", "order_date": "2026-01-01", "requested_by": "PEDRO",
                "plate_id": [str(plate_id)], "plate_type": ["CARRO PARTICULAR"],
                "plate_number": ["SAV300"], "plate_to": [""], "city": [""],
                "quantity": ["1"], "is_plate_annulled": [""],
            })
        assert any(
            r.message == f"record saved: user=testuser order_id={order_id} order_number=3002"
            for r in caplog.records
        )

    def test_edits_an_existing_plate_in_place(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="3001", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV200", quantity=1),
                         is_possible_duplicate=True)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'SAV200'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3001", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [str(plate_id)], "plate_type": ["MOTO PARTICULAR"],
            "plate_number": ["abc12d"], "plate_to": [""], "city": ["CALI"],
            "quantity": ["7"], "is_plate_annulled": [""],
        })

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT plate_type, plate_number, city, quantity, is_possible_duplicate "
                "FROM plates WHERE id = %s", (plate_id,),
            )
            assert cur.fetchone() == ("MOTO PARTICULAR", "ABC12D", "CALI", 7, False)
            # still just the one plate under this order -- an update, not a duplicate insert
            cur.execute("SELECT count(*) FROM plates WHERE order_id = %s", (order_id,))
            assert cur.fetchone()[0] == 1

    def test_order_voided_checkbox_applies_to_every_plate_in_the_order(self, authenticated_client, db_conn):
        """order_voided is ONE header-level checkbox, not a per-plate field --
        the 'anulada' word on the schema's plates.is_voided column really
        means 'the whole order was voided', so checking it must flip every
        plate row in this order, including one that keeps a real plate_number
        (the plate was made, then the order got voided after the fact)."""
        order_id = _insert_order(db_conn, order_number="3002", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV300", quantity=1),
                         is_possible_duplicate=False)
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV301", quantity=1),
                         is_possible_duplicate=False)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'SAV300'")
            id_300 = cur.fetchone()[0]
            cur.execute("SELECT id FROM plates WHERE plate_number = 'SAV301'")
            id_301 = cur.fetchone()[0]
        db_conn.commit()

        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3002", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "order_voided": "on",
            "plate_id": [str(id_300), str(id_301)],
            "plate_type": ["CARRO PARTICULAR", "CARRO PARTICULAR"],
            "plate_number": ["SAV300", "SAV301"], "plate_to": ["", ""], "city": ["", ""],
            "quantity": ["1", "1"], "is_plate_annulled": ["", ""],
        })

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number, is_voided FROM plates WHERE order_id = %s ORDER BY id",
                        (order_id,))
            assert cur.fetchall() == [("SAV300", True), ("SAV301", True)]

    def test_order_detail_page_reflects_the_current_voided_state(self, authenticated_client, db_conn):
        """The header checkbox must come back checked on reload, computed
        from the order's actual plates -- not just remembered from the form."""
        order_id = _insert_order(db_conn, order_number="3002b", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV302",
                                      quantity=1, is_voided=True),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.get(f"/records/{order_id}")
        html = resp.data.decode()
        assert '<input type="checkbox" name="order_voided" checked>' in html

    def test_adds_a_new_plate_row_in_the_same_submit(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="3003", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV400", quantity=1),
                         is_possible_duplicate=False)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'SAV400'")
            existing_id = cur.fetchone()[0]
        db_conn.commit()

        resp = authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3003", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [str(existing_id), ""],
            "plate_type": ["CARRO PARTICULAR", "MOTO PARTICULAR"],
            "plate_number": ["SAV400", "add50d"], "plate_to": ["", ""],
            "city": ["", ""], "quantity": ["1", "3"], "is_plate_annulled": ["", ""],
        })
        assert resp.status_code == 302

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM plates WHERE order_id = %s", (order_id,))
            assert cur.fetchone()[0] == 2
            cur.execute("SELECT plate_type, quantity, order_id FROM plates WHERE plate_number = 'ADD50D'")
            assert cur.fetchone() == ("MOTO PARTICULAR", 3, order_id)

    def test_removing_a_row_from_the_submit_deletes_it(self, authenticated_client, db_conn):
        """The form has no explicit delete button/route -- removing a row
        client-side (JS) before submitting is what deletes it: its plate_id is
        simply absent from the next save."""
        order_id = _insert_order(db_conn, order_number="3004", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV500", quantity=1),
                         is_possible_duplicate=False)
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV600", quantity=1),
                         is_possible_duplicate=False)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'SAV500'")
            keep_id = cur.fetchone()[0]
        db_conn.commit()

        # Only SAV500 is submitted -- SAV600's row was removed in the browser.
        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3004", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [str(keep_id)], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["SAV500"], "plate_to": [""], "city": [""],
            "quantity": ["1"], "is_plate_annulled": [""],
        })

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number FROM plates WHERE order_id = %s", (order_id,))
            assert [r[0] for r in cur.fetchall()] == ["SAV500"]

    def test_removing_every_row_deletes_all_plates_but_keeps_the_order(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="3005", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV700", quantity=1),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3005", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [], "plate_type": [], "plate_number": [], "plate_to": [],
            "city": [], "quantity": [], "is_plate_annulled": [],
        })
        assert resp.status_code == 302

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM plates WHERE order_id = %s", (order_id,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM production_orders WHERE id = %s", (order_id,))
            assert cur.fetchone()[0] == 1  # the order itself survives, just empty

    def test_new_row_is_flagged_a_possible_duplicate_against_a_live_plate(self, authenticated_client, db_conn):
        _insert_order(db_conn, order_number="3006", order_date=date(2026, 1, 1), requested_by="PEDRO")
        db.insert_plate(
            db_conn,
            order_id=_insert_order(db_conn, order_number="3007", order_date=date(2026, 1, 1),
                                      requested_by="PEDRO"),
            row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV800", quantity=1),
            is_possible_duplicate=False,
        )
        other_order_id = _insert_order(db_conn, order_number="3008", order_date=date(2026, 1, 1),
                                          requested_by="MARIA")
        db_conn.commit()

        authenticated_client.post(f"/records/{other_order_id}/save", data={
            "order_number": "3008", "order_date": "2026-01-01", "requested_by": "MARIA",
            "plate_id": [""], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["sav800"], "plate_to": [""], "city": [""],
            "quantity": ["1"], "is_plate_annulled": [""],
        })

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT is_possible_duplicate FROM plates WHERE order_id = %s AND plate_number = 'SAV800'",
                (other_order_id,),
            )
            assert cur.fetchone() == (True,)

    def test_recalculates_inventory_after_saving(self, authenticated_client, db_conn):
        db.insert_blank_preparation(db_conn, prep_date=date(2026, 1, 1), category="CARRO PARTICULAR",
                                     quantity=100, prepared_by=None)
        order_id = _insert_order(db_conn, order_number="3009", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db_conn.commit()

        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3009", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [""], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["sav900"], "plate_to": [""], "city": [""],
            "quantity": ["15"], "is_plate_annulled": [""],
        })

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            assert cur.fetchone() == (15,)

    def test_invalid_plate_number_rerenders_with_error_and_saves_nothing(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="3010", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="SAV111", quantity=1),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "SHOULD-NOT-STICK", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [""], "plate_type": ["CARRO PARTICULAR"],
            # CARRO PARTICULAR requires AAA000 -- all-digits doesn't match.
            "plate_number": ["123456"], "plate_to": [""], "city": [""],
            "quantity": ["1"], "is_plate_annulled": [""],
        })
        assert resp.status_code == 400
        assert b"Error" in resp.data

        with db_conn.cursor(row_factory=tuple_row) as cur:
            # the whole save is one transaction: the header edit didn't stick
            # either, and the original plate is untouched.
            cur.execute("SELECT order_number FROM production_orders WHERE id = %s", (order_id,))
            assert cur.fetchone() == ("3010",)
            cur.execute("SELECT count(*) FROM plates WHERE order_id = %s", (order_id,))
            assert cur.fetchone()[0] == 1

    def test_future_date_is_rejected(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="3011", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db_conn.commit()

        resp = authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3011", "order_date": "2999-01-01", "requested_by": "PEDRO",
            "plate_id": [], "plate_type": [], "plate_number": [], "plate_to": [],
            "city": [], "quantity": [], "is_plate_annulled": [],
        })
        assert resp.status_code == 400
        assert "futura" in resp.get_data(as_text=True)

    def test_nonexistent_order_redirects_to_records(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/records/999999/save", data={
            "order_number": "X", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [], "plate_type": [], "plate_number": [], "plate_to": [],
            "city": [], "quantity": [], "is_plate_annulled": [],
        })
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/records"



class TestRecordSavePosFields:
    """Fase 3 parity: record_save re-resolves customer_id from a possibly-
    edited requested_by (per the plan's B5, only WHICH customer it points to
    -- never that customer's own stored attributes), and writes the 10
    order-level POS fields + per-line reason/vehicle_brand."""

    def test_renaming_requested_by_resolves_and_points_to_a_new_customer(
        self, authenticated_client, db_conn
    ):
        order_id = _insert_order(db_conn, order_number="3100", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db_conn.commit()

        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3100", "order_date": "2026-01-01", "requested_by": "MARIA NUEVA",
            "plate_id": [], "plate_type": [], "plate_number": [], "plate_to": [],
            "city": [], "quantity": [], "is_plate_annulled": [],
        })

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT customer_id, requested_by FROM production_orders WHERE id = %s",
                        (order_id,))
            customer_id, requested_by = cur.fetchone()
            assert requested_by == "MARIA NUEVA"
            cur.execute("SELECT name FROM customers WHERE id = %s", (customer_id,))
            assert cur.fetchone() == ("MARIA NUEVA",)

    def test_never_rewrites_an_existing_customers_own_attributes(self, authenticated_client, db_conn):
        """B5: record_save may only re-point customer_id, never edit the
        customer's own stored data -- there is no form field for it at all."""
        with db.get_conn() as conn:
            db.resolve_or_create_customer(conn, "PEDRO", type="EMPRESA", phone="555")
            conn.commit()
        order_id = _insert_order(db_conn, order_number="3101", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db_conn.commit()

        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3101", "order_date": "2026-01-02", "requested_by": "PEDRO",
            "plate_id": [], "plate_type": [], "plate_number": [], "plate_to": [],
            "city": [], "quantity": [], "is_plate_annulled": [],
        })

        with db.get_conn() as conn:
            customer = db.get_customer_by_name(conn, "PEDRO")
        assert customer is not None
        assert customer.type == "EMPRESA"
        assert customer.phone == "555"

    def test_saves_order_level_pos_fields(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="3102", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db_conn.commit()

        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3102", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "receipt_type": "RECIBO", "receipt_number": "R-9", "payment_method": "CRÉDITO",
            "tracking_number": "G-9", "client_order_number": "P-9",
            "delivery_date": "2026-01-15", "payment_date": "2026-01-16",
            "dispatched_by": "CARLOS", "received_by": "SOFIA", "notes": "nota",
            "plate_id": [], "plate_type": [], "plate_number": [], "plate_to": [],
            "city": [], "quantity": [], "is_plate_annulled": [],
        })

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT receipt_type, receipt_number, payment_method, tracking_number, "
                "client_order_number, dispatched_by, received_by, notes "
                "FROM production_orders WHERE id = %s",
                (order_id,),
            )
            assert cur.fetchone() == (
                "RECIBO", "R-9", "CRÉDITO", "G-9", "P-9", "CARLOS", "SOFIA", "nota",
            )

    def test_edits_reason_and_vehicle_brand_on_an_existing_plate(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="3103", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="RVB100"),
                         is_possible_duplicate=False)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'RVB100'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3103", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [str(plate_id)], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["RVB100"], "plate_to": [""], "city": [""],
            "quantity": ["1"], "is_plate_annulled": [""],
            "reason": ["PÉRDIDA"], "vehicle_brand": ["FORD"], "unit_price": [""],
        })

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT reason, vehicle_brand FROM plates WHERE id = %s", (plate_id,))
            assert cur.fetchone() == ("PÉRDIDA", "FORD")

    def test_existing_plates_unit_price_cannot_be_overwritten_via_the_form(
        self, authenticated_client, db_conn
    ):
        """unit_price for an already-saved plate is never a caller-supplied
        value -- update_plate_full has no such parameter, so even a POST that
        carries a tampered unit_price for an existing row is ignored; only
        the re-snapshot rule (plate_type changed or not) governs it."""
        order_id = _insert_order(db_conn, order_number="3104", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="NOP100",
                                      unit_price=Decimal("150000")),
                         is_possible_duplicate=False)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT id FROM plates WHERE plate_number = 'NOP100'")
            plate_id = cur.fetchone()[0]
        db_conn.commit()

        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3104", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [str(plate_id)], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["NOP100"], "plate_to": [""], "city": [""],
            "quantity": ["1"], "is_plate_annulled": [""],
            "unit_price": ["999999"],  # tampered -- must be ignored
        })

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT unit_price FROM plates WHERE id = %s", (plate_id,))
            assert cur.fetchone() == (Decimal("150000"),)  # untouched, type didn't change

    def test_new_plate_row_falls_back_to_the_current_tariff_when_left_blank(
        self, authenticated_client, db_conn
    ):
        order_id = _insert_order(db_conn, order_number="3105", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.set_price(db_conn, "MOTO PARTICULAR", Decimal("90000"), "admin")
        db_conn.commit()

        authenticated_client.post(f"/records/{order_id}/save", data={
            "order_number": "3105", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "plate_id": [""], "plate_type": ["MOTO PARTICULAR"],
            "plate_number": ["NEW90D"], "plate_to": [""], "city": [""],
            "quantity": ["1"], "is_plate_annulled": [""], "unit_price": [""],
        })

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT unit_price FROM plates WHERE plate_number = 'NEW90D'")
            assert cur.fetchone() == (Decimal("90000"),)



class TestRecordsDeleteRoute:
    def test_deletes_order_and_its_plates_and_redirects(self, authenticated_client, db_conn):
        order_id = _insert_order(db_conn, order_number="2800", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="RD111", quantity=8),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = authenticated_client.post("/records/delete", data={"order_id": str(order_id)})
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/records"

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE id = %s", (order_id,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM plates WHERE order_id = %s", (order_id,))
            assert cur.fetchone()[0] == 0

    def test_recalc_still_reflects_a_second_order_that_still_has_plates(self, authenticated_client, db_conn):
        """Complements the review/delete quirk test above: verify recalc DOES
        correctly reflect consumed when the category still has other live
        plates left after the delete (i.e. the stale-value quirk is specific
        to a category dropping to zero plates, not recalc being broken
        outright)."""
        db.insert_blank_preparation(db_conn, prep_date=date(2026, 1, 1), category="CARRO PARTICULAR",
                                     quantity=100, prepared_by=None)
        keep_order = _insert_order(db_conn, order_number="2801", order_date=date(2026, 1, 1),
                                      requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=keep_order,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="RD200", quantity=3),
                         is_possible_duplicate=False)
        delete_order = _insert_order(db_conn, order_number="2802", order_date=date(2026, 1, 1),
                                        requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=delete_order,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="RD201", quantity=8),
                         is_possible_duplicate=False)
        db.recalc_inventory(db_conn)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            assert cur.fetchone() == (11,)  # sanity check before deletion
        db_conn.commit()

        authenticated_client.post("/records/delete", data={"order_id": str(delete_order)})

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            assert cur.fetchone() == (3,)  # correctly dropped to just the surviving order

