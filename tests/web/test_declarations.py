"""Integration tests for the internal /declaraciones review screen.

A declaration is ONE declarant plus 1..N ownership cards; a flag on any card
holds the whole declaration. Contract under test:
- every route is @pos_allowed (admin + point_of_sale): the point of sale
  reviews (approve / reject / reassign) and handles the notarization step; the
  read-only 'production' role gets 403 on everything (it handles ID data);
  anonymous requests redirect to /login.
- PÉRDIDA vs DETERIORO: reason badge per card, "2 pérdida · 1 deterioro" in
  the list, a reason filter; approved declarations with a PÉRDIDA card get a
  PDF to download, an "Autenticación notarial" block and a gate on "Crear
  pedido".
- the list shows one row per declaration (declarant, card count, total
  plates, compact summary) and the search finds any card's data.
- the detail page has one section per card and explains each card's flags
  with the real context staff need; approval is all-or-nothing and reassigning
  a card is chosen per card (`reassign_<declaration_card_id>`).
- files are served only through a stored name resolved by
  app.web.uploads.resolve_upload_path, with safe headers.

EVERY public declaration is stored as pending_review (flags or not); the
point of sale approves it. So "approved" fixtures are built the real way:
submit_declaration, then approve_declaration (see _submit_approved). Each
declaration shows ONE status badge, its derived stage (db.declaration_stage),
and the list filters by it (`?stage=`).

Pending declarations are built through the real
app.core.declarations.submit_declaration so the conflicts are real. No test
here expects a DB error, so no rollback gymnastics are needed (see
tests/conftest.py's clean_db docstring).
"""

from __future__ import annotations

import io
import re
import uuid
import zipfile
from dataclasses import replace
from datetime import date, datetime

import pytest

from app.core import db
from app.core import declarations as decl
from app.core.models import ExtractedRow
from tests.conftest import PUNTO_VENTA_USERNAME, TEST_USERNAME
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
    _reviewer_id,
)

LIST_URL = "/declaraciones"
PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"
PDF = b"%PDF-1.4 fake pdf body"
JPEG = b"\xff\xd8\xff\xe0" + b"fake-jpeg-body"


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
    declaration_id = _submit(db_conn, _gestor_input(cards=cards))
    record = db.get_declaration(db_conn, declaration_id)
    assert record is not None and record.status == "pending_review"
    return declaration_id


def _status(db_conn, declaration_id: int) -> str:
    record = db.get_declaration(db_conn, declaration_id)
    assert record is not None
    return record.status


def _make_order(db_conn, order_number: str = "7001") -> int:
    row = ExtractedRow(
        order_number=order_number, order_date=date(2026, 9, 2), requested_by="MARIA LOPEZ",
        plate_type="CARRO PARTICULAR",
    )
    return db.insert_order(db_conn, row=row, customer_id=None, created_by=None)


def _submit_approved(db_conn, inp=None) -> int:
    """Submit (always pending) and approve it as the reviewer: the one helper
    every "approved declaration" fixture goes through."""
    declaration_id = _submit(db_conn, inp)
    decl.approve_declaration(db_conn, declaration_id, _reviewer_id(db_conn))
    assert _status(db_conn, declaration_id) == "approved"
    return declaration_id


def _approved(db_conn, name: str = "ANA APROBADA", plate: str = "AAA111") -> int:
    """An approved declaration whose only card is a DETERIORO (no notary step)."""
    return _submit_approved(db_conn, _input(
        declarant=_owner(name=name), cards=[_card(plate_number=plate, transit_license=f"L-{plate}")],
    ))


def _unflagged_pending(db_conn) -> int:
    """A clean declaration: pending_review with NO review flags."""
    declaration_id = _submit(db_conn)
    record = db.get_declaration(db_conn, declaration_id)
    assert record is not None and record.status == "pending_review" and record.review_flags == []
    return declaration_id


STAGE_BADGE = re.compile(r'class="badge (to_review|awaiting_notarization|ready|attended|rejected)"')


def _stage_badges(html: str) -> list[str]:
    return STAGE_BADGE.findall(html)


def _attend(db_conn, declaration_id: int, order_number: str = "7001") -> int:
    order_id = _make_order(db_conn, order_number)
    db.set_declaration_production_order(db_conn, declaration_id, order_id)
    return order_id


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
        signature_file=names["signature"],
    )
    declaration_id = _submit_approved(db_conn, inp)
    # The public form no longer takes a notarized PDF; the column stays for legacy
    # rows, so write it straight through to keep covering that download kind.
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE declarations SET notarized_pdf_path = %s WHERE id = %s",
            (names["notarized_pdf"], declaration_id),
        )
    return declaration_id, names


