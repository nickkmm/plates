"""Cross-cutting role-based access tests -- deliberately NOT bucketed into
a single blueprint file like the other tests/web/test_*.py files above.
TestProduccionRole and TestPuntoVentaRole each assert one role's access
across MULTIPLE blueprints at once (records/ingest/review/inventory) in
the same test class, so a single-blueprint destination would be wrong
for them -- they get their own file instead.

Needs both an authenticated client and a real Postgres connection
(`db_conn`/`clean_db`) -- see tests/web/test_review.py's module
docstring for the full separate-connection-per-checkout caveat."""

from __future__ import annotations

from datetime import date
from urllib.parse import urlparse

from app.core import db
from tests.web._helpers import _insert_order, make_row


class TestProduccionRole:
    """The read-only 'production' role (see app.web.auth.consulta_allowed) --
    started as a full-access role and was demoted to read-only once real
    usage showed it should only ever look things up (see
    auth.FULL_ACCESS_ROLES) -- may only reach the small consult-only
    allowlist -- /records and /records/<id> -- and must be default-denied
    (403) everywhere else that writes or otherwise isn't a pure lookup.
    These tests never touch the DB for the 403 assertions: the role check in
    login_required/consulta_allowed runs before any view body, so an
    empty/garbage POST body is enough to prove the request never reached the
    handler.
    """

    def test_records_returns_200(self, produccion_client, db_conn):
        _insert_order(db_conn, order_number="5100", order_date=date(2026, 1, 1),
                         requested_by="PEDRO")
        db_conn.commit()

        resp = produccion_client.get("/records")
        assert resp.status_code == 200
        assert b"5100" in resp.data

    def test_recent_orders_list_hides_the_delete_button(self, produccion_client, db_conn):
        """/records/delete already 403s for 'production' (see below), but the
        recent-orders list used to show the 'Eliminar' button to every role
        regardless -- same class of bug as the plate-edit form: it looked
        available even though clicking it would just 403."""
        _insert_order(db_conn, order_number="5101", order_date=date(2026, 1, 1),
                         requested_by="PEDRO")
        db_conn.commit()

        resp = produccion_client.get("/records")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "5101" in html
        assert "Eliminar" not in html
        assert 'action="/records/delete"' not in html

    def test_record_detail_shows_a_read_only_table_not_the_edit_form(self, produccion_client, db_conn):
        """Regression guard: /records/<id>/save already 403s for 'production'
        (see below), but the page itself used to render the same editable
        inputs and 'Guardar' button to every role regardless -- so a
        read-only user saw something that looked editable but wasn't, which
        is exactly the confusing behavior reported. No form, no inputs, no
        save button; the plate's data must still be visible as plain text."""
        order_id = _insert_order(db_conn, order_number="5102", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="PRD111", quantity=2),
                         is_possible_duplicate=False)
        db_conn.commit()

        resp = produccion_client.get(f"/records/{order_id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "PRD111" in html
        assert 'name="plate_number"' not in html
        assert 'name="plate_id"' not in html
        assert "Guardar" not in html
        assert f'action="/records/{order_id}/save"' not in html
        assert "Agregar" not in html
        assert "<form" not in html.split("</nav>")[1]  # no form anywhere past the nav bar

    def test_ingest_get_is_forbidden(self, produccion_client):
        resp = produccion_client.get("/ingest")
        assert resp.status_code == 403

    def test_ingest_post_is_forbidden(self, produccion_client):
        resp = produccion_client.post("/ingest", data={
            "payment_method": "OTROS",})
        assert resp.status_code == 403

    def test_ingest_pdf_is_forbidden(self, produccion_client):
        resp = produccion_client.get("/ingest/pdf")
        assert resp.status_code == 403

    def test_review_is_forbidden(self, produccion_client):
        resp = produccion_client.get("/review")
        assert resp.status_code == 403

    def test_review_update_is_forbidden(self, produccion_client):
        resp = produccion_client.post("/review/update", data={})
        assert resp.status_code == 403

    def test_inventory_is_forbidden(self, produccion_client):
        resp = produccion_client.get("/inventory")
        assert resp.status_code == 403

    def test_inventory_alistado_is_forbidden(self, produccion_client):
        resp = produccion_client.post("/inventory/alistado", data={})
        assert resp.status_code == 403

    def test_records_delete_is_forbidden(self, produccion_client):
        resp = produccion_client.post("/records/delete", data={})
        assert resp.status_code == 403

    def test_record_save_is_forbidden(self, produccion_client):
        """Reconciliation guard: /records/<id>/save (the consolidated
        edit-order-in-one-shot route) is a write route -- it must stay on the
        default-deny @login_required, not the consult-only allowlist."""
        resp = produccion_client.post("/records/1/save", data={})
        assert resp.status_code == 403

    def test_nav_hides_full_access_only_links(self, produccion_client):
        resp = produccion_client.get("/records")
        html = resp.data.decode()
        assert 'href="/ingest"' not in html
        assert 'href="/review"' not in html
        assert 'href="/inventory"' not in html
        assert 'href="/precios"' not in html
        assert 'href="/customers"' not in html

    def test_admin_client_is_unaffected_full_access_regression_guard(
        self, authenticated_client, clean_db
    ):
        """The existing admin-role fixture must still get through everywhere
        -- the role gate must not accidentally narrow full access."""
        assert authenticated_client.get("/ingest").status_code == 200
        assert authenticated_client.get("/review").status_code == 200
        assert authenticated_client.get("/inventory").status_code == 200
        assert authenticated_client.get("/records").status_code == 200
        assert authenticated_client.get("/customers").status_code == 200



class TestPuntoVentaRole:
    """'point_of_sale' (Fase 2's POS_ROLES) is the Fase 3 payoff: narrower than
    admin, but wide enough to actually run the POS capture flow -- /ingest,
    /ingest/pdf, and /records/<id>/save now sit behind @pos_allowed instead
    of admin-only @login_required. Everything admin-only (review, inventario,
    records delete) must still 403 it, same as production."""

    def test_lands_on_ingest_not_records(self, client, punto_venta_credentials):
        username, password = punto_venta_credentials
        resp = client.post("/login", data={"username": username, "password": password})
        assert resp.status_code == 302
        resp2 = client.get("/")
        assert urlparse(resp2.headers["Location"]).path == "/ingest"

    def test_nav_shows_registrar_but_not_admin_only_links(self, punto_venta_client, clean_db):
        resp = punto_venta_client.get("/ingest")
        html = resp.data.decode()
        assert 'href="/ingest"' in html
        assert 'href="/review"' not in html
        assert 'href="/inventory"' not in html
        assert 'href="/precios"' not in html
        assert 'href="/customers"' not in html

    def test_ingest_get_returns_200(self, punto_venta_client, clean_db):
        assert punto_venta_client.get("/ingest").status_code == 200

    def test_ingest_pdf_get_returns_200(self, punto_venta_client, clean_db):
        assert punto_venta_client.get("/ingest/pdf").status_code == 200

    def test_can_save_an_order_through_ingest(self, punto_venta_client, clean_db):
        resp = punto_venta_client.post("/ingest", data={
            "payment_method": "EFECTIVO",
            "order_number": "7000", "order_date": "2026-01-01", "requested_by": "PEDRO",
            "line_type": ["placa"], "plate_type": ["CARRO PARTICULAR"],
            "plate_number": ["PTV100"], "plate_to": [""], "quantity": ["1"], "city": [""],
        })
        assert resp.status_code == 200
        assert b"Registrado" in resp.data

    def test_can_reach_and_save_record_detail(self, punto_venta_client, db_conn):
        order_id = _insert_order(db_conn, order_number="7001", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db_conn.commit()
        resp = punto_venta_client.get(f"/records/{order_id}")
        assert resp.status_code == 200
        assert 'name="order_number"' in resp.data.decode()  # sees the real edit form, not read-only

        resp = punto_venta_client.post(f"/records/{order_id}/save", data={
            "order_number": "7001", "order_date": "2026-01-01", "requested_by": "MARIA",
            "plate_id": [], "plate_type": [], "plate_number": [], "plate_to": [],
            "city": [], "quantity": [], "is_plate_annulled": [],
        })
        assert resp.status_code == 302

    def test_review_is_forbidden(self, punto_venta_client):
        assert punto_venta_client.get("/review").status_code == 403

    def test_inventory_is_forbidden(self, punto_venta_client):
        assert punto_venta_client.get("/inventory").status_code == 403

    def test_inventory_alistado_is_forbidden(self, punto_venta_client):
        assert punto_venta_client.post("/inventory/alistado", data={}).status_code == 403

    def test_records_delete_is_forbidden(self, punto_venta_client):
        assert punto_venta_client.post("/records/delete", data={}).status_code == 403

    def test_records_read_access_still_works(self, punto_venta_client, db_conn):
        _insert_order(db_conn, order_number="7002", order_date=date(2026, 1, 1), requested_by="PEDRO")
        db_conn.commit()
        resp = punto_venta_client.get("/records")
        assert resp.status_code == 200
        assert b"7002" in resp.data



class TestDeclarationsNav:
    """/declaraciones is for admin + point_of_sale only; never the read-only role."""

    def test_nav_link_hidden_for_production(self, produccion_client):
        assert 'href="/declaraciones"' not in produccion_client.get("/records").data.decode()

    def test_production_gets_403_on_declaraciones(self, produccion_client):
        assert produccion_client.get("/declaraciones").status_code == 403

    def test_nav_link_shown_for_point_of_sale(self, punto_venta_client, clean_db):
        assert 'href="/declaraciones"' in punto_venta_client.get("/ingest").data.decode()

    def test_nav_link_shown_for_admin(self, authenticated_client, clean_db):
        assert 'href="/declaraciones"' in authenticated_client.get("/ingest").data.decode()
