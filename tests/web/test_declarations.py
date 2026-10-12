"""Integration tests for the internal /declaraciones review screen.

A declaration is ONE declarant plus 1..N ownership cards; a flag on any card
holds the whole declaration. Contract under test:
- list/detail/file download are @pos_allowed (admin + point_of_sale); approve
  and reject are admin-only; the read-only 'production' role gets 403 on
  everything (it handles ID data); anonymous requests redirect to /login.
- the list shows one row per declaration (declarant, card count, total
  plates, compact summary) and the search finds any card's data.
- the detail page has one section per card and explains each card's flags
  with the real context staff need; approval is all-or-nothing and reassigning
  a card is chosen per card (`reassign_<declaration_card_id>`).
- files are served only through a stored name resolved by
  app.web.uploads.resolve_upload_path, with safe headers.

Pending declarations are built through the real
app.core.declarations.submit_declaration so the conflicts are real. No test
here expects a DB error, so no rollback gymnastics are needed (see
tests/conftest.py's clean_db docstring).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import replace
from datetime import date

import pytest

from app.core import db
from app.core import declarations as decl
from tests.core.test_declarations import (
    TODAY,
    _card,
    _cards_of,
    _count,
    _fabricate,
    _gcard,
    _gestor_input,
    _input,
    _owner,
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


def _luis():
    return _owner(name="LUIS PEREZ", document_number="777")


def _three_cards_one_flagged(db_conn) -> int:
    """A GESTOR declaration with 3 cards (4 plates); only card 2 (DEF456) is
    held: it already exists under another owner."""
    _create_card(db_conn, "OTRO DUENO", license_="L2", plate="DEF456")
    cards = [
        _gcard("ABC123", "L1", plate_quantity=1),
        _gcard("DEF456", "L2", owner=_luis(), relation="POSEEDOR", reason="PÉRDIDA", plate_quantity=2),
        _gcard("GHI789", "L3", plate_quantity=1),
    ]
    declaration_id = _submit(db_conn, _gestor_input(cards=cards, notarized_pdf_file="n.pdf"))
    record = db.get_declaration(db_conn, declaration_id)
    assert record is not None and record.status == "pending_review"
    return declaration_id


def _status(db_conn, declaration_id: int) -> str:
    record = db.get_declaration(db_conn, declaration_id)
    assert record is not None
    return record.status


def _section(html: str, number: int) -> str:
    """The HTML of one card's fieldset (card N), up to the next section."""
    start = html.index(f'id="card-{number}"')
    end = html.find('id="card-', start + 1)
    return html[start:end if end != -1 else len(html)]


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
        "signature": _store(uploads, PNG, "png"),
        "notarized_pdf": _store(uploads, PDF, "pdf"),
    }
    inp = replace(
        _gestor_input(cards=[_gcard(reason="PÉRDIDA")]),
        signature_file=names["signature"], notarized_pdf_file=names["notarized_pdf"],
    )
    return _submit(db_conn, inp), names


