"""Integration tests for GET /customers, GET/POST /customers/<id> (Fase 6).

The contract under test:
- Admin-only via @login_required (NOT @pos_allowed), same access level as
  /precios -- master data, not order/payment capture, so 'point_of_sale'
  must 403 here too.
- The list page shows every customer and supports a ?q= substring filter
  on name.
- The edit form does a REAL overwrite (db.update_customer), unlike
  db.resolve_or_create_customer's passive, never-clobber-an-existing-value
  backfill -- an admin can knowingly blank out or replace any field,
  including the name.
- A blank name, or a rename that collides with a different existing
  customer, re-renders the form with a Spanish error instead of a 500 or a
  silently-accepted bad value.
"""

from __future__ import annotations

from app.core import db

CUSTOMERS_URL = "/customers"


def _detail_url(customer_id: int) -> str:
    return f"/customers/{customer_id}"


class TestCustomersList:
    def test_lists_every_customer(self, authenticated_client, db_conn):
        db.resolve_or_create_customer(db_conn, "ZETA")
        db.resolve_or_create_customer(db_conn, "ALFA")
        html = authenticated_client.get(CUSTOMERS_URL).data.decode()
        assert "ZETA" in html
        assert "ALFA" in html

    def test_marks_a_customer_with_no_data_as_sin_datos(self, authenticated_client, db_conn):
        db.resolve_or_create_customer(db_conn, "NUEVO")
        html = authenticated_client.get(CUSTOMERS_URL).data.decode()
        assert "sin datos" in html

    def test_marks_a_customer_with_data_as_completo(self, authenticated_client, db_conn):
        db.resolve_or_create_customer(db_conn, "CON DATOS", phone="3001112233")
        html = authenticated_client.get(CUSTOMERS_URL).data.decode()
        assert "completo" in html

    def test_query_filters_by_substring_of_name(self, authenticated_client, db_conn):
        # Deliberately NOT "PTO.V" -- the search box's own placeholder text
        # ("ej. PTO.V") would false-positive a naive `in html` check.
        db.resolve_or_create_customer(db_conn, "ZAFRAS")
        db.resolve_or_create_customer(db_conn, "MUVA GRANADA")
        html = authenticated_client.get(CUSTOMERS_URL, query_string={"q": "muva"}).data.decode()
        assert "MUVA GRANADA" in html
        assert "ZAFRAS" not in html

    def test_no_customers_shows_empty_message(self, authenticated_client, db_conn):
        html = authenticated_client.get(CUSTOMERS_URL).data.decode()
        assert "No hay clientes registrados" in html


class TestCustomerDetail:
    def test_shows_the_customers_current_attributes(self, authenticated_client, db_conn):
        customer_id = db.resolve_or_create_customer(
            db_conn, "PTO.V", type="EMPRESA", phone="3001112233", email="a@b.com",
        )
        html = authenticated_client.get(_detail_url(customer_id)).data.decode()
        assert "3001112233" in html
        assert "a@b.com" in html

    def test_nonexistent_id_redirects_to_the_list(self, authenticated_client, db_conn):
        resp = authenticated_client.get(_detail_url(999999))
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/customers")


class TestCustomerSave:
    def test_overwrites_every_attribute(self, authenticated_client, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "PTO.V", type="EMPRESA")
        resp = authenticated_client.post(_detail_url(customer_id), data={
            "name": "PTO.V",
            "customer_type": "PERSONA NATURAL",
            "customer_document_type": "CÉDULA",
            "customer_document_number": "999",
            "customer_document_relation": "DUEÑO",
            "customer_phone": "3009998877",
            "customer_email": "nuevo@correo.com",
        })
        assert resp.status_code == 302

        customer = db.get_customer(db_conn, customer_id)
        assert customer is not None
        assert customer.type == "PERSONA NATURAL"
        assert customer.document_number == "999"
        assert customer.phone == "3009998877"
        assert customer.email == "nuevo@correo.com"

    def test_can_rename_the_customer(self, authenticated_client, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "PTO.V VIEJO")
        authenticated_client.post(_detail_url(customer_id), data={"name": "PTO.V NUEVO"})
        customer = db.get_customer(db_conn, customer_id)
        assert customer is not None
        assert customer.name == "PTO.V NUEVO"

    def test_blank_name_is_rejected(self, authenticated_client, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "PTO.V")
        resp = authenticated_client.post(_detail_url(customer_id), data={"name": "  "})
        assert resp.status_code == 400
        assert "no puede quedar vacío" in resp.data.decode()
        customer = db.get_customer(db_conn, customer_id)
        assert customer is not None
        assert customer.name == "PTO.V"

    def test_renaming_to_a_name_already_used_by_another_customer_is_rejected(
        self, authenticated_client, db_conn
    ):
        db.resolve_or_create_customer(db_conn, "MUVA GRANADA")
        other_id = db.resolve_or_create_customer(db_conn, "MUVA SERGIO")
        resp = authenticated_client.post(_detail_url(other_id), data={"name": "MUVA GRANADA"})
        assert resp.status_code == 400
        assert "Ya existe otro cliente" in resp.data.decode()
        customer = db.get_customer(db_conn, other_id)
        assert customer is not None
        assert customer.name == "MUVA SERGIO"  # untouched

    def test_nonexistent_id_redirects_to_the_list(self, authenticated_client, db_conn):
        resp = authenticated_client.post(_detail_url(999999), data={"name": "X"})
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/customers")


class TestCustomersForbidden:
    """@login_required (admin-only), NOT @pos_allowed -- 'point_of_sale'
    captures orders/payments, never edits customer master data, so it 403s
    here same as production."""

    def test_list_is_forbidden_for_production(self, produccion_client):
        assert produccion_client.get(CUSTOMERS_URL).status_code == 403

    def test_list_is_forbidden_for_point_of_sale(self, punto_venta_client):
        assert punto_venta_client.get(CUSTOMERS_URL).status_code == 403

    def test_detail_is_forbidden_for_point_of_sale(self, punto_venta_client, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "PTO.V")
        assert punto_venta_client.get(_detail_url(customer_id)).status_code == 403

    def test_save_is_forbidden_for_point_of_sale(self, punto_venta_client, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "PTO.V", phone="3001112233")
        resp = punto_venta_client.post(_detail_url(customer_id), data={"name": "OTRO NOMBRE"})
        assert resp.status_code == 403
        customer = db.get_customer(db_conn, customer_id)
        assert customer is not None
        assert customer.name == "PTO.V"  # untouched
