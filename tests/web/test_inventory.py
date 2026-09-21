"""Integration tests for app/web/blueprints/inventory.py -- GET /inventory
(material cards + ornamental counter) and POST /inventory/alistado
(records a blank-plate preparation, recalculates inventory, and
redirects).

Needs both an authenticated client and a real Postgres connection
(`db_conn`/`clean_db`) -- see tests/web/test_review.py's module
docstring for the full separate-connection-per-checkout caveat."""

from __future__ import annotations

from datetime import date
from urllib.parse import urlparse

from psycopg.rows import tuple_row

from app.core import db
from tests.web._helpers import _insert_order, make_row


class TestInventoryRoute:
    def test_renders_material_cards_and_ornamental_counter(self, authenticated_client, db_conn):
        db.insert_blank_preparation(db_conn, prep_date=date(2026, 1, 1), category="CARRO PARTICULAR",
                                     quantity=50, prepared_by=None)
        order_id = _insert_order(db_conn, order_number="2500", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_type="ORNAMENTAL", quantity=3),
                         is_possible_duplicate=False)
        db.recalc_inventory(db_conn)
        db_conn.commit()

        resp = authenticated_client.get("/inventory")
        assert resp.status_code == 200
        assert b"CARRO PARTICULAR" in resp.data
        assert b"ORNAMENTAL" in resp.data

    def test_empty_db_still_renders_200(self, authenticated_client, clean_db):
        resp = authenticated_client.get("/inventory")
        assert resp.status_code == 200
        assert b"ORNAMENTAL" in resp.data



class TestInventoryAlistadoRoute:
    def test_inserts_blank_preparation_recalculates_and_redirects(self, authenticated_client, clean_db):
        resp = authenticated_client.post("/inventory/alistado", data={
            "category": "MOTO PARTICULAR", "quantity": "250",
            "prep_date": "2026-01-01", "prepared_by": "GILMER",
        })
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/inventory"

        with db.get_conn() as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT category, quantity, prepared_by FROM blank_preparations")
            assert cur.fetchall() == [("MOTO PARTICULAR", 250, "GILMER")]
            cur.execute("SELECT prepared FROM inventory_categories WHERE category = 'MOTO PARTICULAR'")
            assert cur.fetchone() == (250,)