class TestList:
    def test_default_shows_pending_first_and_hides_nothing_in_pending_tab(self, authenticated_client, db_conn):
        approved_id = _submit(db_conn, _input(declarant=_owner(name="ANA APROBADA"),
                                              cards=[_card(plate_number="AAA111", transit_license="L-A")]))
        pending_id = _pending_recent(db_conn)
        html = authenticated_client.get(LIST_URL).data.decode()
        assert "MARIA LOPEZ" in html
        assert "ANA APROBADA" not in html          # default tab = Pendientes
        assert "Placa reciente" in html
        assert _status(db_conn, approved_id) == "approved"
        assert _status(db_conn, pending_id) == "pending_review"

    def test_all_tab_puts_pending_before_approved(self, authenticated_client, db_conn):
        _submit(db_conn, _input(declarant=_owner(name="ANA APROBADA"),
                                cards=[_card(plate_number="AAA111", transit_license="L-A")]))
        _pending_recent(db_conn)
        html = authenticated_client.get(f"{LIST_URL}?status=all").data.decode()
        assert html.index("MARIA LOPEZ") < html.index("ANA APROBADA")

    def test_status_filters(self, authenticated_client, db_conn):
        _submit(db_conn, _input(declarant=_owner(name="ANA APROBADA"),
                                cards=[_card(plate_number="AAA111", transit_license="L-A")]))
        pending_id = _pending_recent(db_conn)
        decl.reject_declaration(db_conn, pending_id, 1, "no")
        approved = authenticated_client.get(f"{LIST_URL}?status=approved").data.decode()
        rejected = authenticated_client.get(f"{LIST_URL}?status=rejected").data.decode()
        assert "ANA APROBADA" in approved and "MARIA LOPEZ" not in approved
        assert "MARIA LOPEZ" in rejected and "ANA APROBADA" not in rejected

    def test_unknown_status_falls_back_to_pending(self, authenticated_client, db_conn):
        _pending_recent(db_conn)
        assert authenticated_client.get(f"{LIST_URL}?status=bogus").status_code == 200

    def test_one_row_per_declaration_with_counts_and_summary(self, authenticated_client, db_conn):
        _three_cards_one_flagged(db_conn)
        html = authenticated_client.get(LIST_URL).data.decode()
        assert html.count("<tr>") == 2                      # header + ONE declaration row
        row = html[html.index('data-label="Declarante"'):]
        assert "GESTORIA SUR" in row and "GESTOR" in row
        assert re.search(r'data-label="Tarjetas">3<', html)
        assert re.search(r'data-label="Placas">4<', html)   # 1 + 2 + 1, not 3
        assert "ABC123 · DEF456 +1" in html
        assert "DETERIORO 2 · PÉRDIDA 1" in html            # mixed reasons are spelled out
        assert "Tarjeta de otro dueño" in html
        assert "en la tarjeta 2" in html                    # which card holds it

    def test_single_card_summary_has_no_plus(self, authenticated_client, db_conn):
        _pending_recent(db_conn)
        html = authenticated_client.get(LIST_URL).data.decode()
        assert re.search(r'data-label="Tarjetas">1<', html)
        assert "ABC123<" in html and "+" not in html[html.index("<tbody>"):]
        assert "DETERIORO" in html

    @pytest.mark.parametrize("term", [
        "MARIA", "1020304050", "ABC123", "LIC-001",          # declarant / its cards
    ])
    def test_search_single_card(self, authenticated_client, db_conn, term):
        _pending_recent(db_conn)
        html = authenticated_client.get(f"{LIST_URL}?status=all&q={term}").data.decode()
        assert "MARIA LOPEZ" in html

    @pytest.mark.parametrize("term", [
        "GESTORIA", "900123456",            # the declarant
        "DEF456", "L3",                      # a non-first card's plate / licence
        "LUIS PEREZ", "777",                 # a card's own owner
    ])
    def test_search_finds_the_declaration_through_any_card(self, authenticated_client, db_conn, term):
        _three_cards_one_flagged(db_conn)
        html = authenticated_client.get(f"{LIST_URL}?status=all&q={term}").data.decode()
        assert "GESTORIA SUR" in html

    def test_search_without_match(self, authenticated_client, db_conn):
        _pending_recent(db_conn)
        html = authenticated_client.get(f"{LIST_URL}?q=NOEXISTE").data.decode()
        assert "No se encontró ninguna declaración" in html

    def test_pending_count_is_visible(self, authenticated_client, db_conn):
        _pending_recent(db_conn)
        html = authenticated_client.get(LIST_URL).data.decode()
        assert "Pendientes (1)" in html

    def test_list_loads_cards_with_one_query_for_all_rows(self, authenticated_client, db_conn, monkeypatch):
        _pending_recent(db_conn)
        _three_cards_one_flagged(db_conn)
        calls: list[int] = []
        real = db.list_cards_for_declarations

        def spy(conn, ids):
            calls.append(len(list(ids)))
            return real(conn, ids)

        monkeypatch.setattr(db, "list_cards_for_declarations", spy)
        authenticated_client.get(f"{LIST_URL}?status=all")
        assert calls == [2]


