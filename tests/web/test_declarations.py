"""Integration tests for the internal /declaraciones review screen.

Contract under test:
- list/detail/file download are @pos_allowed (admin + point_of_sale); approve
  and reject are admin-only; the read-only 'production' role gets 403 on
  everything (it handles ID data); anonymous requests redirect to /login.
- the detail page explains each flag with the real context staff need.
- files are served only through a stored name resolved by
  app.web.uploads.resolve_upload_path, with safe headers.

Pending declarations are built through the real
app.core.declarations.submit_declaration so the conflicts are real. No test
here expects a DB error, so no rollback gymnastics are needed (see
tests/conftest.py's clean_db docstring).
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date

import pytest

from app.core import db
from app.core import declarations as decl
from tests.core.test_declarations import (
    TODAY,
    _fabricate,
    _gestor_input,
    _input,
    _owner,
    _vehicle,
)

LIST_URL = "/declaraciones"
PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"
PDF = b"%PDF-1.4 fake pdf body"


def _detail(declaration_id: int) -> str:
    return f"/declaraciones/{declaration_id}"


def _submit(db_conn, inp=None) -> int:
    result = decl.submit_declaration(db_conn, inp or _input(), today=TODAY)
    return result.declaration_id


def _pending_recent(db_conn) -> int:
    _fabricate(db_conn, "ABC123", date(2026, 9, 1))
    return _submit(db_conn)


def _create_card(db_conn, owner_name: str, *, license_: str = "LIC-001", plate: str = "ABC123") -> int:
    owner_id = db.resolve_or_create_customer(db_conn, owner_name, type="CLIENTE")
    return db.create_ownership_card(db_conn, owner_id, transit_license=license_, plate_number=plate)


def _pending_other_owner(db_conn) -> int:
    _create_card(db_conn, "OTRO DUENO")
    return _submit(db_conn)


def _status(db_conn, declaration_id: int) -> str:
    record = db.get_declaration(db_conn, declaration_id)
    assert record is not None
    return record.status


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    return tmp_path


def _store(uploads, content: bytes, ext: str) -> str:
    name = f"{uuid.uuid4().hex}.{ext}"
    (uploads / name).write_bytes(content)
    return name


def _with_files(db_conn, uploads) -> tuple[int, dict[str, str]]:
    names = {
        "owner_signature": _store(uploads, PNG, "png"),
        "gestor_signature": _store(uploads, PNG, "png"),
        "notarized_pdf": _store(uploads, PDF, "pdf"),
    }
    inp = replace(
        _gestor_input(reason="PÉRDIDA"), owner_signature_file=names["owner_signature"],
        gestor_signature_file=names["gestor_signature"], notarized_pdf_file=names["notarized_pdf"],
    )
    return _submit(db_conn, inp), names


class TestList:
    def test_default_shows_pending_first_and_hides_nothing_in_pending_tab(self, authenticated_client, db_conn):
        approved_id = _submit(db_conn, _input(owner=_owner(name="ANA APROBADA"),
                                              vehicle=_vehicle(plate_number="AAA111", transit_license="L-A")))
        pending_id = _pending_recent(db_conn)
        html = authenticated_client.get(LIST_URL).data.decode()
        assert "MARIA LOPEZ" in html
        assert "ANA APROBADA" not in html          # default tab = Pendientes
        assert "Placa reciente" in html
        assert _status(db_conn, approved_id) == "approved"
        assert _status(db_conn, pending_id) == "pending_review"

    def test_all_tab_puts_pending_before_approved(self, authenticated_client, db_conn):
        _submit(db_conn, _input(owner=_owner(name="ANA APROBADA"),
                                vehicle=_vehicle(plate_number="AAA111", transit_license="L-A")))
        _pending_recent(db_conn)
        html = authenticated_client.get(f"{LIST_URL}?status=all").data.decode()
        assert html.index("MARIA LOPEZ") < html.index("ANA APROBADA")

    def test_status_filters(self, authenticated_client, db_conn):
        _submit(db_conn, _input(owner=_owner(name="ANA APROBADA"),
                                vehicle=_vehicle(plate_number="AAA111", transit_license="L-A")))
        pending_id = _pending_recent(db_conn)
        decl.reject_declaration(db_conn, pending_id, 1, "no")
        approved = authenticated_client.get(f"{LIST_URL}?status=approved").data.decode()
        rejected = authenticated_client.get(f"{LIST_URL}?status=rejected").data.decode()
        assert "ANA APROBADA" in approved and "MARIA LOPEZ" not in approved
        assert "MARIA LOPEZ" in rejected and "ANA APROBADA" not in rejected

    def test_unknown_status_falls_back_to_pending(self, authenticated_client, db_conn):
        _pending_recent(db_conn)
        assert authenticated_client.get(f"{LIST_URL}?status=bogus").status_code == 200

    @pytest.mark.parametrize("term", ["MARIA", "1020304050", "ABC123", "LIC-001"])
    def test_search(self, authenticated_client, db_conn, term):
        _pending_recent(db_conn)
        html = authenticated_client.get(f"{LIST_URL}?status=all&q={term}").data.decode()
        assert "MARIA LOPEZ" in html

    def test_search_without_match(self, authenticated_client, db_conn):
        _pending_recent(db_conn)
        html = authenticated_client.get(f"{LIST_URL}?q=NOEXISTE").data.decode()
        assert "No se encontró ninguna declaración" in html

    def test_pending_count_is_visible(self, authenticated_client, db_conn):
        _pending_recent(db_conn)
        html = authenticated_client.get(LIST_URL).data.decode()
        assert "Pendientes (1)" in html


class TestDetail:
    def test_shows_declared_data(self, authenticated_client, db_conn):
        declaration_id = _pending_recent(db_conn)
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        for text in ("MARIA LOPEZ", "1020304050", "ABC123", "LIC-001", "MAZDA", "DETERIORO", "CRA 1 # 2-3"):
            assert text in html

    def test_unknown_id_redirects_to_list(self, authenticated_client, db_conn):
        resp = authenticated_client.get(_detail(9999))
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(LIST_URL)

    def test_plate_made_recently_explains_dates(self, authenticated_client, db_conn):
        html = authenticated_client.get(_detail(_pending_recent(db_conn))).data.decode()
        assert "menos de 6 meses" in html
        assert "01/09/2026" in html
        assert "01/03/2027" in html

    def test_card_other_owner_shows_existing_owner(self, authenticated_client, db_conn):
        declaration_id = _pending_other_owner(db_conn)
        record = db.get_declaration(db_conn, declaration_id)
        assert record is not None and record.review_flags == [decl.CARD_OTHER_OWNER]
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert "a nombre de otro cliente" in html
        assert "OTRO DUENO" in html
        assert "LIC-001" in html

    def test_card_data_mismatch_shows_both_cards(self, authenticated_client, db_conn):
        _create_card(db_conn, "DUENO UNO", license_="LIC-001", plate="XYZ999")
        _create_card(db_conn, "DUENO DOS", license_="LIC-002", plate="ABC123")
        declaration_id = _submit(db_conn)
        record = db.get_declaration(db_conn, declaration_id)
        assert record is not None and decl.CARD_DATA_MISMATCH in record.review_flags
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert "no cuadran" in html
        assert "DUENO UNO" in html and "XYZ999" in html
        assert "DUENO DOS" in html and "LIC-002" in html

    def test_customer_type_mismatch_shows_existing_type(self, authenticated_client, db_conn):
        db.resolve_or_create_customer(db_conn, "MARIA LOPEZ", type="EMPRESA")
        declaration_id = _submit(db_conn)
        record = db.get_declaration(db_conn, declaration_id)
        assert record is not None and record.review_flags == [decl.CUSTOMER_TYPE_MISMATCH]
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert "tipo distinto" in html
        assert "EMPRESA" in html and "se esperaba CLIENTE" in html

    def test_customer_document_mismatch_shows_both_documents(self, authenticated_client, db_conn):
        db.resolve_or_create_customer(db_conn, "MARIA LOPEZ", type="CLIENTE", document_number="777888999")
        declaration_id = _submit(db_conn)
        record = db.get_declaration(db_conn, declaration_id)
        assert record is not None and record.review_flags == [decl.CUSTOMER_DOCUMENT_MISMATCH]
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert "otro documento" in html
        assert "777888999" in html and "1020304050" in html

    def test_approved_detail_shows_linked_records_and_no_review_forms(self, authenticated_client, db_conn):
        declaration_id = _submit(db_conn)
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert "Resultado de la revisión" in html
        assert "Tarjeta de propiedad" in html
        assert "/approve" not in html and "/reject" not in html

    def test_admin_sees_review_forms_for_pending(self, authenticated_client, db_conn):
        declaration_id = _pending_recent(db_conn)
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert f"/declaraciones/{declaration_id}/approve" in html
        assert f"/declaraciones/{declaration_id}/reject" in html

    def test_point_of_sale_sees_no_review_forms(self, punto_venta_client, db_conn):
        declaration_id = _pending_recent(db_conn)
        html = punto_venta_client.get(_detail(declaration_id)).data.decode()
        assert "/approve" not in html and "/reject" not in html
        assert "menos de 6 meses" in html


class TestFiles:
    @pytest.mark.parametrize("kind,mimetype,body", [
        ("owner_signature", "image/png", PNG),
        ("gestor_signature", "image/png", PNG),
        ("notarized_pdf", "application/pdf", PDF),
    ])
    def test_serves_each_kind(self, authenticated_client, db_conn, uploads, kind, mimetype, body):
        declaration_id, _ = _with_files(db_conn, uploads)
        resp = authenticated_client.get(f"{_detail(declaration_id)}/file/{kind}")
        assert resp.status_code == 200
        assert resp.mimetype == mimetype
        assert resp.data == body
        assert resp.headers["Content-Disposition"].startswith("inline")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["Cache-Control"] == "no-store"

    def test_point_of_sale_can_download(self, punto_venta_client, db_conn, uploads):
        declaration_id, _ = _with_files(db_conn, uploads)
        resp = punto_venta_client.get(f"{_detail(declaration_id)}/file/owner_signature")
        assert resp.status_code == 200

    def test_detail_links_to_the_files(self, authenticated_client, db_conn, uploads):
        declaration_id, names = _with_files(db_conn, uploads)
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert f"/declaraciones/{declaration_id}/file/notarized_pdf" in html
        assert all(name not in html for name in names.values())   # stored names never exposed

    def test_missing_file_on_disk_is_404(self, authenticated_client, db_conn, uploads):
        declaration_id, names = _with_files(db_conn, uploads)
        (uploads / names["owner_signature"]).unlink()
        resp = authenticated_client.get(f"{_detail(declaration_id)}/file/owner_signature")
        assert resp.status_code == 404

    def test_no_path_stored_is_404(self, authenticated_client, db_conn, uploads):
        declaration_id = _submit(db_conn, _input(owner_signature_file=None))
        resp = authenticated_client.get(f"{_detail(declaration_id)}/file/notarized_pdf")
        assert resp.status_code == 404

    def test_unknown_kind_is_404(self, authenticated_client, db_conn, uploads):
        declaration_id, _ = _with_files(db_conn, uploads)
        assert authenticated_client.get(f"{_detail(declaration_id)}/file/passwd").status_code == 404

    def test_unknown_declaration_is_404(self, authenticated_client, db_conn, uploads):
        assert authenticated_client.get(f"{_detail(9999)}/file/owner_signature").status_code == 404

    def test_a_traversal_looking_stored_name_is_never_served(self, authenticated_client, db_conn, uploads, tmp_path):
        """Even if a bad value somehow reached the DB, resolve_upload_path
        only accepts names we generate, so nothing outside the dir is read."""
        (uploads.parent / "secret.png").write_bytes(PNG)
        declaration_id = _submit(db_conn, _input(owner_signature_file="../secret.png"))
        resp = authenticated_client.get(f"{_detail(declaration_id)}/file/owner_signature")
        assert resp.status_code == 404

    def test_the_url_cannot_carry_a_file_name(self, authenticated_client, db_conn, uploads):
        declaration_id, names = _with_files(db_conn, uploads)
        resp = authenticated_client.get(f"{_detail(declaration_id)}/file/{names['owner_signature']}")
        assert resp.status_code == 404
        resp = authenticated_client.get(f"{_detail(declaration_id)}/file/..%2f..%2fetc%2fpasswd")
        assert resp.status_code == 404


class TestApprove:
    def test_plain_approve_creates_customer_and_card(self, authenticated_client, db_conn):
        declaration_id = _pending_recent(db_conn)
        resp = authenticated_client.post(f"{_detail(declaration_id)}/approve", data={"note": "ok, confirmado"})
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(_detail(declaration_id))
        record = db.get_declaration(db_conn, declaration_id)
        assert record is not None
        assert record.status == "approved"
        assert record.review_note == "ok, confirmado"
        assert record.reviewed_by is not None
        assert record.customer_id is not None and record.ownership_card_id is not None

    def test_approve_with_reassign_moves_the_card(self, authenticated_client, db_conn):
        declaration_id = _pending_other_owner(db_conn)
        resp = authenticated_client.post(
            f"{_detail(declaration_id)}/approve", data={"reassign_card": "on"},
        )
        assert resp.status_code == 302
        record = db.get_declaration(db_conn, declaration_id)
        assert record is not None and record.status == "approved"
        card = db.get_ownership_card(db_conn, record.ownership_card_id)
        assert card is not None and card.customer_id == record.customer_id

    def test_value_error_returns_400_and_changes_nothing(self, authenticated_client, db_conn):
        declaration_id = _pending_other_owner(db_conn)
        before = db.get_ownership_card_by_transit_license(db_conn, "LIC-001")
        resp = authenticated_client.post(f"{_detail(declaration_id)}/approve", data={})
        assert resp.status_code == 400
        assert "ya existe a nombre de otro cliente" in resp.data.decode()
        assert _status(db_conn, declaration_id) == "pending_review"
        assert db.get_ownership_card_by_transit_license(db_conn, "LIC-001") == before
        assert db.get_customer_by_name(db_conn, "MARIA LOPEZ") is None

    def test_approving_an_already_reviewed_declaration_is_400(self, authenticated_client, db_conn):
        declaration_id = _pending_recent(db_conn)
        authenticated_client.post(f"{_detail(declaration_id)}/approve", data={})
        resp = authenticated_client.post(f"{_detail(declaration_id)}/approve", data={})
        assert resp.status_code == 400
        assert "pendiente de revisión" in resp.data.decode()

    def test_unknown_id_redirects_to_list(self, authenticated_client, db_conn):
        resp = authenticated_client.post(f"{_detail(9999)}/approve", data={})
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(LIST_URL)

    def test_logs_one_info_line(self, authenticated_client, db_conn, caplog):
        declaration_id = _pending_recent(db_conn)
        with caplog.at_level("INFO"):
            authenticated_client.post(f"{_detail(declaration_id)}/approve", data={})
        lines = [r.getMessage() for r in caplog.records if "declaration review" in r.getMessage()]
        assert len(lines) == 1
        assert f"declaration_id={declaration_id}" in lines[0] and "decision=approve" in lines[0]


class TestReject:
    def test_reject_with_note(self, authenticated_client, db_conn):
        declaration_id = _pending_recent(db_conn)
        resp = authenticated_client.post(f"{_detail(declaration_id)}/reject", data={"note": "datos falsos"})
        assert resp.status_code == 302
        record = db.get_declaration(db_conn, declaration_id)
        assert record is not None
        assert record.status == "rejected" and record.review_note == "datos falsos"
        assert record.customer_id is None and record.ownership_card_id is None
        assert db.get_customer_by_name(db_conn, "MARIA LOPEZ") is None

    def test_note_is_required(self, authenticated_client, db_conn):
        declaration_id = _pending_recent(db_conn)
        resp = authenticated_client.post(f"{_detail(declaration_id)}/reject", data={"note": "   "})
        assert resp.status_code == 400
        assert "Escriba una nota" in resp.data.decode()
        assert _status(db_conn, declaration_id) == "pending_review"


class TestRoles:
    @pytest.fixture
    def declaration_id(self, db_conn, uploads) -> int:
        return _with_files(db_conn, uploads)[0]

    def test_production_gets_403_on_everything(self, produccion_client, declaration_id):
        base = _detail(declaration_id)
        assert produccion_client.get(LIST_URL).status_code == 403
        assert produccion_client.get(base).status_code == 403
        assert produccion_client.get(f"{base}/file/owner_signature").status_code == 403
        assert produccion_client.post(f"{base}/approve", data={}).status_code == 403
        assert produccion_client.post(f"{base}/reject", data={"note": "x"}).status_code == 403

    def test_point_of_sale_reads_but_cannot_decide(self, punto_venta_client, db_conn, declaration_id):
        base = _detail(declaration_id)
        assert punto_venta_client.get(LIST_URL).status_code == 200
        assert punto_venta_client.get(base).status_code == 200
        assert punto_venta_client.get(f"{base}/file/owner_signature").status_code == 200
        assert punto_venta_client.post(f"{base}/approve", data={}).status_code == 403
        assert punto_venta_client.post(f"{base}/reject", data={"note": "x"}).status_code == 403
        assert db.get_declaration(db_conn, declaration_id).status == "approved"  # type: ignore[union-attr]

    def test_point_of_sale_cannot_decide_on_a_pending_one(self, punto_venta_client, db_conn):
        pending_id = _pending_recent(db_conn)
        assert punto_venta_client.post(f"{_detail(pending_id)}/approve", data={}).status_code == 403
        assert _status(db_conn, pending_id) == "pending_review"

    def test_admin_can_do_everything(self, authenticated_client, declaration_id):
        base = _detail(declaration_id)
        assert authenticated_client.get(LIST_URL).status_code == 200
        assert authenticated_client.get(base).status_code == 200
        assert authenticated_client.get(f"{base}/file/owner_signature").status_code == 200

    @pytest.mark.parametrize("method,url", [
        ("get", LIST_URL), ("get", "/declaraciones/1"), ("get", "/declaraciones/1/file/owner_signature"),
        ("post", "/declaraciones/1/approve"), ("post", "/declaraciones/1/reject"),
    ])
    def test_anonymous_redirects_to_login(self, client, clean_db, method, url):
        resp = getattr(client, method)(url)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