class TestList:
    def test_default_shows_pending_first_and_hides_nothing_in_pending_tab(self, authenticated_client, db_conn):
        approved_id = _approved(db_conn)
        pending_id = _pending_recent(db_conn)
        html = authenticated_client.get(LIST_URL).data.decode()
        assert "MARIA LOPEZ" in html
        assert "ANA APROBADA" not in html          # default tab = Por revisar
        assert "Placa reciente" in html
        assert _status(db_conn, approved_id) == "approved"
        assert _status(db_conn, pending_id) == "pending_review"

    def test_all_tab_puts_pending_before_approved(self, authenticated_client, db_conn):
        _approved(db_conn)
        _pending_recent(db_conn)
        html = authenticated_client.get(f"{LIST_URL}?stage=all").data.decode()
        assert html.index("MARIA LOPEZ") < html.index("ANA APROBADA")

    def test_unknown_stage_falls_back_to_pending(self, authenticated_client, db_conn):
        _pending_recent(db_conn)
        resp = authenticated_client.get(f"{LIST_URL}?stage=bogus")
        assert resp.status_code == 200 and "MARIA LOPEZ" in resp.data.decode()

    def test_one_row_per_declaration_with_counts_and_summary(self, authenticated_client, db_conn):
        _three_cards_one_flagged(db_conn)
        html = authenticated_client.get(LIST_URL).data.decode()
        assert html.count("<tr>") == 2                      # header + ONE declaration row
        row = html[html.index('data-label="Declarante"'):]
        assert "GESTORIA SUR" in row and "GESTOR" in row
        assert re.search(r'data-label="Tarjetas">3<', html)
        assert re.search(r'data-label="Placas">4<', html)   # 1 + 2 + 1, not 3
        assert "ABC123 · DEF456 +1" in html
        assert "1 pérdida · 2 deterioro" in html            # mixed reasons are counted, PÉRDIDA first
        assert "Tarjeta de otro dueño" in html
        assert "en la tarjeta 2" in html                    # which card holds it

    def test_single_card_summary_has_no_plus(self, authenticated_client, db_conn):
        _pending_recent(db_conn)
        html = authenticated_client.get(LIST_URL).data.decode()
        assert re.search(r'data-label="Tarjetas">1<', html)
        assert "ABC123<" in html and "+" not in html[html.index("<tbody>"):]
        assert "1 deterioro" in html

    @pytest.mark.parametrize("term", [
        "MARIA", "1020304050", "ABC123", "LIC-001",          # declarant / its cards
    ])
    def test_search_single_card(self, authenticated_client, db_conn, term):
        _pending_recent(db_conn)
        html = authenticated_client.get(f"{LIST_URL}?stage=all&q={term}").data.decode()
        assert "MARIA LOPEZ" in html

    @pytest.mark.parametrize("term", [
        "GESTORIA", "900123456",            # the declarant
        "DEF456", "L3",                      # a non-first card's plate / licence
        "LUIS PEREZ", "777",                 # a card's own owner
    ])
    def test_search_finds_the_declaration_through_any_card(self, authenticated_client, db_conn, term):
        _three_cards_one_flagged(db_conn)
        html = authenticated_client.get(f"{LIST_URL}?stage=all&q={term}").data.decode()
        assert "GESTORIA SUR" in html

    def test_search_without_match(self, authenticated_client, db_conn):
        _pending_recent(db_conn)
        html = authenticated_client.get(f"{LIST_URL}?q=NOEXISTE").data.decode()
        assert "No se encontró ninguna declaración" in html

    def test_pending_count_is_visible(self, authenticated_client, db_conn):
        _pending_recent(db_conn)
        html = authenticated_client.get(LIST_URL).data.decode()
        assert "Por revisar (1)" in html

    def test_list_loads_cards_with_one_query_for_all_rows(self, authenticated_client, db_conn, monkeypatch):
        _pending_recent(db_conn)
        _three_cards_one_flagged(db_conn)
        calls: list[int] = []
        real = db.list_cards_for_declarations

        def spy(conn, ids):
            calls.append(len(list(ids)))
            return real(conn, ids)

        monkeypatch.setattr(db, "list_cards_for_declarations", spy)
        authenticated_client.get(f"{LIST_URL}?stage=all")
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
        summary = html[html.index("Retenida por"):html.index("Archivos")]   # the held-cards box only
        assert 'href="#card-2"' in summary and 'href="#card-1"' not in summary
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
        declaration_id = _submit_approved(db_conn, _gestor_input(cards=cards))
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

    @pytest.mark.parametrize("client_fixture", ["authenticated_client", "punto_venta_client"])
    def test_unflagged_pending_has_a_plain_approve_button_and_reject_with_required_note(
        self, request, db_conn, client_fixture,
    ):
        client = request.getfixturevalue(client_fixture)
        declaration_id = _unflagged_pending(db_conn)
        html = client.get(_detail(declaration_id)).data.decode()
        assert f'action="/declaraciones/{declaration_id}/approve"' in html
        assert "Aprobar declaración</button>" in html
        assert "no tiene alertas" in html
        assert "se crean o enlazan" in html and "tarjeta de propiedad" in html
        assert f'action="/declaraciones/{declaration_id}/reject"' in html
        assert re.search(r'<textarea name="note" rows="2" required>', html)
        assert "Retenida" not in html and 'name="reassign_' not in html
        assert _stage_badges(html) == ["to_review"] and "Por revisar" in html

    def test_flagged_pending_keeps_alerts_and_the_same_approve_button(self, authenticated_client, db_conn):
        html = authenticated_client.get(_detail(_pending_recent(db_conn))).data.decode()
        assert "Aprobar declaración</button>" in html
        assert "no tiene alertas" not in html
        assert "Retenida por 1 de 1 tarjeta" in html

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

    def test_point_of_sale_sees_review_forms_and_reassign_checkbox(self, punto_venta_client, db_conn):
        declaration_id = _three_cards_one_flagged(db_conn)
        rows = _cards_of(db_conn, declaration_id)
        html = punto_venta_client.get(_detail(declaration_id)).data.decode()
        assert f"/declaraciones/{declaration_id}/approve" in html
        assert f"/declaraciones/{declaration_id}/reject" in html
        assert f'name="reassign_{rows[1].id}"' in html
        assert "a nombre de otro cliente" in html
        assert "administrador" not in html

    def test_point_of_sale_gets_no_links_to_the_admin_only_customer_screen(self, punto_venta_client, db_conn):
        declaration_id = _submit_approved(db_conn, _gestor_input(cards=[_gcard("ABC123", "L1")]))
        html = punto_venta_client.get(_detail(declaration_id)).data.decode()
        assert "Resultado de la revisión" in html
        assert "/customers/" not in html

    def test_point_of_sale_sees_flags_context(self, punto_venta_client, db_conn):
        html = punto_venta_client.get(_detail(_pending_recent(db_conn))).data.decode()
        assert "menos de 6 meses" in html


def _with_photos(db_conn, uploads) -> tuple[int, int, dict[str, str]]:
    """An approved declaration with the cédula (front, back) and one card photo
    pair stored; returns (declaration id, card id, stored names)."""
    names = {
        "id_front": _store(uploads, JPEG, "jpg"), "id_back": _store(uploads, PNG, "png"),
        "card_front": _store(uploads, JPEG, "jpg"), "card_back": _store(uploads, JPEG, "jpg"),
    }
    inp = replace(
        _input(cards=[_card(front_file=names["card_front"], back_file=names["card_back"])]),
        id_front_file=names["id_front"], id_back_file=names["id_back"],
    )
    declaration_id = _submit_approved(db_conn, inp)
    (card,) = db.list_declaration_cards(db_conn, declaration_id)
    return declaration_id, card.id, names


