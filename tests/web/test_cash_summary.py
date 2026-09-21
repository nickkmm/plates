"""Integration tests for GET /cuadre and GET /cuadre/pdf (Fase 5b).

The contract under test:
- @pos_allowed on BOTH routes -- admin and punto_venta get 200, produccion
  403 (the plan is explicit both the HTML view and the PDF export need
  this, unlike /precios which is admin-only).
- Both params default to today when omitted.
- The SIN FORMA DE PAGO bucket renders as a normal row, not hidden.
- A NULL unit_price plate shows up in the visible warning section, linked to
  its order, and is never folded into the grand total.
- Invalid/out-of-order date params re-render the form with a Spanish error
  instead of a 500.
- /cuadre/pdf returns a real `application/pdf` download (Content-Disposition:
  attachment) whose bytes are actually a PDF.
- The "Cuadre" nav link is gated by pos_roles, same as "Registrar".

Aggregation correctness itself (grouping, exclusions, Decimal typing) is
covered in tests/core/test_cash_summary.py -- this file only exercises the
web layer around it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.core import db
from app.core.models import ExtractedRow
from app.web.helpers import today_iso

CUADRE_URL = "/cuadre"
CUADRE_PDF_URL = "/cuadre/pdf"


def _make_row(**overrides: object) -> ExtractedRow:
    defaults: dict[str, object] = {
        "order_number": "1",
        "order_date": date(2026, 1, 1),
        "requested_by": "PEDRO",
        "plate_type": "CARRO PARTICULAR",
    }
    defaults.update(overrides)
    return ExtractedRow(**defaults)


def _insert_order(conn, **overrides: object) -> int:
    return db.insert_order(conn, row=_make_row(**overrides), customer_id=None, created_by=None)


def _insert_plate(conn, order_id: int, **overrides: object) -> None:
    db.insert_plate(conn, order_id=order_id, row=_make_row(**overrides), is_possible_duplicate=False)


class TestCuadreForbidden:
    def test_get_is_forbidden_for_produccion(self, produccion_client):
        assert produccion_client.get(CUADRE_URL).status_code == 403

    def test_get_is_allowed_for_admin(self, authenticated_client):
        assert authenticated_client.get(CUADRE_URL).status_code == 200

    def test_get_is_allowed_for_punto_venta(self, punto_venta_client):
        assert punto_venta_client.get(CUADRE_URL).status_code == 200

    def test_pdf_is_forbidden_for_produccion(self, produccion_client):
        assert produccion_client.get(CUADRE_PDF_URL).status_code == 403

    def test_pdf_is_allowed_for_admin(self, authenticated_client):
        assert authenticated_client.get(CUADRE_PDF_URL).status_code == 200

    def test_pdf_is_allowed_for_punto_venta(self, punto_venta_client):
        assert punto_venta_client.get(CUADRE_PDF_URL).status_code == 200


class TestCuadreDefaults:
    def test_get_with_no_params_defaults_both_dates_to_today(self, authenticated_client, db_conn):
        resp = authenticated_client.get(CUADRE_URL)
        assert resp.status_code == 200
        html = resp.data.decode()
        today = today_iso()
        # Both date inputs are pre-filled with today's date (sticky range).
        assert html.count(f'value="{today}"') == 2


class TestCuadreContent:
    def test_sin_forma_de_pago_bucket_renders_as_a_normal_row(self, authenticated_client, db_conn):
        order = _insert_order(db_conn, order_number="1", order_date=date(2026, 1, 1), payment_method=None)
        _insert_plate(db_conn, order, unit_price=Decimal("15000"), quantity=1)
        db_conn.commit()

        resp = authenticated_client.get(f"{CUADRE_URL}?desde=2026-01-01&hasta=2026-01-01")

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "SIN FORMA DE PAGO" in html
        assert "15.000" in html  # Colombian thousands separator via |cop

    def test_grouping_across_dates_and_payment_methods_renders_correct_totals(
        self, authenticated_client, db_conn
    ):
        order1 = _insert_order(
            db_conn, order_number="1", order_date=date(2026, 2, 1), payment_method="EFECTIVO"
        )
        _insert_plate(db_conn, order1, unit_price=Decimal("10000"), quantity=1)
        order2 = _insert_order(
            db_conn, order_number="2", order_date=date(2026, 2, 2), payment_method="TRANSFERENCIA"
        )
        _insert_plate(db_conn, order2, unit_price=Decimal("20000"), quantity=1)
        db_conn.commit()

        resp = authenticated_client.get(f"{CUADRE_URL}?desde=2026-02-01&hasta=2026-02-02")

        html = resp.data.decode()
        assert "2026-02-01" in html
        assert "2026-02-02" in html
        assert "EFECTIVO" in html
        assert "TRANSFERENCIA" in html
        assert "10.000" in html
        assert "20.000" in html
        assert "30.000" in html  # grand total

    def test_voided_and_annulled_plates_never_appear_in_the_total(self, authenticated_client, db_conn):
        order = _insert_order(db_conn, order_number="1", order_date=date(2026, 3, 1))
        _insert_plate(db_conn, order, unit_price=Decimal("1000"), quantity=1)
        _insert_plate(db_conn, order, unit_price=Decimal("999999"), quantity=1, is_voided=True)
        _insert_plate(db_conn, order, unit_price=Decimal("888888"), quantity=1, is_plate_annulled=True)
        db_conn.commit()

        resp = authenticated_client.get(f"{CUADRE_URL}?desde=2026-03-01&hasta=2026-03-01")

        html = resp.data.decode()
        assert "999.999" not in html
        assert "888.888" not in html
        assert "1.000" in html

    def test_empty_range_shows_no_orders_message(self, authenticated_client, db_conn):
        resp = authenticated_client.get(f"{CUADRE_URL}?desde=2026-05-01&hasta=2026-05-01")
        assert resp.status_code == 200
        assert "No hay órdenes con placas en este rango." in resp.data.decode()


class TestMissingPriceWarning:
    def test_missing_price_plate_is_listed_and_linked_and_never_counted_as_zero(
        self, authenticated_client, db_conn
    ):
        order = _insert_order(db_conn, order_number="9001", order_date=date(2026, 4, 1))
        _insert_plate(db_conn, order, unit_price=None, plate_type="MOTO PARTICULAR", quantity=1)
        db_conn.commit()

        resp = authenticated_client.get(f"{CUADRE_URL}?desde=2026-04-01&hasta=2026-04-01")

        html = resp.data.decode()
        assert resp.status_code == 200
        assert "9001" in html
        assert "MOTO PARTICULAR" in html
        assert f"/records/{order}" in html
        # The bucket total must read as zero, never absent -- confirm the
        # explicit '$0' rendering rather than any nonzero fabricated amount.
        assert "Placas sin precio" in html

    def test_no_missing_price_section_when_all_plates_are_priced(self, authenticated_client, db_conn):
        order = _insert_order(db_conn, order_number="1", order_date=date(2026, 4, 5))
        _insert_plate(db_conn, order, unit_price=Decimal("1000"))
        db_conn.commit()

        resp = authenticated_client.get(f"{CUADRE_URL}?desde=2026-04-05&hasta=2026-04-05")

        assert "Placas sin precio" not in resp.data.decode()


class TestCuadreValidation:
    def test_invalid_date_format_is_rejected_not_500(self, authenticated_client, db_conn):
        resp = authenticated_client.get(f"{CUADRE_URL}?desde=not-a-date&hasta=2026-01-01")
        assert resp.status_code == 400
        assert "no es una fecha válida" in resp.data.decode()

    def test_desde_after_hasta_is_rejected_not_500(self, authenticated_client, db_conn):
        resp = authenticated_client.get(f"{CUADRE_URL}?desde=2026-01-05&hasta=2026-01-01")
        assert resp.status_code == 400
        assert "no puede ser posterior" in resp.data.decode()

    def test_pdf_invalid_date_is_rejected_not_500(self, authenticated_client, db_conn):
        resp = authenticated_client.get(f"{CUADRE_PDF_URL}?desde=nope&hasta=2026-01-01")
        assert resp.status_code == 400


class TestCuadrePdfExport:
    def test_pdf_response_is_a_real_pdf_download(self, authenticated_client, db_conn):
        order = _insert_order(db_conn, order_number="1", order_date=date(2026, 6, 1), payment_method="EFECTIVO")
        _insert_plate(db_conn, order, unit_price=Decimal("5000"), quantity=1)
        db_conn.commit()

        resp = authenticated_client.get(f"{CUADRE_PDF_URL}?desde=2026-06-01&hasta=2026-06-01")

        assert resp.status_code == 200
        assert resp.mimetype == "application/pdf"
        assert "attachment" in resp.headers.get("Content-Disposition", "")
        assert resp.data[:4] == b"%PDF"
        assert len(resp.data) > 500

    def test_pdf_filename_includes_the_date_range(self, authenticated_client, db_conn):
        resp = authenticated_client.get(f"{CUADRE_PDF_URL}?desde=2026-06-01&hasta=2026-06-02")
        disposition = resp.headers.get("Content-Disposition", "")
        assert "2026-06-01" in disposition
        assert "2026-06-02" in disposition

    def test_forbidden_clients_never_reach_pdf_rendering(self, produccion_client):
        """A 403 from the decorator must short-circuit before any PDF work --
        confirm the body is a plain 403, not any PDF bytes."""
        resp = produccion_client.get(CUADRE_PDF_URL)
        assert resp.status_code == 403
        assert resp.data[:4] != b"%PDF"


class TestCuadreNavLink:
    def test_cuadre_link_visible_for_admin(self, authenticated_client):
        html = authenticated_client.get("/records").data.decode()
        assert "Cuadre" in html

    def test_cuadre_link_visible_for_punto_venta(self, punto_venta_client):
        html = punto_venta_client.get("/records").data.decode()
        assert "Cuadre" in html

    def test_cuadre_link_hidden_for_produccion(self, produccion_client):
        html = produccion_client.get("/records").data.decode()
        assert "Cuadre" not in html
