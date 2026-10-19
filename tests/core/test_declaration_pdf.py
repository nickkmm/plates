"""Tests for app.core.declaration_pdf.build_declaration_pdf.

Pure function (no DB): the records are built in memory. Text is checked by
extracting it back with pdfplumber, whitespace-normalized because the PDF wraps
lines.
"""

from __future__ import annotations

import io
import re
import struct
import zlib
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pdfplumber

from app.core.db.declarations import DeclarationCardRecord, DeclarationRecord
from app.core.declaration_pdf import build_declaration_pdf

TEMPLATE = Path(__file__).resolve().parents[2] / "app" / "web" / "templates" / "declaration.html"
ISSUED_AT = datetime(2026, 10, 19, 14, 30)


def _declarant(**overrides: str) -> dict[str, str]:
    data = {
        "name": "María José Pérez Gómez", "document_type": "CÉDULA", "document_number": "1.234.567.890",
        "address": "Carrera 10 # 20-30", "neighborhood": "El Prado", "municipality": "Palmira",
        "phone": "3001112233", "email": "maria@example.com",
    }
    data.update(overrides)
    return data


def _declaration(
    *, acting_as: str = "PROPIETARIO", declarant: dict[str, str] | None = None,
    cards_data: list[dict[str, Any]] | None = None,
) -> DeclarationRecord:
    return DeclarationRecord(
        id=57, created_at=datetime(2026, 10, 18, 9, 5), acting_as=acting_as, gestor_id=None,
        status="pending_review", review_flags=[], reviewed_by=None, reviewed_at=None, review_note=None,
        submitted_data={"acting_as": acting_as, "declarant": declarant or _declarant(),
                        "cards": cards_data or []},
        notarized_pdf_path=None, submitter_ip=None, user_agent=None, accepted_terms_at=None,
        declarant_signature_path=None, production_order_id=None, notarized_received_at=None,
        notarized_received_by=None,
    )


def _card(position: int = 0, **overrides: Any) -> DeclarationCardRecord:
    values: dict[str, Any] = {
        "id": position + 1, "declaration_id": 57, "position": position, "reason": "PÉRDIDA",
        "plate_quantity": 2, "plate_number": f"ABC{position:03d}", "transit_license": f"LT-{10000 + position}",
        "engine_number": f"MOT{position}", "chassis_number": f"CHA{position}",
        "vehicle_type": "CARRO PARTICULAR", "registered_in": "PALMIRA", "vehicle_brand": "MAZDA",
        "owner_relation": None, "owner_data": {}, "customer_id": None, "ownership_card_id": None,
        "review_flags": [],
    }
    values.update(overrides)
    return DeclarationCardRecord(**values)