class TestPhotos:
    @pytest.mark.parametrize("kind,mimetype,body", [
        ("id_front", "image/jpeg", JPEG), ("id_back", "image/png", PNG),
    ])
    def test_serves_the_cedula_photos(self, authenticated_client, db_conn, uploads, kind, mimetype, body):
        declaration_id, _, _ = _with_photos(db_conn, uploads)
        resp = authenticated_client.get(f"{_detail(declaration_id)}/file/{kind}")
        assert (resp.status_code, resp.mimetype, resp.data) == (200, mimetype, body)
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["Cache-Control"] == "no-store"

    @pytest.mark.parametrize("side", ["front", "back"])
    def test_serves_the_card_photos(self, punto_venta_client, db_conn, uploads, side):
        declaration_id, card_id, _ = _with_photos(db_conn, uploads)
        resp = punto_venta_client.get(f"{_detail(declaration_id)}/cards/{card_id}/photo/{side}")
        assert (resp.status_code, resp.mimetype, resp.data) == (200, "image/jpeg", JPEG)
        assert resp.headers["Cache-Control"] == "no-store"

    def test_a_card_of_another_declaration_or_a_bad_side_is_404(self, authenticated_client, db_conn, uploads):
        declaration_id, card_id, _ = _with_photos(db_conn, uploads)
        other_id, _, _ = _with_photos(db_conn, uploads)
        base = _detail(other_id)
        assert authenticated_client.get(f"{base}/cards/{card_id}/photo/front").status_code == 404
        assert authenticated_client.get(f"{_detail(declaration_id)}/cards/{card_id}/photo/side").status_code == 404
        assert authenticated_client.get(f"{_detail(declaration_id)}/cards/999999/photo/front").status_code == 404

    def test_older_declarations_without_photos_are_404_and_show_none(self, authenticated_client, db_conn, uploads):
        declaration_id = _approved(db_conn)
        with db_conn.cursor() as cur:   # a declaration made before the form asked for photos
            cur.execute("UPDATE declarations SET declarant_id_front_path = NULL, declarant_id_back_path = NULL")
            cur.execute("UPDATE declaration_cards SET card_front_path = NULL, card_back_path = NULL")
        (card,) = db.list_declaration_cards(db_conn, declaration_id)
        assert authenticated_client.get(f"{_detail(declaration_id)}/file/id_front").status_code == 404
        assert authenticated_client.get(f"{_detail(declaration_id)}/cards/{card.id}/photo/back").status_code == 404
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert "Fotos de la tarjeta" not in html and "Cédula del declarante" not in html

    def test_the_detail_shows_the_photos_without_exposing_stored_names(self, authenticated_client, db_conn, uploads):
        declaration_id, card_id, names = _with_photos(db_conn, uploads)
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert "Cédula del declarante" in html and "Fotos de la tarjeta de propiedad" in html
        for kind in ("id_front", "id_back"):
            assert f"/declaraciones/{declaration_id}/file/{kind}" in html
        for side in ("front", "back"):
            assert f"/declaraciones/{declaration_id}/cards/{card_id}/photo/{side}" in html
        assert all(name not in html for name in names.values())

    def test_the_files_section_links_to_each_card_with_photos(self, authenticated_client, db_conn, uploads):
        declaration_id, _, _ = _with_photos(db_conn, uploads)
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert 'href="#card-1"' in html and "Fotos de la tarjeta de propiedad (en cada tarjeta" in html

    def test_zip_has_every_photo_with_readable_names(self, authenticated_client, db_conn, uploads):
        declaration_id, _, _ = _with_photos(db_conn, uploads)
        resp = authenticated_client.get(f"{_detail(declaration_id)}/photos.zip")
        assert resp.status_code == 200 and resp.mimetype == "application/zip"
        assert f"declaracion-{declaration_id}-fotos.zip" in resp.headers["Content-Disposition"]
        assert resp.headers["Cache-Control"] == "no-store"
        with zipfile.ZipFile(io.BytesIO(resp.data)) as archive:
            names = sorted(archive.namelist())
            assert names == ["cedula-atras.png", "cedula-frente.jpg",
                             "tarjeta-1-ABC123-atras.jpg", "tarjeta-1-ABC123-frente.jpg"]
            assert archive.read("cedula-frente.jpg") == JPEG and archive.read("cedula-atras.png") == PNG

    def test_zip_skips_missing_files_and_is_404_when_nothing_is_left(self, authenticated_client, db_conn, uploads):
        declaration_id, _, names = _with_photos(db_conn, uploads)
        (uploads / names["id_front"]).unlink()
        resp = authenticated_client.get(f"{_detail(declaration_id)}/photos.zip")
        with zipfile.ZipFile(io.BytesIO(resp.data)) as archive:
            assert "cedula-frente.jpg" not in archive.namelist() and len(archive.namelist()) == 3
        for name in ("id_back", "card_front", "card_back"):
            (uploads / names[name]).unlink()
        assert authenticated_client.get(f"{_detail(declaration_id)}/photos.zip").status_code == 404

    def test_zip_button_only_when_there_are_photos(self, authenticated_client, db_conn, uploads):
        declaration_id, _, _ = _with_photos(db_conn, uploads)
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert f"/declaraciones/{declaration_id}/photos.zip" in html and "Descargar todas las fotos" in html
        with db_conn.cursor() as cur:
            cur.execute("UPDATE declarations SET declarant_id_front_path = NULL, declarant_id_back_path = NULL")
            cur.execute("UPDATE declaration_cards SET card_front_path = NULL, card_back_path = NULL")
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert "photos.zip" not in html
        assert authenticated_client.get(f"{_detail(declaration_id)}/photos.zip").status_code == 404

    def test_production_role_cannot_download_the_zip(self, produccion_client, db_conn, uploads):
        declaration_id, _, _ = _with_photos(db_conn, uploads)
        assert produccion_client.get(f"{_detail(declaration_id)}/photos.zip").status_code == 403

    def test_a_missing_photo_file_is_404(self, authenticated_client, db_conn, uploads):
        declaration_id, _, names = _with_photos(db_conn, uploads)
        (uploads / names["id_front"]).unlink()
        assert authenticated_client.get(f"{_detail(declaration_id)}/file/id_front").status_code == 404

    def test_production_role_cannot_see_the_photos(self, produccion_client, db_conn, uploads):
        declaration_id, card_id, _ = _with_photos(db_conn, uploads)
        assert produccion_client.get(f"{_detail(declaration_id)}/file/id_front").status_code == 403
        assert produccion_client.get(f"{_detail(declaration_id)}/cards/{card_id}/photo/front").status_code == 403


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

    @pytest.mark.parametrize("client_fixture", ["authenticated_client", "punto_venta_client"])
    def test_unflagged_declaration_is_approved_end_to_end(self, request, db_conn, client_fixture):
        client = request.getfixturevalue(client_fixture)
        declaration_id = _unflagged_pending(db_conn)
        assert db.get_customer_by_name(db_conn, "MARIA LOPEZ") is None      # nothing created yet
        assert _count(db_conn, "ownership_cards") == 0
        resp = client.post(f"{_detail(declaration_id)}/approve", data={"note": ""})
        assert resp.status_code == 302 and resp.headers["Location"].endswith(_detail(declaration_id))
        assert _status(db_conn, declaration_id) == "approved"
        assert db.get_customer_by_name(db_conn, "MARIA LOPEZ") is not None
        (row,) = _cards_of(db_conn, declaration_id)
        assert row.customer_id is not None and row.ownership_card_id is not None
        html = client.get(_detail(declaration_id)).data.decode()
        assert _stage_badges(html) == ["ready"] and "Lista para pedido" in html
        assert "Crear pedido" in html and "/approve" not in html

    def test_approval_error_on_an_unflagged_declaration_is_shown_in_spanish(self, authenticated_client, db_conn):
        declaration_id = _unflagged_pending(db_conn)
        # Created after the submission: approve_declaration re-checks the customer's type.
        db.resolve_or_create_customer(db_conn, "MARIA LOPEZ", type="EMPRESA")
        resp = authenticated_client.post(f"{_detail(declaration_id)}/approve", data={})
        assert resp.status_code == 400
        html = resp.data.decode()
        assert 'class="error"' in html and "ya existe con tipo EMPRESA" in html
        assert _status(db_conn, declaration_id) == "pending_review"
        assert "Aprobar declaración</button>" in html

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


