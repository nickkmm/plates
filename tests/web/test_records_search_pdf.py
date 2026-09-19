"""Integration tests for POST /records/search-pdf.

The contract under test: uploading a pedido PDF here is CONSULT ONLY. Unlike
/ingest/pdf (which leads toward saving through /ingest), this route may never
insert/update/delete anything in the database, regardless of what the PDF
contains -- it only checks each plate the PDF mentions against the existing
`plates` table and reports whether it's already there.
"""

from __future__ import annotations

import io
from datetime import date
from urllib.parse import urlparse

from psycopg.rows import tuple_row

from app.core import db
from app.core.models import ExtractedRow
from tests.pdf_fixtures import build_pdf_without_table, build_pedido_pdf


def upload(client, data: bytes, filename: str = "pedido.pdf"):
    return client.post(
        "/records/search-pdf",
        data={"pdf": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
    )


def make_row(**overrides: object) -> ExtractedRow:
    defaults: dict[str, object] = {
        "order_number": "1",
        "order_date": date(2026, 1, 1),
        "requested_by": "PEDRO",
        "plate_type": "CARRO PARTICULAR",
    }
    defaults.update(overrides)
    return ExtractedRow(**defaults)


def row_counts(conn) -> tuple[int, int]:
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT count(*) FROM production_orders")
        orders = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM plates")
        plates = cur.fetchone()[0]
        return orders, plates


class TestAuth:
    def test_requires_login(self, client):
        resp = upload(client, build_pedido_pdf())
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/login"


class TestConsultOnly:
    """The payoff: this door must never write to the database."""

    def test_saves_nothing_even_with_real_plate_lines(
        self, authenticated_client, db_conn, monkeypatch
    ):
        def boom(*args, **kwargs):
            raise AssertionError("process_rows must never run from the search-pdf route")

        monkeypatch.setattr("app.web.app.process_rows", boom)

        before = row_counts(db_conn)
        resp = upload(authenticated_client, build_pedido_pdf())
        assert resp.status_code == 200
        after = row_counts(db_conn)
        assert before == after == (0, 0)

    def test_saves_nothing_even_when_a_plate_already_exists(
        self, authenticated_client, db_conn
    ):
        order_id = db.insert_order(
            db_conn, order_number="EXIST-1", order_date=date(2026, 1, 1), requested_by="CLIENTE DEMO"
        )
        db.insert_plate(
            db_conn, order_id=order_id,
            row=make_row(plate_number="FND001", plate_type="CARRO PARTICULAR"),
            is_possible_duplicate=False,
        )
        db_conn.commit()

        before = row_counts(db_conn)
        resp = upload(authenticated_client, build_pedido_pdf(lines=[
            {"item": "1", "placa": "FND001", "tipo": "PARTICULAR"},
        ]))
        assert resp.status_code == 200
        assert row_counts(db_conn) == before


class TestLookupResults:
    def test_an_existing_plate_is_reported_as_found_with_a_link(
        self, authenticated_client, db_conn
    ):
        order_id = db.insert_order(
            db_conn, order_number="EXIST-2", order_date=date(2026, 1, 1), requested_by="CLIENTE DEMO"
        )
        db.insert_plate(
            db_conn, order_id=order_id,
            row=make_row(plate_number="FND002", plate_type="CARRO PARTICULAR"),
            is_possible_duplicate=False,
        )
        db_conn.commit()

        resp = upload(authenticated_client, build_pedido_pdf(lines=[
            {"item": "1", "placa": "FND002", "tipo": "PARTICULAR"},
        ]))
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "FND002" in html
        assert "Ya existe" in html
        assert f'/records/{order_id}' in html
        assert "EXIST-2" in html

    def test_a_plate_not_in_the_db_is_reported_as_not_found(
        self, authenticated_client, clean_db
    ):
        resp = upload(authenticated_client, build_pedido_pdf(lines=[
            {"item": "1", "placa": "MISS001", "tipo": "PARTICULAR"},
        ]))
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "MISS001" in html
        assert "No encontrada" in html
        assert "Ya existe" not in html

    def test_works_even_when_the_header_is_unreadable(
        self, authenticated_client, clean_db
    ):
        """to_rows() would raise on this (missing order_date), but this route
        never calls to_rows() -- only the plate lines matter here."""
        resp = upload(authenticated_client, build_pedido_pdf(order_date="POR CONFIRMAR"))
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "ABC12D" in html

    def test_a_pedido_with_no_real_plate_numbers_shows_the_empty_message(
        self, authenticated_client, clean_db
    ):
        resp = upload(authenticated_client, build_pedido_pdf(lines=[
            {"item": "1", "placa": "MUESTRA", "tipo": "PARTICULAR"},
        ]))
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "no tiene placas para verificar" in html


class TestGracefulFailure:
    def test_a_non_pdf_upload_does_not_500(self, authenticated_client):
        resp = upload(authenticated_client, b"esto no es un PDF", filename="notas.txt")
        assert resp.status_code == 400
        assert "PDF" in resp.get_data(as_text=True)

    def test_a_valid_pdf_that_is_not_a_pedido_does_not_500(self, authenticated_client):
        resp = upload(authenticated_client, build_pdf_without_table())
        assert resp.status_code == 400
        html = resp.get_data(as_text=True)
        assert "tabla" in html

    def test_submitting_without_choosing_a_file(self, authenticated_client):
        resp = authenticated_client.post(
            "/records/search-pdf", data={}, content_type="multipart/form-data"
        )
        assert resp.status_code == 400
        assert "Elegí un archivo" in resp.get_data(as_text=True)

    def test_an_empty_file_is_rejected_gracefully(self, authenticated_client):
        resp = upload(authenticated_client, b"")
        assert resp.status_code == 400
        assert resp.status_code != 500

    def test_a_pedido_with_no_lines_at_all_does_not_500(self, authenticated_client):
        resp = upload(authenticated_client, build_pedido_pdf(lines=[{"item": "1"}]))
        assert resp.status_code == 400
        assert resp.status_code != 500


class TestConsultaRoleAllowed:
    """Reconciliation guard: this route is the third member of the consult-only
    allowlist (alongside /records and /records/<id>) mentioned in
    app.web.auth's docs -- the 'consulta' role (PTO.V) must be able to reach
    it, unlike every write route."""

    def test_consulta_role_can_search_by_pdf(self, consulta_client):
        resp = upload(consulta_client, build_pedido_pdf())
        assert resp.status_code == 200