def _png(width: int = 60, height: int = 24) -> bytes:
    """A tiny valid RGBA PNG (transparent with a black diagonal), no Pillow needed."""
    raw = b""
    for y in range(height):
        row = bytearray()
        for x in range(width):
            on = abs(x * height // width - y) <= 1
            row += bytes((0, 0, 0, 255)) if on else bytes((0, 0, 0, 0))
        raw += b"\x00" + bytes(row)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _build(declaration: DeclarationRecord, cards: list[DeclarationCardRecord],
           signature: bytes | None = None) -> bytes:
    return build_declaration_pdf(declaration=declaration, cards=cards, signature_png=signature,
                                 issued_at=ISSUED_AT)


def _pages_text(pdf: bytes) -> list[str]:
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        return [" ".join((page.extract_text() or "").split()) for page in doc.pages]


def _text(pdf: bytes) -> str:
    return " ".join(_pages_text(pdf))


def _image_count(pdf: bytes) -> int:
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        return sum(len(page.images) for page in doc.pages)


def test_basic_content() -> None:
    cards = [_card(0, reason="PÉRDIDA"), _card(1, reason="DETERIORO", plate_quantity=1)]
    pdf = _build(_declaration(), cards)
    assert pdf.startswith(b"%PDF-")
    text = _text(pdf)
    assert "INGSE SAS" in text and "DECLARACIÓN JURAMENTADA" in text
    assert "Declaración N° 57" in text
    assert "19/10/2026" in text and "18/10/2026" in text
    assert "María José Pérez Gómez" in text and "1.234.567.890" in text
    assert "Carrera 10 # 20-30" in text and "El Prado" in text and "Palmira" in text
    assert "3001112233" in text and "maria@example.com" in text
    assert "[X] Propietario" in text and "[ ] Poseedor" in text
    for card in cards:
        assert card.plate_number in text
        assert card.transit_license in text
        assert card.engine_number in text and card.chassis_number in text
        assert card.reason in text
    assert "TARJETA DE PROPIEDAD 1 de 2" in text and "TARJETA DE PROPIEDAD 2 de 2" in text
    assert "AUTENTICACIÓN NOTARIAL" in text
    assert "debe estar autenticado por Notaría" in text   # at least one PÉRDIDA
    assert "Huella índice derecho" in text


def test_legal_statements_present() -> None:
    text = _text(_build(_declaration(), [_card()]))
    for phrase in (
        "bajo la gravedad del juramento",
        "artículo 83 de la Constitución Política de Colombia",
        "Falsedad en documento privado (art. 289 C.P.): prisión de 16 a 108 meses",
        "Falso testimonio (art. 442 C.P.)",
        "prisión de 6 a 12 años",
        "Los trámites ante el RUNT son de responsabilidad exclusiva del cliente",
        "la sección 3",
    ):
        assert phrase in text


def test_no_notary_note_without_loss() -> None:
    text = _text(_build(_declaration(), [_card(reason="DETERIORO")]))
    assert "debe estar autenticado por Notaría" not in text


def test_footer_and_paging_for_30_cards() -> None:
    cards = [_card(i, reason="PÉRDIDA" if i % 2 else "DETERIORO") for i in range(30)]
    pdf = _build(_declaration(), cards)
    pages = _pages_text(pdf)
    assert len(pages) > 1
    for i, page in enumerate(pages, start=1):
        assert f"Declaración N° 57 — página {i} de {len(pages)}" in page
    text = " ".join(pages)
    for card in cards:
        assert card.plate_number in text and card.transit_license in text
    # Signature block (title + declarant name under it) stays on one page.
    sign_pages = [p for p in pages if "7. FIRMA" in p]
    assert len(sign_pages) == 1
    assert "Firma del declarante" in sign_pages[0]
    assert "María José Pérez Gómez" in sign_pages[0].split("7. FIRMA", 1)[1]
    assert "AUTENTICACIÓN NOTARIAL" in text


def test_nothing_overflows_the_page() -> None:
    long_word = "X" * 200
    cards = [_card(i, vehicle_brand=long_word, registered_in="CIUDAD " * 40) for i in range(3)]
    pdf = _build(_declaration(declarant=_declarant(address=long_word)), cards)
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        for page in doc.pages:
            for word in page.extract_words():
                assert word["x0"] >= 0 and word["x1"] <= page.width
                assert word["bottom"] <= page.height


def test_none_signature_works() -> None:
    pdf = _build(_declaration(), [_card()], signature=None)
    assert pdf.startswith(b"%PDF-")
    assert _image_count(pdf) == 0
    assert "Firma del declarante" in _text(pdf)


def test_unreadable_signature_is_omitted() -> None:
    pdf = _build(_declaration(), [_card()], signature=b"this is not a png")
    assert pdf.startswith(b"%PDF-")
    assert _image_count(pdf) == 0


def test_real_png_signature_is_embedded() -> None:
    pdf = _build(_declaration(), [_card()], signature=_png())
    assert pdf.startswith(b"%PDF-")
    assert _image_count(pdf) == 1


def test_special_characters_render() -> None:
    declarant = _declarant(name="Ñandú & Hijos <S.A.>", address="Calle \"5\" & <6>")
    cards = [_card(0, vehicle_brand="O'Neil", registered_in="José <b>Cali</b> & Co", plate_number="ÑAN123")]
    pdf = _build(_declaration(declarant=declarant), cards, signature=_png())
    text = _text(pdf)
    assert "Ñandú & Hijos <S.A.>" in text
    assert "O'Neil" in text
    assert "José <b>Cali</b> & Co" in text   # markup shown literally, never interpreted
    assert 'Calle "5" & <6>' in text
    assert "ÑAN123" in text


def test_characters_outside_latin1_do_not_break() -> None:
    declarant = _declarant(name="Łukasz Żółć 😀\x00\n tab\t")
    pdf = _build(_declaration(declarant=declarant), [_card()])
    assert pdf.startswith(b"%PDF-")
    assert "ukasz" in _text(pdf)


def test_gestor_declaration_with_per_card_owners() -> None:
    owner_a = _declarant(name="Carlos Cliente Uno", document_number="111222333")
    owner_b = _declarant(name="Ana Cliente Dos", document_number="444555666", document_type="NIT")
    cards = [
        _card(0, owner_data=owner_a, owner_relation="DUEÑO"),
        _card(1, owner_data=owner_b, owner_relation="POSEEDOR", reason="DETERIORO"),
    ]
    declaration = _declaration(acting_as="GESTOR", declarant=_declarant(name="Gestiones Rápidas SAS"))
    text = _text(_build(declaration, cards, signature=_png()))
    assert "[X] Gestor" in text and "[ ] Poseedor" in text
    assert "Gestiones Rápidas SAS" in text
    assert "Carlos Cliente Uno" in text and "111222333" in text
    assert "Ana Cliente Dos" in text and "444555666" in text
    assert "(Dueño)" in text and "(Poseedor)" in text


def test_owner_block_not_shown_when_declarant_is_owner() -> None:
    cards = [_card(0, owner_data=_declarant(name="Otro Nombre"), owner_relation="DUEÑO")]
    text = _text(_build(_declaration(acting_as="PROPIETARIO"), cards))
    assert "Otro Nombre" not in text


def test_missing_optional_data_and_no_cards() -> None:
    declaration = _declaration(declarant={"name": "Solo Nombre"})
    cards = [_card(0, engine_number=None, chassis_number=None, vehicle_type=None, registered_in=None,
                   vehicle_brand=None)]
    text = _text(_build(declaration, cards))
    assert "Solo Nombre" in text
    assert _build(declaration, []).startswith(b"%PDF-")


def test_naive_and_aware_issue_dates() -> None:
    from zoneinfo import ZoneInfo
    declaration = _declaration()
    aware = datetime(2026, 10, 20, 3, 0, tzinfo=ZoneInfo("UTC"))   # still 19/10 in Bogota
    pdf = build_declaration_pdf(declaration=declaration, cards=[_card()], signature_png=None, issued_at=aware)
    assert "Fecha de emisión 19/10/2026" in _text(pdf)


def test_output_is_deterministic() -> None:
    args = (_declaration(), [_card(0), _card(1)])
    assert _build(*args, signature=_png()) == _build(*args, signature=_png())


# --- Drift guard: the legal text on the public form == the text in the PDF ---

class _LegalBlockParser(HTMLParser):
    """Collects the text of every h3 / p / li inside <div class="legal">."""

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.current: list[str] | None = None
        self.items: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.depth:
            if tag == "div":
                self.depth += 1
            if tag in ("h3", "p", "li"):
                self.current = []
        elif tag == "div" and "legal" in (dict(attrs).get("class") or "").split():
            self.depth = 1

    def handle_endtag(self, tag: str) -> None:
        if not self.depth:
            return
        if tag in ("h3", "p", "li") and self.current is not None:
            self.items.append(" ".join("".join(self.current).split()))
            self.current = None
        elif tag == "div":
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth and self.current is not None:
            self.current.append(data)


def test_legal_text_matches_public_form() -> None:
    parser = _LegalBlockParser()
    parser.feed(TEMPLATE.read_text(encoding="utf-8"))
    assert len(parser.items) >= 12, "legal block not found / parsed in declaration.html"
    text = _text(_build(_declaration(), [_card()]))
    missing = [item for item in parser.items if item and item not in text]
    assert not missing, f"legal text in declaration.html not found in the PDF: {missing}"
    # And the other direction for the key phrases (both sides carry them).
    html = " ".join(re.sub(r"<[^>]+>", " ", TEMPLATE.read_text(encoding="utf-8")).split())
    for phrase in ("bajo la gravedad del juramento", "artículo 83 de la Constitución Política",
                   "Falsedad en documento privado (art. 289 C.P.)", "Falso testimonio (art. 442 C.P.)"):
        assert phrase in html and phrase in text
