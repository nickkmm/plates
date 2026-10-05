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

    def test_a_customer_with_only_person_fields_is_completo(self, authenticated_client, db_conn):
        db.resolve_or_create_customer(db_conn, "SOLO DIRECCION", address="CALLE 1")
        html = authenticated_client.get(CUSTOMERS_URL).data.decode()
        assert "completo" in html
        assert "sin datos" not in html

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

    def test_shows_person_fields_and_the_new_type_vocabulary(self, authenticated_client, db_conn):
        customer_id = db.resolve_or_create_customer(
            db_conn, "PERSONA", type="CLIENTE", address="CALLE 77", neighborhood="LAS PALMAS",
            municipality="JAMUNDÍ",
        )
        html = authenticated_client.get(_detail_url(customer_id)).data.decode()
        for expected in ("CALLE 77", "LAS PALMAS", "JAMUNDÍ", "Dirección", "Barrio", "Municipio"):
            assert expected in html
        for ct in ("TRÁNSITO", "EMPRESA", "GESTOR", "CLIENTE"):
            assert f'<option value="{ct}"' in html
        assert 'value="PERSONA NATURAL"' not in html
        assert "Licencia de tránsito" not in html
        assert '<option value="CÉDULA DE EXTRANJERÍA"' in html

    def test_person_inputs_are_disabled_for_empresa(self, authenticated_client, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "UNA EMPRESA", type="EMPRESA")
        html = authenticated_client.get(_detail_url(customer_id)).data.decode()
        assert 'id="customerAddress" value="" disabled' in html
        assert 'id="documentRelation" disabled' in html

    def test_person_inputs_are_enabled_for_cliente(self, authenticated_client, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "UN CLIENTE", type="CLIENTE")
        html = authenticated_client.get(_detail_url(customer_id)).data.decode()
        assert 'id="customerAddress" value="" disabled' not in html
        assert 'id="documentRelation" disabled' not in html

    def test_nonexistent_id_redirects_to_the_list(self, authenticated_client, db_conn):
        resp = authenticated_client.get(_detail_url(999999))
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/customers")


class TestCustomerSave:
    def test_overwrites_every_attribute(self, authenticated_client, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "PTO.V", type="EMPRESA")
        resp = authenticated_client.post(_detail_url(customer_id), data={
            "name": "PTO.V",
            "customer_type": "CLIENTE",
            "customer_document_type": "CÉDULA",
            "customer_document_number": "999",
            "customer_document_relation": "DUEÑO",
            "customer_phone": "3009998877",
            "customer_email": "nuevo@correo.com",
            "customer_address": "CALLE 5 # 6-7",
            "customer_neighborhood": "SAN FERNANDO",
            "customer_municipality": "CALI",
        })
        assert resp.status_code == 302

        customer = db.get_customer(db_conn, customer_id)
        assert customer is not None
        assert customer.type == "CLIENTE"
        assert customer.document_relation == "DUEÑO"
        assert customer.address == "CALLE 5 # 6-7"
        assert customer.neighborhood == "SAN FERNANDO"
        assert customer.municipality == "CALI"
        assert customer.document_number == "999"
        assert customer.phone == "3009998877"
        assert customer.email == "nuevo@correo.com"

    def test_gestor_keeps_person_fields_but_not_document_relation(self, authenticated_client, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "GESTORIA")
        authenticated_client.post(_detail_url(customer_id), data={
            "name": "GESTORIA", "customer_type": "GESTOR",
            "customer_document_relation": "DUEÑO", "customer_address": "CALLE 1",
            "customer_neighborhood": "CENTRO", "customer_municipality": "TULUÁ",
        })
        customer = db.get_customer(db_conn, customer_id)
        assert customer is not None
        assert customer.type == "GESTOR"
        assert customer.document_relation is None
        assert (customer.address, customer.neighborhood, customer.municipality) == (
            "CALLE 1", "CENTRO", "TULUÁ",
        )

    def test_changing_to_empresa_drops_stored_person_fields(self, authenticated_client, db_conn):
        customer_id = db.resolve_or_create_customer(
            db_conn, "CAMBIA", type="CLIENTE", document_relation="DUEÑO", address="CALLE 1",
            neighborhood="CENTRO", municipality="CALI",
        )
        authenticated_client.post(_detail_url(customer_id), data={
            "name": "CAMBIA", "customer_type": "EMPRESA", "customer_document_type": "NIT",
            "customer_document_number": "900", "customer_document_relation": "DUEÑO",
            "customer_address": "CALLE 1", "customer_neighborhood": "CENTRO",
            "customer_municipality": "CALI",
        })
        customer = db.get_customer(db_conn, customer_id)
        assert customer is not None
        assert customer.type == "EMPRESA"
        assert customer.document_relation is None
        assert customer.address is None
        assert customer.neighborhood is None
        assert customer.municipality is None

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
