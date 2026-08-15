"""Integration tests for the /ingest/pdf upload-and-review flow.

The contract under test: uploading a pedido PDF NEVER saves anything. It
re-renders the ordinary /ingest form pre-filled, and only the person pressing
"Guardar orden" (POST /ingest, unchanged) commits — so the PDF door reuses the
manual door's validation, duplicate gate and backend rather than bypassing it.
"""

from __future__ import annotations

import io
from urllib.parse import urlparse

from app.core.pdf_extract import parse_pedido_pdf
from app.web.app import _form_from_pedido
from tests.pdf_fixtures import build_pdf_without_table, build_pedido_pdf


def upload(client, data: bytes, filename: str = "pedido.pdf"):
    return client.post(
        "/ingest/pdf",
        data={"pdf": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
    )


def rendered_line_rows(html: str) -> str:
    """Just the <tbody id="lines"> contents.

    Checked instead of the whole page because ingest.html always ships a
    <template id="lineTpl"> containing a `<tr class="line">` for the JS to
    clone — matching on the page as a whole would find that and prove nothing.
    """
    return html.split('<tbody id="lines">')[1].split("</tbody>")[0]


def order_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM production_orders")
        return cur.fetchone()[0]


class TestAuth:
    def test_get_requires_login(self, client):
        resp = client.get("/ingest/pdf")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/login"

    def test_post_requires_login(self, client):
        resp = upload(client, build_pedido_pdf())
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/login"


class TestUploadForm:
    def test_get_shows_the_upload_form(self, authenticated_client):
        """Needs no DB: the upload page has no datalists to populate."""
        resp = authenticated_client.get("/ingest/pdf")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'type="file"' in html
        assert 'name="pdf"' in html
        assert 'enctype="multipart/form-data"' in html

    def test_manual_form_links_to_the_pdf_upload(self, authenticated_client, clean_db):
        resp = authenticated_client.get("/ingest")
        assert resp.status_code == 200
        assert "/ingest/pdf" in resp.get_data(as_text=True)


class TestParsedReview:
    def test_renders_the_normal_ingest_form_prefilled(self, authenticated_client, clean_db):
        resp = upload(authenticated_client, build_pedido_pdf())
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        # It is the real form, posting to the real save route.
        assert "Registrar producción de placas" in html
        assert 'action="/ingest"' in html
        assert "Guardar orden" in html

        # Header parsed from the PDF.
        assert 'value="9001"' in html
        assert 'value="CLIENTE DEMO"' in html
        assert 'value="2026-08-11"' in html
        assert 'value="CIUDAD DEMO"' in html

        # Every plate line is present as a real editable row in the table body.
        rows = rendered_line_rows(html)
        assert rows.count('<tr class="line">') == 4
        for plate in ("ABC12D", "EFG34H", "PUB100", "RNG001", "RNG009"):
            assert plate in rows

    def test_shows_the_production_order_not_the_client_pedido_number(
        self, authenticated_client, clean_db
    ):
        resp = upload(authenticated_client, build_pedido_pdf(order_number="9001", pedido_numero="28"))
        html = resp.get_data(as_text=True)
        assert 'name="order_number" value="9001"' in html

    def test_range_line_is_marked_as_rango(self, authenticated_client, clean_db):
        resp = upload(authenticated_client, build_pedido_pdf())
        html = resp.get_data(as_text=True)
        assert '<option value="rango" selected>' in html

    def test_saves_nothing_until_the_person_presses_guardar(
        self, authenticated_client, db_conn, monkeypatch
    ):
        def boom(*args, **kwargs):
            raise AssertionError("process_rows must not run during the PDF review step")

        monkeypatch.setattr("app.web.app.process_rows", boom)

        resp = upload(authenticated_client, build_pedido_pdf())
        assert resp.status_code == 200
        assert order_count(db_conn) == 0


class TestNotices:
    def test_several_cities_leaves_the_field_blank_and_says_why(
        self, authenticated_client, clean_db
    ):
        """production_orders stores ONE city (plates have no city column), so a
        multi-city pedido can't be auto-filled — it must be flagged, not guessed."""
        resp = upload(authenticated_client, build_pedido_pdf(lines=[
            {"item": "1", "placa": "AAA111", "ciudad": "PALMIRA", "tipo": "PARTICULAR"},
            {"item": "2", "placa": "BBB222", "ciudad": "CALI", "tipo": "PARTICULAR"},
        ]))
        html = resp.get_data(as_text=True)
        assert "varias ciudades" in html
        assert "PALMIRA, CALI" in html
        assert 'name="city" list="cities" value=""' in html

    def test_unmarked_plate_type_is_flagged_and_left_unselected(
        self, authenticated_client, clean_db
    ):
        resp = upload(authenticated_client, build_pedido_pdf(lines=[
            {"item": "1", "placa": "AAA111"},
        ]))
        html = resp.get_data(as_text=True)
        assert "no marca el tipo de placa" in html
        assert "— elegí el tipo —" in html

    def test_unreadable_date_is_flagged_but_the_lines_survive(
        self, authenticated_client, clean_db
    ):
        resp = upload(authenticated_client, build_pedido_pdf(order_date="POR CONFIRMAR"))
        html = resp.get_data(as_text=True)
        assert "No se pudo leer la fecha" in html
        assert "ABC12D" in html

    def test_clean_pedido_shows_no_notices(self, authenticated_client, clean_db):
        resp = upload(authenticated_client, build_pedido_pdf())
        assert "Revisá esto antes de guardar" not in resp.get_data(as_text=True)


class TestGracefulFailure:
    def test_a_non_pdf_upload_does_not_500(self, authenticated_client):
        resp = upload(authenticated_client, b"esto no es un PDF", filename="notas.txt")
        assert resp.status_code == 400
        assert "PDF" in resp.get_data(as_text=True)

    def test_a_valid_pdf_that_is_not_a_pedido_explains_itself(self, authenticated_client):
        resp = upload(authenticated_client, build_pdf_without_table())
        assert resp.status_code == 400
        html = resp.get_data(as_text=True)
        assert "tabla" in html
        assert "manualmente" in html  # points at the fallback

    def test_a_pedido_with_no_plates_is_rejected_clearly(self, authenticated_client):
        resp = upload(authenticated_client, build_pedido_pdf(lines=[{"item": "1"}]))
        assert resp.status_code == 400
        assert "ninguna placa" in resp.get_data(as_text=True)

    def test_submitting_without_choosing_a_file(self, authenticated_client):
        resp = authenticated_client.post(
            "/ingest/pdf", data={}, content_type="multipart/form-data"
        )
        assert resp.status_code == 400
        assert "Elegí un archivo" in resp.get_data(as_text=True)

    def test_an_empty_file_is_rejected_gracefully(self, authenticated_client):
        resp = upload(authenticated_client, b"")
        assert resp.status_code == 400
        assert resp.status_code != 500


class TestSavingWhatThePdfProduced:
    """The payoff: the shapes /ingest/pdf renders must be exactly what the
    UNCHANGED manual save path already accepts."""

    def test_parsed_lines_save_through_the_ordinary_ingest_post(
        self, authenticated_client, db_conn
    ):
        header, lines, _ = _form_from_pedido(parse_pedido_pdf(build_pedido_pdf()))

        resp = authenticated_client.post("/ingest", data={
            "order_date": header["order_date"],
            "order_number": header["order_number"],
            "requested_by": header["requested_by"],
            "city": header["city"],
            "line_type": [line["line_type"] for line in lines],
            "plate_type": [line["plate_type"] for line in lines],
            "plate_number": [line["plate_number"] or "" for line in lines],
            "plate_to": [line["plate_to"] or "" for line in lines],
            "quantity": [str(line["quantity"]) for line in lines],
        })
        assert resp.status_code == 200
        assert "Registrado" in resp.get_data(as_text=True)

        with db_conn.cursor() as cur:
            cur.execute("SELECT order_number, requested_by, city FROM production_orders")
            assert cur.fetchall() == [("9001", "CLIENTE DEMO", "CIUDAD DEMO")]
            cur.execute("SELECT plate_number, plate_to, plate_type, quantity FROM plates ORDER BY id")
            assert cur.fetchall() == [
                ("ABC12D", None, "MOTO PARTICULAR", 1),
                ("EFG34H", None, "MOTO PARTICULAR", 1),
                ("PUB100", None, "CARRO PÚBLICO", 2),
                ("RNG001", "RNG009", "CARRO PARTICULAR", 9),
            ]


class TestManualFlowUnchanged:
    def test_plain_get_ingest_has_no_prefilled_rows_or_notices(
        self, authenticated_client, clean_db
    ):
        """Regression guard: the PDF work added `notices` and a blank type
        option, neither of which may leak into ordinary manual entry."""
        resp = authenticated_client.get("/ingest")
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert "<tr" not in rendered_line_rows(html)  # empty; JS adds the first row
        assert "Revisá esto antes de guardar" not in html
        assert "— elegí el tipo —" not in html