class TestCreateOrder:
    """The "Crear pedido" step: any role that sees the screen can start the
    order of an approved, not-yet-attended declaration."""

    @pytest.mark.parametrize("client_fixture", ["authenticated_client", "punto_venta_client"])
    def test_button_for_approved_unattended_for_both_roles(self, request, db_conn, client_fixture):
        client = request.getfixturevalue(client_fixture)
        declaration_id = _approved(db_conn)
        html = client.get(_detail(declaration_id)).data.decode()
        assert "Crear pedido" in html
        assert f'href="/ingest?declaration_id={declaration_id}"' in html
        assert "Atendida" not in html

    def test_no_button_for_pending_but_explains_why(self, authenticated_client, db_conn):
        html = authenticated_client.get(_detail(_pending_recent(db_conn))).data.decode()
        assert "Crear pedido" not in html and "declaration_id=" not in html
        assert "cuando el punto de venta apruebe" in html and "revisa todas las declaraciones" in html
        assert "administrador" not in html

    def test_no_button_for_rejected(self, authenticated_client, db_conn):
        pending_id = _pending_recent(db_conn)
        decl.reject_declaration(db_conn, pending_id, 1, "no")
        html = authenticated_client.get(_detail(pending_id)).data.decode()
        assert "Crear pedido" not in html and "declaration_id=" not in html
        assert "cuando el punto de venta apruebe" not in html
        assert _stage_badges(html) == ["rejected"] and "Rechazada" in html

    @pytest.mark.parametrize("client_fixture", ["authenticated_client", "punto_venta_client"])
    def test_attended_shows_badge_and_order_link_without_button(self, request, db_conn, client_fixture):
        client = request.getfixturevalue(client_fixture)
        declaration_id = _approved(db_conn)
        order_id = _attend(db_conn, declaration_id, "7001")
        html = client.get(_detail(declaration_id)).data.decode()
        assert "Atendida" in html
        assert f'href="/records/{order_id}"' in html and ">7001<" in html
        assert "Crear pedido" not in html
        assert _stage_badges(html) == ["attended"]

    def test_pending_page_keeps_approve_and_reject_forms(self, authenticated_client, db_conn):
        pending_id = _pending_recent(db_conn)
        html = authenticated_client.get(_detail(pending_id)).data.decode()
        assert 'id="approve-form"' in html
        assert f"/declaraciones/{pending_id}/reject" in html

    def test_failed_approval_rerender_has_no_button(self, authenticated_client, db_conn):
        pending_id = _pending_recent(db_conn)
        resp = authenticated_client.post(f"{_detail(pending_id)}/reject", data={"note": ""})
        assert resp.status_code == 400
        html = resp.data.decode()
        assert "Crear pedido" not in html and 'class="error"' in html

    def test_list_shows_one_stage_badge_per_row(self, authenticated_client, db_conn):
        _approved(db_conn)
        done = _approved(db_conn, name="BETO ATENDIDO", plate="BBB222")
        _attend(db_conn, done)
        html = authenticated_client.get(f"{LIST_URL}?stage=all").data.decode()
        rows = html[html.index("<tbody>"):].split("<tr>")[1:]
        by_name = {name: next(r for r in rows if name in r) for name in ("ANA APROBADA", "BETO ATENDIDO")}
        assert _stage_badges(by_name["ANA APROBADA"]) == ["ready"]
        assert "Lista para pedido" in by_name["ANA APROBADA"]
        assert _stage_badges(by_name["BETO ATENDIDO"]) == ["attended"]
        assert "Atendida" in by_name["BETO ATENDIDO"] and "Lista para pedido" not in by_name["BETO ATENDIDO"]
        for row in by_name.values():
            for old in ("Aprobada", "Por atender", "Autenticada"):
                assert old not in row

    def test_list_pending_row_has_only_the_review_badge(self, authenticated_client, db_conn):
        _pending_recent(db_conn)
        row = authenticated_client.get(LIST_URL).data.decode().split("<tbody>")[1]
        assert _stage_badges(row) == ["to_review"] and "Por revisar" in row
        assert "Atendida" not in row and "Lista para pedido" not in row and "Pendiente" not in row

    def test_failed_review_rerender_keeps_a_single_badge(self, authenticated_client, db_conn):
        pending_id = _pending_recent(db_conn)
        html = authenticated_client.post(f"{_detail(pending_id)}/reject", data={"note": ""}).data.decode()
        assert _stage_badges(html) == ["to_review"]