class TestDetail:
    def test_shows_declared_data(self, authenticated_client, db_conn):
        declaration_id = _pending_recent(db_conn)
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        for text in ("MARIA LOPEZ", "1020304050", "ABC123", "LIC-001", "MAZDA", "DETERIORO", "CRA 1 # 2-3",
                     "Tarjeta de propiedad 1 de 1"):
            assert text in html
        assert "Vehículo (tarjeta" not in html          # never "vehicle" as the entity

    def test_unknown_id_redirects_to_list(self, authenticated_client, db_conn):
        resp = authenticated_client.get(_detail(9999))
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(LIST_URL)

    def test_one_section_per_card_with_its_own_data(self, authenticated_client, db_conn):
        html = authenticated_client.get(_detail(_three_cards_one_flagged(db_conn))).data.decode()
        assert html.count('id="card-') == 3
        first, second, third = (_section(html, n) for n in (1, 2, 3))
        assert "ABC123" in first and "L1" in first and "DEF456" not in first
        assert "DEF456" in second and "PÉRDIDA" in second and "POSEEDOR" in second
        assert "LUIS PEREZ" in second and "LUIS PEREZ" not in first
        assert "GHI789" in third and "DETERIORO" in third
        assert "Total de placas" in html and re.search(r"Total de placas</dt><dd>4<", html)
        assert "GESTORIA SUR" in html and "900123456" in html           # declarant block

    def test_only_the_held_card_is_marked_and_explained(self, authenticated_client, db_conn):
        html = authenticated_client.get(_detail(_three_cards_one_flagged(db_conn))).data.decode()
        assert "Retenida por 1 de 3 tarjetas" in html
        assert 'href="#card-2"' in html and 'href="#card-1"' not in html
        first, second, third = (_section(html, n) for n in (1, 2, 3))
        assert "a nombre de otro cliente" in second and "OTRO DUENO" in second
        assert "Tarjeta de otro dueño" in second
        assert "alert" not in first and "alert" not in third
        assert "flagged" in second and "flagged" not in first

    def test_flag_context_only_inside_the_flagged_card(self, authenticated_client, db_conn):
        _fabricate(db_conn, "GHI789", date(2026, 9, 1))
        _create_card(db_conn, "OTRO DUENO", license_="L2", plate="DEF456")
        cards = [_gcard("ABC123", "L1"), _gcard("DEF456", "L2"), _gcard("GHI789", "L3")]
        declaration_id = _submit(db_conn, _gestor_input(cards=cards))
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert "Retenida por 2 de 3 tarjetas" in html
        first, second, third = (_section(html, n) for n in (1, 2, 3))
        assert "menos de 6 meses" in third and "01/03/2027" in third
        assert "menos de 6 meses" not in second and "OTRO DUENO" in second
        assert "OTRO DUENO" not in third
        assert "menos de 6 meses" not in first and "OTRO DUENO" not in first

    def test_plate_made_recently_explains_dates(self, authenticated_client, db_conn):
        html = authenticated_client.get(_detail(_pending_recent(db_conn))).data.decode()
        assert "menos de 6 meses" in html
        assert "01/09/2026" in html
        assert "01/03/2027" in html

    def test_card_other_owner_shows_existing_owner(self, authenticated_client, db_conn):
        _create_card(db_conn, "OTRO DUENO")
        declaration_id = _submit(db_conn)
        (row,) = _cards_of(db_conn, declaration_id)
        assert row.review_flags == [decl.CARD_OTHER_OWNER]
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert "a nombre de otro cliente" in html
        assert "OTRO DUENO" in html
        assert "LIC-001" in html

    def test_card_data_mismatch_shows_both_cards(self, authenticated_client, db_conn):
        _create_card(db_conn, "DUENO UNO", license_="LIC-001", plate="XYZ999")
        _create_card(db_conn, "DUENO DOS", license_="LIC-002", plate="ABC123")
        declaration_id = _submit(db_conn)
        (row,) = _cards_of(db_conn, declaration_id)
        assert decl.CARD_DATA_MISMATCH in row.review_flags
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert "no cuadran" in html
        assert "DUENO UNO" in html and "XYZ999" in html
        assert "DUENO DOS" in html and "LIC-002" in html

    def test_customer_type_mismatch_shows_existing_type(self, authenticated_client, db_conn):
        db.resolve_or_create_customer(db_conn, "MARIA LOPEZ", type="EMPRESA")
        declaration_id = _submit(db_conn)
        (row,) = _cards_of(db_conn, declaration_id)
        assert row.review_flags == [decl.CUSTOMER_TYPE_MISMATCH]
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert "tipo distinto" in html
        assert "EMPRESA" in html and "se esperaba CLIENTE" in html

    def test_gestor_type_mismatch_is_labelled_on_every_card(self, authenticated_client, db_conn):
        db.resolve_or_create_customer(db_conn, "GESTORIA SUR", type="CLIENTE")
        cards = [_gcard("ABC123", "L1"), _gcard("DEF456", "L2", owner=_luis())]
        declaration_id = _submit(db_conn, _gestor_input(cards=cards))
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        for number in (1, 2):
            assert "Gestor &#39;GESTORIA SUR&#39; ya existe con tipo CLIENTE; se esperaba GESTOR" in _section(html, number)

    def test_same_name_twice_in_the_submission_is_explained(self, authenticated_client, db_conn):
        cards = [_gcard("ABC123", "L1"), _gcard("DEF456", "L2", owner=_owner(document_number="999"))]
        declaration_id = _submit(db_conn, _gestor_input(cards=cards))
        rows = _cards_of(db_conn, declaration_id)
        assert [r.review_flags for r in rows] == [[], [decl.CUSTOMER_DOCUMENT_MISMATCH]]
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert "En la tarjeta 1 el mismo nombre" in _section(html, 2)
        assert "1020304050" in _section(html, 2)
        assert "Retenida por 1 de 2 tarjetas" in html

    def test_customer_document_mismatch_shows_both_documents(self, authenticated_client, db_conn):
        db.resolve_or_create_customer(db_conn, "MARIA LOPEZ", type="CLIENTE", document_number="777888999")
        declaration_id = _submit(db_conn)
        (row,) = _cards_of(db_conn, declaration_id)
        assert row.review_flags == [decl.CUSTOMER_DOCUMENT_MISMATCH]
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert "otro documento" in html
        assert "777888999" in html and "1020304050" in html

    def test_approved_detail_shows_linked_records_per_card_and_no_review_forms(
        self, authenticated_client, db_conn,
    ):
        cards = [_gcard("ABC123", "L1"), _gcard("DEF456", "L2", owner=_luis())]
        declaration_id = _submit(db_conn, _gestor_input(cards=cards))
        rows = _cards_of(db_conn, declaration_id)
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert "Resultado de la revisión" in html
        assert "Retenida" not in html
        for number, row in enumerate(rows, start=1):
            section = _section(html, number)
            assert f"/customers/{row.customer_id}" in section
            assert f"placa {row.plate_number}" in section
        record = db.get_declaration(db_conn, declaration_id)
        assert record is not None and record.gestor_id is not None
        assert f"/customers/{record.gestor_id}" in html
        assert "/approve" not in html and "/reject" not in html

    def test_admin_sees_review_forms_for_pending(self, authenticated_client, db_conn):
        declaration_id = _pending_recent(db_conn)
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert f"/declaraciones/{declaration_id}/approve" in html
        assert f"/declaraciones/{declaration_id}/reject" in html

    def test_reassign_checkbox_only_on_cards_with_card_other_owner(self, authenticated_client, db_conn):
        declaration_id = _three_cards_one_flagged(db_conn)
        rows = _cards_of(db_conn, declaration_id)
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert f'name="reassign_{rows[1].id}"' in html
        assert 'form="approve-form"' in html and 'id="approve-form"' in html
        assert html.count('name="reassign_') == 1
        assert "Reasignar esta tarjeta" in _section(html, 2)

    def test_no_reassign_checkbox_without_that_flag(self, authenticated_client, db_conn):
        html = authenticated_client.get(_detail(_pending_recent(db_conn))).data.decode()
        assert 'name="reassign_' not in html

    def test_point_of_sale_sees_no_review_forms_nor_checkboxes(self, punto_venta_client, db_conn):
        declaration_id = _three_cards_one_flagged(db_conn)
        html = punto_venta_client.get(_detail(declaration_id)).data.decode()
        assert "/approve" not in html and "/reject" not in html
        assert 'name="reassign_' not in html
        assert "a nombre de otro cliente" in html
        assert "/customers/" not in html

    def test_point_of_sale_sees_flags_context(self, punto_venta_client, db_conn):
        html = punto_venta_client.get(_detail(_pending_recent(db_conn))).data.decode()
        assert "menos de 6 meses" in html