class TestStageFilter:
    @pytest.fixture
    def names(self, db_conn) -> dict[str, str]:
        """One declaration per situation, keyed by label -> declarant name."""
        _approved(db_conn, name="PEDRO LISTO", plate="PPP111")                       # ready
        attended = _approved(db_conn, name="BETO ATENDIDO", plate="BBB222")
        _attend(db_conn, attended)                                                    # attended
        _loss_approved(db_conn, name="LUISA PERDIDA", plate="LLL111")                 # awaiting_notarization
        notarized = _loss_approved(db_conn, name="NORA NOTARIADA", plate="NNN333")
        db.set_declaration_notarized_received(db_conn, notarized, received_by=None)   # ready
        rejected = _pending_recent(db_conn)                                           # MARIA LOPEZ
        decl.reject_declaration(db_conn, rejected, 1, "no")
        _three_cards_one_flagged(db_conn)                                             # GESTORIA SUR: to_review
        return {
            "ready": "PEDRO LISTO", "attended": "BETO ATENDIDO", "awaiting_notarization": "LUISA PERDIDA",
            "notarized": "NORA NOTARIADA", "rejected": "MARIA LOPEZ", "to_review": "GESTORIA SUR",
        }

    @staticmethod
    def _shown(client, names: dict[str, str], qs: str) -> set[str]:
        html = client.get(f"{LIST_URL}?{qs}").data.decode()
        return {key for key, name in names.items() if name in html}

    def test_each_tab_shows_only_its_stage(self, authenticated_client, names):
        def shown(stage: str) -> set[str]:
            return self._shown(authenticated_client, names, f"stage={stage}")

        assert shown("to_review") == {"to_review"}
        assert shown("awaiting_notarization") == {"awaiting_notarization"}
        assert shown("ready") == {"ready", "notarized"}
        assert shown("attended") == {"attended"}
        assert shown("rejected") == {"rejected"}
        assert shown("all") == set(names)

    def test_default_tab_is_to_review(self, authenticated_client, names):
        assert self._shown(authenticated_client, names, "") == {"to_review"}

    def test_stage_combines_with_search_and_reason(self, authenticated_client, names):
        def get(qs: str) -> set[str]:
            return self._shown(authenticated_client, names, qs)

        assert get("stage=ready&q=PEDRO") == {"ready"}
        assert get("stage=ready&q=BETO") == set()
        assert get("stage=all&q=LUISA") == {"awaiting_notarization"}
        assert get("stage=ready&reason=loss") == {"notarized"}
        assert get("stage=ready&reason=deterioration") == {"ready"}
        assert get("stage=all&reason=loss") == {"awaiting_notarization", "notarized", "to_review"}
        assert get("stage=awaiting_notarization&reason=deterioration") == set()
        assert get("stage=all&reason=loss&q=NORA") == {"notarized"}

    def test_tabs_are_the_stage_labels_and_keep_search_and_reason(self, authenticated_client, names):
        html = authenticated_client.get(f"{LIST_URL}?stage=ready&q=PEDRO&reason=loss").data.decode()
        filters = html[html.index('class="filters"'):html.index('class="search"')]
        for label in ("Por revisar", "Falta autenticación", "Lista para pedido", "Atendidas", "Rechazadas", "Todas"):
            assert f">{label}" in filters
        assert "Pendientes" not in filters and "Aprobadas" not in filters
        assert re.search(r'href="[^"]*stage=awaiting_notarization[^"]*q=PEDRO[^"]*reason=loss', filters)
        assert re.search(r'class="on">Lista para pedido', filters)
        assert 'name="stage" value="ready"' in html
        assert 'name="attended"' not in html

    def test_stage_filter_empty_state_message(self, authenticated_client, db_conn):
        html = authenticated_client.get(f"{LIST_URL}?stage=attended").data.decode()
        assert "No hay declaraciones en esta etapa." in html


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

    def test_point_of_sale_reads_everything(self, punto_venta_client, declaration_id):
        base = _detail(declaration_id)
        assert punto_venta_client.get(LIST_URL).status_code == 200
        assert punto_venta_client.get(base).status_code == 200
        assert punto_venta_client.get(f"{base}/file/signature").status_code == 200
        assert punto_venta_client.get(f"{base}/pdf").status_code == 200

    def test_point_of_sale_approves_with_reassign_and_is_recorded_as_reviewer(
        self, punto_venta_client, db_conn,
    ):
        pending_id = _three_cards_one_flagged(db_conn)
        rows = _cards_of(db_conn, pending_id)
        data = {f"reassign_{rows[1].id}": "on", "note": "revisado en mostrador"}
        resp = punto_venta_client.post(f"{_detail(pending_id)}/approve", data=data)
        assert resp.status_code == 302
        record = db.get_declaration(db_conn, pending_id)
        assert record is not None and record.status == "approved"
        pos_user = db.get_user_by_username(db_conn, PUNTO_VENTA_USERNAME)
        assert pos_user is not None and record.reviewed_by == pos_user.id
        card = db.get_ownership_card(db_conn, _cards_of(db_conn, pending_id)[1].ownership_card_id)
        assert card is not None and card.customer_id == _cards_of(db_conn, pending_id)[1].customer_id

    def test_point_of_sale_rejects_and_is_recorded_as_reviewer(self, punto_venta_client, db_conn):
        pending_id = _three_cards_one_flagged(db_conn)
        resp = punto_venta_client.post(f"{_detail(pending_id)}/reject", data={"note": "datos falsos"})
        assert resp.status_code == 302
        record = db.get_declaration(db_conn, pending_id)
        assert record is not None and record.status == "rejected"
        pos_user = db.get_user_by_username(db_conn, PUNTO_VENTA_USERNAME)
        assert pos_user is not None and record.reviewed_by == pos_user.id

    def test_point_of_sale_marks_notarized_received_and_undoes_it(self, punto_venta_client, db_conn, declaration_id):
        resp = punto_venta_client.post(f"{_detail(declaration_id)}/notarized")
        assert resp.status_code == 302
        record = db.get_declaration(db_conn, declaration_id)
        pos_user = db.get_user_by_username(db_conn, PUNTO_VENTA_USERNAME)
        assert record is not None and pos_user is not None
        assert record.notarized_received_at is not None and record.notarized_received_by == pos_user.id
        assert punto_venta_client.post(f"{_detail(declaration_id)}/notarized/undo").status_code == 302
        record = db.get_declaration(db_conn, declaration_id)
        assert record is not None and record.notarized_received_at is None

    def test_production_gets_403_on_the_new_actions(self, produccion_client, db_conn, declaration_id):
        base = _detail(declaration_id)
        assert produccion_client.get(f"{base}/pdf").status_code == 403
        assert produccion_client.post(f"{base}/notarized").status_code == 403
        assert produccion_client.post(f"{base}/notarized/undo").status_code == 403
        record = db.get_declaration(db_conn, declaration_id)
        assert record is not None and record.notarized_received_at is None

    def test_admin_can_do_everything(self, authenticated_client, declaration_id):
        base = _detail(declaration_id)
        assert authenticated_client.get(LIST_URL).status_code == 200
        assert authenticated_client.get(base).status_code == 200
        assert authenticated_client.get(f"{base}/file/signature").status_code == 200

    @pytest.mark.parametrize("method,url", [
        ("get", LIST_URL), ("get", "/declaraciones/1"), ("get", "/declaraciones/1/file/signature"),
        ("get", "/declaraciones/1/file/id_front"), ("get", "/declaraciones/1/cards/1/photo/front"),
        ("get", "/declaraciones/1/photos.zip"),
        ("post", "/declaraciones/1/approve"), ("post", "/declaraciones/1/reject"),
        ("get", "/declaraciones/1/pdf"), ("post", "/declaraciones/1/notarized"),
        ("post", "/declaraciones/1/notarized/undo"),
    ])
    def test_anonymous_redirects_to_login(self, client, clean_db, method, url):
        resp = getattr(client, method)(url)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


def _loss_approved(db_conn, name: str = "LUISA PERDIDA", plate: str = "LLL111", **input_kw) -> int:
    """An approved declaration whose only card is a PÉRDIDA (awaiting the
    notarized copy until it is marked received)."""
    return _submit_approved(db_conn, _input(
        declarant=_owner(name=name),
        cards=[_card(plate_number=plate, transit_license=f"L-{plate}", reason="PÉRDIDA")],
        **input_kw,
    ))


def _record(db_conn, declaration_id: int) -> db.DeclarationRecord:
    record = db.get_declaration(db_conn, declaration_id)
    assert record is not None
    return record


class TestReasons:
    def test_detail_badges_each_card_reason_distinctly(self, authenticated_client, db_conn):
        html = authenticated_client.get(_detail(_three_cards_one_flagged(db_conn))).data.decode()
        first, second, third = (_section(html, n) for n in (1, 2, 3))
        assert 'class="badge reason deterioration">DETERIORO<' in first
        assert 'class="badge reason loss">PÉRDIDA<' in second
        assert 'class="badge reason deterioration">DETERIORO<' in third
        assert "reason loss" not in first and "reason loss" not in third

    def test_list_summary_counts_reasons(self, authenticated_client, db_conn):
        _three_cards_one_flagged(db_conn)
        html = authenticated_client.get(LIST_URL).data.decode()
        assert "1 pérdida · 2 deterioro" in html
        assert "reasons has-loss" in html

    def test_list_summary_for_a_pure_declaration_has_no_loss_style(self, authenticated_client, db_conn):
        _pending_recent(db_conn)
        body = authenticated_client.get(LIST_URL).data.decode().split("<tbody>")[1]
        assert "1 deterioro" in body and "pérdida" not in body
        assert "has-loss" not in body

    def test_filter_by_reason(self, authenticated_client, db_conn):
        _approved(db_conn)                                     # ANA APROBADA: DETERIORO only
        _loss_approved(db_conn)                                # LUISA PERDIDA: PÉRDIDA only
        _three_cards_one_flagged(db_conn)                      # GESTORIA SUR (pending): mixed
        everyone = {"ANA APROBADA", "LUISA PERDIDA", "GESTORIA SUR"}

        def names(reason: str) -> set[str]:
            html = authenticated_client.get(f"{LIST_URL}?stage=all&reason={reason}").data.decode()
            return {n for n in everyone if n in html}

        assert names("all") == everyone
        assert names("loss") == {"LUISA PERDIDA", "GESTORIA SUR"}            # any card PÉRDIDA
        assert names("deterioration") == {"ANA APROBADA", "GESTORIA SUR"}    # any card DETERIORO
        assert names("bogus") == everyone
        assert names("PÉRDIDA") == {"LUISA PERDIDA", "GESTORIA SUR"}         # the stored word works too

    def test_reason_filter_combines_with_stage_and_search(self, authenticated_client, db_conn):
        _loss_approved(db_conn)
        attended = _loss_approved(db_conn, name="BETO ATENDIDO", plate="BBB222")
        _attend(db_conn, attended)
        _approved(db_conn)

        def get(qs: str) -> str:
            return authenticated_client.get(f"{LIST_URL}?{qs}").data.decode()

        assert "LUISA PERDIDA" not in get("stage=to_review&reason=loss")
        html = get("stage=awaiting_notarization&reason=loss")
        assert "LUISA PERDIDA" in html and "BETO ATENDIDO" not in html and "ANA APROBADA" not in html
        html = get("stage=attended&reason=loss&q=BETO")
        assert "BETO ATENDIDO" in html and "LUISA PERDIDA" not in html

    def test_reason_select_is_rendered_selected_and_kept_by_the_tabs(self, authenticated_client, db_conn):
        _loss_approved(db_conn)
        html = authenticated_client.get(f"{LIST_URL}?stage=awaiting_notarization&reason=loss").data.decode()
        assert '<option value="loss" selected>Pérdida</option>' in html
        assert '<option value="all">Todos</option>' in html
        assert '<option value="deterioration">Deterioro</option>' in html
        assert "reason=loss" in html