class TestFiles:
    @pytest.mark.parametrize("kind,mimetype,body", [
        ("signature", "image/png", PNG),
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

    @pytest.mark.parametrize("kind", ["owner_signature", "gestor_signature"])
    def test_removed_signature_kinds_are_404(self, authenticated_client, db_conn, uploads, kind):
        declaration_id, _ = _with_files(db_conn, uploads)
        assert authenticated_client.get(f"{_detail(declaration_id)}/file/{kind}").status_code == 404

    def test_point_of_sale_can_download(self, punto_venta_client, db_conn, uploads):
        declaration_id, _ = _with_files(db_conn, uploads)
        resp = punto_venta_client.get(f"{_detail(declaration_id)}/file/signature")
        assert resp.status_code == 200

    def test_detail_shows_the_single_signature_and_links_to_the_files(
        self, authenticated_client, db_conn, uploads,
    ):
        declaration_id, names = _with_files(db_conn, uploads)
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert html.count(f"/declaraciones/{declaration_id}/file/signature") == 2   # link + <img>
        assert f"/declaraciones/{declaration_id}/file/notarized_pdf" in html
        assert all(name not in html for name in names.values())   # stored names never exposed

    def test_missing_file_on_disk_is_404(self, authenticated_client, db_conn, uploads):
        declaration_id, names = _with_files(db_conn, uploads)
        (uploads / names["signature"]).unlink()
        resp = authenticated_client.get(f"{_detail(declaration_id)}/file/signature")
        assert resp.status_code == 404

    def test_no_path_stored_is_404(self, authenticated_client, db_conn, uploads):
        declaration_id = _submit(db_conn, _input(signature_file=None))
        resp = authenticated_client.get(f"{_detail(declaration_id)}/file/notarized_pdf")
        assert resp.status_code == 404
        assert authenticated_client.get(f"{_detail(declaration_id)}/file/signature").status_code == 404

    def test_unknown_kind_is_404(self, authenticated_client, db_conn, uploads):
        declaration_id, _ = _with_files(db_conn, uploads)
        assert authenticated_client.get(f"{_detail(declaration_id)}/file/passwd").status_code == 404

    def test_unknown_declaration_is_404(self, authenticated_client, db_conn, uploads):
        assert authenticated_client.get(f"{_detail(9999)}/file/signature").status_code == 404

    def test_a_traversal_looking_stored_name_is_never_served(self, authenticated_client, db_conn, uploads, tmp_path):
        """Even if a bad value somehow reached the DB, resolve_upload_path
        only accepts names we generate, so nothing outside the dir is read."""
        (uploads.parent / "secret.png").write_bytes(PNG)
        declaration_id = _submit(db_conn, _input(signature_file="../secret.png"))
        resp = authenticated_client.get(f"{_detail(declaration_id)}/file/signature")
        assert resp.status_code == 404

    def test_the_url_cannot_carry_a_file_name(self, authenticated_client, db_conn, uploads):
        declaration_id, names = _with_files(db_conn, uploads)
        resp = authenticated_client.get(f"{_detail(declaration_id)}/file/{names['signature']}")
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
        (row,) = _cards_of(db_conn, declaration_id)
        assert row.customer_id is not None and row.ownership_card_id is not None

    def test_approve_with_reassign_moves_the_card(self, authenticated_client, db_conn):
        _create_card(db_conn, "OTRO DUENO")
        declaration_id = _submit(db_conn)
        (row,) = _cards_of(db_conn, declaration_id)
        resp = authenticated_client.post(
            f"{_detail(declaration_id)}/approve", data={f"reassign_{row.id}": "on"},
        )
        assert resp.status_code == 302
        assert _status(db_conn, declaration_id) == "approved"
        (row,) = _cards_of(db_conn, declaration_id)
        assert row.ownership_card_id is not None
        card = db.get_ownership_card(db_conn, row.ownership_card_id)
        assert card is not None and card.customer_id == row.customer_id

    def test_approves_every_card_of_a_multi_card_declaration(self, authenticated_client, db_conn):
        declaration_id = _three_cards_one_flagged(db_conn)
        rows = _cards_of(db_conn, declaration_id)
        resp = authenticated_client.post(
            f"{_detail(declaration_id)}/approve", data={f"reassign_{rows[1].id}": "on", "note": "ok"},
        )
        assert resp.status_code == 302
        assert _status(db_conn, declaration_id) == "approved"
        done = _cards_of(db_conn, declaration_id)
        assert all(r.customer_id and r.ownership_card_id for r in done)
        moved = db.get_ownership_card(db_conn, done[1].ownership_card_id)
        assert moved is not None and moved.customer_id == done[1].customer_id
        assert db.get_customer(db_conn, done[1].customer_id).name == "LUIS PEREZ"  # type: ignore[union-attr]
        record = db.get_declaration(db_conn, declaration_id)
        assert record is not None and record.gestor_id is not None

    def test_without_the_card_checkbox_nothing_is_approved_all_or_nothing(self, authenticated_client, db_conn):
        declaration_id = _three_cards_one_flagged(db_conn)
        before = db.get_ownership_card_by_transit_license(db_conn, "L2")
        customers_before = _count(db_conn, "customers")
        resp = authenticated_client.post(f"{_detail(declaration_id)}/approve", data={})
        assert resp.status_code == 400
        html = resp.data.decode()
        assert "tarjeta 2 (placa DEF456)" in html          # names the card
        assert "ya existe a nombre de otro cliente" in html
        assert 'class="error"' in html
        assert _status(db_conn, declaration_id) == "pending_review"
        assert db.get_ownership_card_by_transit_license(db_conn, "L2") == before
        assert db.get_ownership_card_by_transit_license(db_conn, "L1") is None   # card 1 NOT created
        assert db.get_customer_by_name(db_conn, "GESTORIA SUR") is None
        assert all(r.customer_id is None for r in _cards_of(db_conn, declaration_id))
        assert _count(db_conn, "customers") == customers_before
        assert _count(db_conn, "ownership_cards") == 1

    def test_error_rerender_keeps_the_ticked_checkboxes(self, authenticated_client, db_conn):
        _create_card(db_conn, "OTRO UNO", license_="L1", plate="ABC123")
        _create_card(db_conn, "OTRO DOS", license_="L2", plate="DEF456")
        cards = [_gcard("ABC123", "L1"), _gcard("DEF456", "L2", owner=_luis())]
        declaration_id = _submit(db_conn, _gestor_input(cards=cards))
        rows = _cards_of(db_conn, declaration_id)
        resp = authenticated_client.post(
            f"{_detail(declaration_id)}/approve", data={f"reassign_{rows[0].id}": "on"},
        )
        assert resp.status_code == 400
        html = resp.data.decode()
        assert "tarjeta 2 (placa DEF456)" in html
        assert re.search(rf'name="reassign_{rows[0].id}"[^>]* checked', html)
        assert not re.search(rf'name="reassign_{rows[1].id}"[^>]* checked', html)

    def test_reassign_id_of_another_declaration_is_400_and_changes_nothing(self, authenticated_client, db_conn):
        other_id = _three_cards_one_flagged(db_conn)
        foreign = _cards_of(db_conn, other_id)[1].id
        declaration_id = _pending_recent(db_conn)
        resp = authenticated_client.post(
            f"{_detail(declaration_id)}/approve", data={f"reassign_{foreign}": "on"},
        )
        assert resp.status_code == 400
        assert "no pertenece a esta declaración" in resp.data.decode()
        assert _status(db_conn, declaration_id) == "pending_review"
        assert _status(db_conn, other_id) == "pending_review"

    def test_unrelated_form_fields_are_ignored(self, authenticated_client, db_conn):
        declaration_id = _pending_recent(db_conn)
        resp = authenticated_client.post(
            f"{_detail(declaration_id)}/approve", data={"reassign_card": "on", "reassign_x": "1"},
        )
        assert resp.status_code == 302
        assert _status(db_conn, declaration_id) == "approved"

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
        declaration_id = _three_cards_one_flagged(db_conn)
        resp = authenticated_client.post(f"{_detail(declaration_id)}/reject", data={"note": "datos falsos"})
        assert resp.status_code == 302
        record = db.get_declaration(db_conn, declaration_id)
        assert record is not None
        assert record.status == "rejected" and record.review_note == "datos falsos"
        assert record.gestor_id is None
        assert all(r.customer_id is None and r.ownership_card_id is None for r in _cards_of(db_conn, declaration_id))
        assert db.get_customer_by_name(db_conn, "GESTORIA SUR") is None
        assert db.get_customer_by_name(db_conn, "LUIS PEREZ") is None

    def test_rejected_detail_shows_the_note(self, authenticated_client, db_conn):
        declaration_id = _pending_recent(db_conn)
        authenticated_client.post(f"{_detail(declaration_id)}/reject", data={"note": "datos falsos"})
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert "Rechazada" in html and "datos falsos" in html
        assert "/approve" not in html

    def test_note_is_required(self, authenticated_client, db_conn):
        declaration_id = _pending_recent(db_conn)
        resp = authenticated_client.post(f"{_detail(declaration_id)}/reject", data={"note": "   "})
        assert resp.status_code == 400
        assert "Escriba una nota" in resp.data.decode()
        assert _status(db_conn, declaration_id) == "pending_review"

    def test_unknown_id_redirects_to_list(self, authenticated_client, db_conn):
        resp = authenticated_client.post(f"{_detail(9999)}/reject", data={"note": "x"})
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(LIST_URL)


class TestRoles:
    @pytest.fixture
    def declaration_id(self, db_conn, uploads) -> int:
        return _with_files(db_conn, uploads)[0]

    def test_production_gets_403_on_everything(self, produccion_client, declaration_id):
        base = _detail(declaration_id)
        assert produccion_client.get(LIST_URL).status_code == 403
        assert produccion_client.get(base).status_code == 403
        assert produccion_client.get(f"{base}/file/signature").status_code == 403
        assert produccion_client.post(f"{base}/approve", data={}).status_code == 403
        assert produccion_client.post(f"{base}/reject", data={"note": "x"}).status_code == 403

    def test_point_of_sale_reads_but_cannot_decide(self, punto_venta_client, db_conn, declaration_id):
        base = _detail(declaration_id)
        assert punto_venta_client.get(LIST_URL).status_code == 200
        assert punto_venta_client.get(base).status_code == 200
        assert punto_venta_client.get(f"{base}/file/signature").status_code == 200
        assert punto_venta_client.post(f"{base}/approve", data={}).status_code == 403
        assert punto_venta_client.post(f"{base}/reject", data={"note": "x"}).status_code == 403
        assert db.get_declaration(db_conn, declaration_id).status == "approved"  # type: ignore[union-attr]

    def test_point_of_sale_cannot_decide_on_a_pending_one(self, punto_venta_client, db_conn):
        pending_id = _three_cards_one_flagged(db_conn)
        rows = _cards_of(db_conn, pending_id)
        data = {f"reassign_{rows[1].id}": "on"}
        assert punto_venta_client.post(f"{_detail(pending_id)}/approve", data=data).status_code == 403
        assert punto_venta_client.post(f"{_detail(pending_id)}/reject", data={"note": "x"}).status_code == 403
        assert _status(db_conn, pending_id) == "pending_review"

    def test_admin_can_do_everything(self, authenticated_client, declaration_id):
        base = _detail(declaration_id)
        assert authenticated_client.get(LIST_URL).status_code == 200
        assert authenticated_client.get(base).status_code == 200
        assert authenticated_client.get(f"{base}/file/signature").status_code == 200

    @pytest.mark.parametrize("method,url", [
        ("get", LIST_URL), ("get", "/declaraciones/1"), ("get", "/declaraciones/1/file/signature"),
        ("post", "/declaraciones/1/approve"), ("post", "/declaraciones/1/reject"),
    ])
    def test_anonymous_redirects_to_login(self, client, clean_db, method, url):
        resp = getattr(client, method)(url)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