class TestNotarizationList:
    def test_one_badge_tells_the_notarization_state_of_loss_declarations(self, authenticated_client, db_conn):
        _loss_approved(db_conn)
        done = _loss_approved(db_conn, name="BETO AUTENTICADO", plate="BBB222")
        db.set_declaration_notarized_received(db_conn, done, received_by=None)
        _approved(db_conn)
        html = authenticated_client.get(f"{LIST_URL}?stage=all").data.decode()
        rows = html[html.index("<tbody>"):].split("<tr>")
        by_name = {n: next(r for r in rows if n in r) for n in ("LUISA PERDIDA", "BETO AUTENTICADO", "ANA APROBADA")}
        assert _stage_badges(by_name["LUISA PERDIDA"]) == ["awaiting_notarization"]
        assert "Falta autenticación" in by_name["LUISA PERDIDA"]
        assert _stage_badges(by_name["BETO AUTENTICADO"]) == ["ready"]
        done_row = by_name["BETO AUTENTICADO"]
        assert "Falta autenticación" not in done_row and "Autenticada" not in done_row
        assert _stage_badges(by_name["ANA APROBADA"]) == ["ready"]

    def test_pending_with_a_loss_card_has_no_notary_badge(self, authenticated_client, db_conn):
        _three_cards_one_flagged(db_conn)
        body = authenticated_client.get(LIST_URL).data.decode().split("<tbody>")[1]
        assert _stage_badges(body) == ["to_review"] and "Falta autenticación" not in body


class TestPdf:
    @pytest.fixture
    def calls(self, monkeypatch) -> list[dict]:
        recorded: list[dict] = []

        def fake(*, declaration, cards, signature_png, issued_at):
            recorded.append({"declaration": declaration, "cards": cards,
                             "signature_png": signature_png, "issued_at": issued_at})
            return b"%PDF-1.4 generated"

        monkeypatch.setattr("app.web.blueprints.declarations.build_declaration_pdf", fake)
        return recorded

    @pytest.mark.parametrize("client_fixture", ["authenticated_client", "punto_venta_client"])
    def test_approved_with_loss_downloads_an_attachment(self, request, db_conn, calls, client_fixture):
        client = request.getfixturevalue(client_fixture)
        declaration_id = _loss_approved(db_conn)
        resp = client.get(f"{_detail(declaration_id)}/pdf")
        assert resp.status_code == 200
        assert resp.mimetype == "application/pdf"
        assert resp.data == b"%PDF-1.4 generated"
        assert resp.headers["Content-Disposition"] == f"attachment; filename=declaracion-{declaration_id}.pdf"
        assert resp.headers["Cache-Control"] == "no-store"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    def test_the_generator_gets_the_declaration_cards_signature_and_a_timestamp(
        self, authenticated_client, db_conn, uploads, calls,
    ):
        declaration_id, _ = _with_files(db_conn, uploads)
        authenticated_client.get(f"{_detail(declaration_id)}/pdf")
        (call,) = calls
        assert call["declaration"].id == declaration_id
        assert [c.id for c in call["cards"]] == [c.id for c in _cards_of(db_conn, declaration_id)]
        assert call["signature_png"] == PNG
        assert abs((datetime.now() - call["issued_at"]).total_seconds()) < 60

    @pytest.mark.parametrize("mode", ["no_path", "missing_file", "traversal"])
    def test_signature_is_none_when_unavailable(self, authenticated_client, db_conn, uploads, calls, mode):
        if mode == "no_path":
            declaration_id = _loss_approved(db_conn, signature_file=None)
        elif mode == "missing_file":
            declaration_id, names = _with_files(db_conn, uploads)
            (uploads / names["signature"]).unlink()
        else:
            (uploads.parent / "secret.png").write_bytes(PNG)
            declaration_id = _loss_approved(db_conn, signature_file="../secret.png")
        assert authenticated_client.get(f"{_detail(declaration_id)}/pdf").status_code == 200
        assert calls[-1]["signature_png"] is None

    def test_real_placeholder_generator_returns_a_pdf(self, authenticated_client, db_conn):
        declaration_id = _loss_approved(db_conn)
        resp = authenticated_client.get(f"{_detail(declaration_id)}/pdf")
        assert resp.status_code == 200 and resp.data.startswith(b"%PDF-")

    def test_pending_with_loss_is_404_with_a_clear_message(self, authenticated_client, db_conn, calls):
        pending_id = _three_cards_one_flagged(db_conn)
        resp = authenticated_client.get(f"{_detail(pending_id)}/pdf")
        assert resp.status_code == 404
        assert "solo está disponible para declaraciones aprobadas" in resp.data.decode()
        assert calls == []

    def test_rejected_is_404(self, authenticated_client, db_conn, calls):
        pending_id = _three_cards_one_flagged(db_conn)
        decl.reject_declaration(db_conn, pending_id, 1, "no")
        assert authenticated_client.get(f"{_detail(pending_id)}/pdf").status_code == 404
        assert calls == []

    def test_approved_deterioration_only_can_also_download_the_pdf(self, authenticated_client, db_conn, calls):
        declaration_id = _approved(db_conn)
        resp = authenticated_client.get(f"{_detail(declaration_id)}/pdf")
        assert resp.status_code == 200 and resp.mimetype == "application/pdf"
        assert len(calls) == 1

    def test_deterioration_detail_shows_an_optional_pdf_button_and_no_notary_block(
        self, authenticated_client, db_conn,
    ):
        declaration_id = _approved(db_conn)
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert 'id="optional-pdf"' in html
        assert f'href="/declaraciones/{declaration_id}/pdf"' in html
        assert "Opcional" in html
        assert "Autenticación notarial" not in html and "/notarized" not in html

    def test_loss_detail_has_no_optional_block(self, authenticated_client, db_conn):
        html = authenticated_client.get(_detail(_loss_approved(db_conn))).data.decode()
        assert 'id="optional-pdf"' not in html

    def test_pending_detail_has_no_optional_block(self, authenticated_client, db_conn):
        html = authenticated_client.get(_detail(_three_cards_one_flagged(db_conn))).data.decode()
        assert 'id="optional-pdf"' not in html

    def test_unknown_declaration_is_404(self, authenticated_client, db_conn, calls):
        assert authenticated_client.get(f"{_detail(9999)}/pdf").status_code == 404


class TestNotarizationDetail:
    def test_pending_block_with_pdf_button_and_mark_form_and_no_create_button(
        self, authenticated_client, db_conn,
    ):
        declaration_id = _loss_approved(db_conn)
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert "Autenticación notarial" in html
        assert "Pendiente." in html
        assert "Descargar PDF para autenticación" in html
        assert f'href="/declaraciones/{declaration_id}/pdf"' in html
        assert "se imprime y se entrega al cliente para que lo autentique en una notaría" in html.lower()
        assert f'action="/declaraciones/{declaration_id}/notarized"' in html
        assert "/notarized/undo" not in html
        assert "Crear pedido" not in html and "declaration_id=" not in html
        assert "Falta la autenticación notarial" in html
        assert _stage_badges(html) == ["awaiting_notarization"]

    def test_received_block_shows_when_who_and_undo_and_restores_the_create_button(
        self, authenticated_client, db_conn,
    ):
        declaration_id = _loss_approved(db_conn)
        authenticated_client.post(f"{_detail(declaration_id)}/notarized")
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert "Autenticación recibida" in html
        assert f"(registrada por {TEST_USERNAME})" in html
        assert f'action="/declaraciones/{declaration_id}/notarized/undo"' in html
        assert f'action="/declaraciones/{declaration_id}/notarized"' not in html
        assert "Falta la autenticación notarial" not in html
        assert "Crear pedido" in html and f'href="/ingest?declaration_id={declaration_id}"' in html
        assert "Descargar PDF para autenticación" in html            # reprint stays available
        assert _stage_badges(html) == ["ready"]

    def test_deterioration_only_has_no_block_and_keeps_the_create_button(self, authenticated_client, db_conn):
        html = authenticated_client.get(_detail(_approved(db_conn))).data.decode()
        assert "Autenticación notarial" not in html and "Descargar PDF para autenticación" not in html
        assert "Crear pedido" in html

    def test_mixed_declaration_is_gated(self, authenticated_client, db_conn):
        declaration_id = _submit_approved(db_conn, _gestor_input(
            cards=[_gcard("ABC123", "L1"), _gcard("DEF456", "L2", reason="PÉRDIDA")],
        ))
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert "Autenticación notarial" in html and "Crear pedido" not in html

    def test_pending_and_rejected_have_no_block(self, authenticated_client, db_conn):
        pending_id = _three_cards_one_flagged(db_conn)
        assert "Autenticación notarial" not in authenticated_client.get(_detail(pending_id)).data.decode()
        decl.reject_declaration(db_conn, pending_id, 1, "no")
        assert "Autenticación notarial" not in authenticated_client.get(_detail(pending_id)).data.decode()

    def test_attended_declaration_hides_the_undo(self, authenticated_client, db_conn):
        declaration_id = _loss_approved(db_conn)
        db.set_declaration_notarized_received(db_conn, declaration_id, received_by=None)
        _attend(db_conn, declaration_id)
        html = authenticated_client.get(_detail(declaration_id)).data.decode()
        assert "Autenticación recibida" in html and "/notarized/undo" not in html
        assert _stage_badges(html) == ["attended"] and "Atendida" in html


class TestNotarizedActions:
    def test_mark_records_the_acting_user_and_redirects_to_the_detail(self, authenticated_client, db_conn):
        declaration_id = _loss_approved(db_conn)
        resp = authenticated_client.post(f"{_detail(declaration_id)}/notarized")
        assert resp.status_code == 302 and resp.headers["Location"].endswith(_detail(declaration_id))
        record = _record(db_conn, declaration_id)
        user = db.get_user_by_username(db_conn, TEST_USERNAME)
        assert user is not None
        assert record.notarized_received_at is not None and record.notarized_received_by == user.id

    def test_marking_twice_keeps_the_first_stamp(self, authenticated_client, punto_venta_client, db_conn):
        declaration_id = _loss_approved(db_conn)
        authenticated_client.post(f"{_detail(declaration_id)}/notarized")
        first = _record(db_conn, declaration_id)
        punto_venta_client.post(f"{_detail(declaration_id)}/notarized")
        second = _record(db_conn, declaration_id)
        assert second.notarized_received_at == first.notarized_received_at
        assert second.notarized_received_by == first.notarized_received_by

    def test_undo_clears_the_stamp(self, authenticated_client, db_conn):
        declaration_id = _loss_approved(db_conn)
        authenticated_client.post(f"{_detail(declaration_id)}/notarized")
        resp = authenticated_client.post(f"{_detail(declaration_id)}/notarized/undo")
        assert resp.status_code == 302
        record = _record(db_conn, declaration_id)
        assert record.notarized_received_at is None and record.notarized_received_by is None

    def test_undo_is_refused_once_an_order_attended_it(self, authenticated_client, db_conn):
        declaration_id = _loss_approved(db_conn)
        authenticated_client.post(f"{_detail(declaration_id)}/notarized")
        _attend(db_conn, declaration_id)
        resp = authenticated_client.post(f"{_detail(declaration_id)}/notarized/undo")
        assert resp.status_code == 400 and "ya fue atendida" in resp.data.decode()
        assert _record(db_conn, declaration_id).notarized_received_at is not None

    @pytest.mark.parametrize("url_suffix", ["notarized", "notarized/undo"])
    def test_not_applicable_declarations_are_refused_and_unchanged(self, authenticated_client, db_conn, url_suffix):
        for declaration_id in (_approved(db_conn), _three_cards_one_flagged(db_conn)):
            resp = authenticated_client.post(f"{_detail(declaration_id)}/{url_suffix}")
            assert resp.status_code == 400
            assert "requieren autenticación notarial" in resp.data.decode()
            assert _record(db_conn, declaration_id).notarized_received_at is None

    @pytest.mark.parametrize("url_suffix", ["notarized", "notarized/undo"])
    def test_unknown_id_redirects_to_list(self, authenticated_client, db_conn, url_suffix):
        resp = authenticated_client.post(f"{_detail(9999)}/{url_suffix}")
        assert resp.status_code == 302 and resp.headers["Location"].endswith(LIST_URL)

    def test_get_is_not_allowed(self, authenticated_client, db_conn):
        declaration_id = _loss_approved(db_conn)
        assert authenticated_client.get(f"{_detail(declaration_id)}/notarized").status_code == 405
        assert _record(db_conn, declaration_id).notarized_received_at is None
