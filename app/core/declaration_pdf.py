"""Render a sworn declaration ("declaración juramentada") as a PDF.

For PÉRDIDA, staff hands the client the very declaration they submitted on the
public form (/declaracion) as a PDF so the client can get it notarized. The
layout follows the paper form (data/DECLARACION_JURAMENTADA_CLIENTE_INGSE_13.docx)
and the legal statements are the ones shown on the public form
(app/web/templates/declaration.html, "legal" block): the constants below are a
copy of that text, kept in sync by tests/core/test_declaration_pdf.py (drift
guard). The personal-data authorization (section 7) is NOT copied: it reads
the same constants as the form (app/core/declarations.py), and it is printed
only when the declarant accepted it (`accepted_data_policy_at`), so older
declarations keep their layout, with "7. FIRMA". Section numbers match the
form, because its clause 4(b) refers to "la sección 3".

Pure function, no Flask and no DB: the caller supplies the records, the
signature PNG bytes (or None) and the issue timestamp. Standard Helvetica is
used, so every user-supplied string is (a) XML-escaped, since reportlab's
Paragraph parses markup, and (b) reduced to characters WinAnsi can print
(accents and ñ are fine; anything else becomes its unaccented form or "?").
"""

from __future__ import annotations

import io
import unicodedata
from datetime import datetime
from typing import Any
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.db.declarations import DeclarationCardRecord, DeclarationRecord
from app.core.declarations import (
    DATA_POLICY_PARAGRAPH_1,
    DATA_POLICY_PARAGRAPH_2,
    DATA_POLICY_TITLE,
    MARKETING_STATEMENT,
)

BOGOTA_TZ = ZoneInfo("America/Bogota")

COMPANY_NAME = "INGSE SAS"
COMPANY_TAGLINE = "NIT 900.773.756-0 · Fabricante autorizado de la Placa Única Nacional para Vehículos"
COMPANY_ADDRESS = "Calle 42 # 41-47, Barrio Fray Luis Amigo, Palmira, Valle del Cauca"
COMPANY_CONTACT = "Tel 3176410267 – 3173712906 · servicioalcliente@ingse.co"
TITLE = "DECLARACIÓN JURAMENTADA"
SUBTITLE = "Solicitud de duplicado de placa(s) por deterioro o pérdida"

# --- Legal text: a copy of the "legal" block of app/web/templates/declaration.html ---
SECTION_DECLARATION = "4. DECLARACIÓN Y AUTORIZACIÓN"
DECLARATION_INTRO = "Manifiesto de manera libre y voluntaria, bajo la gravedad del juramento, que:"
DECLARATION_ITEMS = (
    "Soy propietario o poseedor de lo descrito en la(s) tarjeta(s) de propiedad de este formato.",
    "Autorizo a INGSE SAS (NIT 900.773.756-0) a fabricar y entregar, Duplicado de placa bajo mi "
    "responsabilidad, la(s) placa(s) señalada(s) en la sección 3, idénticas a las que figuran en "
    "dichas tarjetas de propiedad.",
    "Toda la información y los documentos que entrego son verídicos y copia fiel del original, y "
    "autorizo que se verifiquen por cualquier medio.",
)
SECTION_LEGAL_NOTICE = "5. AVISO LEGAL — LEA ANTES DE FIRMAR"
LEGAL_NOTICE_PARAGRAPHS = (
    "Este documento es una declaración jurada con efectos legales. Conforme al artículo 83 de la "
    "Constitución Política de Colombia, sus actuaciones ante INGSE SAS se presumen realizadas de "
    "buena fe, es decir, con información veraz y honesta.",
    "Si la información o los documentos entregados son falsos, alterados o inexactos, usted puede "
    "incurrir en los siguientes delitos, contemplados en el Código Penal Colombiano (Ley 599 de 2000):",
)
LEGAL_NOTICE_OFFENSES = (
    "Falsedad en documento privado (art. 289 C.P.): prisión de 16 a 108 meses.",
    "Falso testimonio (art. 442 C.P.), cuando la declaración jurada se rinde ante autoridad competente "
    "en una actuación administrativa: prisión de 6 a 12 años.",
)
LEGAL_NOTICE_CLOSING = (
    "Al firmar, usted declara que ha leído y entendido este aviso y que la información suministrada es "
    "cierta, real y verificable."
)
SECTION_COMMITMENTS = "6. COMPROMISOS Y RESPONSABILIDAD DEL CLIENTE"
COMMITMENTS = (
    "Los trámites ante el RUNT son de responsabilidad exclusiva del cliente.",
    "Debe entregar las placas deterioradas en INGSE SAS, o enviarlas a la Calle 42 # 41-47, Barrio "
    "Fray Luis Amigo, Palmira.",
)

SECTION_DATA_POLICY = f"7. {DATA_POLICY_TITLE}"
SECTION_SIGNATURE = "FIRMA"

NOTARY_NOTE = (
    "NOTA. Si la placa que se va a fabricar es por PÉRDIDA, este formato debe estar autenticado por Notaría."
)

_REASON_LABELS = {
    "DETERIORO": "DETERIORO (deterioro de la(s) placa(s))",
    "PÉRDIDA": "PÉRDIDA (pérdida o hurto de la(s) placa(s))",
}
_ACTING_AS_OPTIONS = (
    ("PROPIETARIO", "Propietario (tarjeta de propiedad)"),
    ("POSEEDOR", "Poseedor"),
    ("GESTOR", "Gestor"),
)
_RELATION_LABELS = {"DUEÑO": "Dueño", "POSEEDOR": "Poseedor"}

_BLUE = colors.HexColor("#0b4f9c")
_GREY_BG = colors.HexColor("#eef2f8")
_GREY_LINE = colors.HexColor("#9aa7bd")
_GREY_TEXT = colors.HexColor("#555555")

_PAGE_W, _PAGE_H = letter
_MARGIN_X = 48
_CONTENT_W = _PAGE_W - 2 * _MARGIN_X
_LOSS_REASON = "PÉRDIDA"
_SIGNATURE_MAX_W = 230
_SIGNATURE_MAX_H = 80
_NOTARY_BOX_H = 150


# --- Text helpers -----------------------------------------------------------

def _safe_char(ch: str) -> str:
    """`ch` if the standard PDF fonts (WinAnsi/cp1252) can print it, else its
    unaccented base letter, else "?". Control characters become a space."""
    if unicodedata.category(ch)[0] == "C":
        return " "
    try:
        ch.encode("cp1252")
    except UnicodeEncodeError:
        base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        try:
            base.encode("cp1252")
        except UnicodeEncodeError:
            return "?"
        return base or "?"
    return ch


def _clean(value: Any) -> str:
    """User-supplied value -> printable single-line text (unescaped)."""
    if value is None:
        return ""
    return " ".join("".join(_safe_char(ch) for ch in str(value)).split())


def _esc(value: Any) -> str:
    """Printable AND markup-escaped: the only form safe to put in a Paragraph."""
    return escape(_clean(value))


def _text(data: dict[str, Any] | None, key: str) -> str:
    return _clean((data or {}).get(key))


def _format_date(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(BOGOTA_TZ)
    return value.strftime("%d/%m/%Y")


def _document(data: dict[str, Any] | None) -> str:
    doc_type, number = _text(data, "document_type"), _text(data, "document_number")
    return " No. ".join(part for part in (doc_type, number) if part) if number else doc_type


# --- Styles -----------------------------------------------------------------

def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=9, leading=12)
    return {
        "base": base,
        "brand": ParagraphStyle("brand", parent=base, fontName="Helvetica-Bold", fontSize=18, leading=22,
                                alignment=TA_CENTER, textColor=_BLUE),
        "brand_sub": ParagraphStyle("brand_sub", parent=base, fontSize=8, leading=10.5, alignment=TA_CENTER,
                                    textColor=_GREY_TEXT),
        "title": ParagraphStyle("title", parent=base, fontName="Helvetica-Bold", fontSize=15, leading=19,
                                alignment=TA_CENTER, spaceBefore=8),
        "subtitle": ParagraphStyle("subtitle", parent=base, fontSize=10, leading=13, alignment=TA_CENTER,
                                   textColor=_GREY_TEXT),
        "section": ParagraphStyle("section", parent=base, fontName="Helvetica-Bold", fontSize=10, leading=13,
                                  textColor=_BLUE, spaceBefore=12, spaceAfter=5, keepWithNext=1),
        "label": ParagraphStyle("label", parent=base, fontName="Helvetica-Bold", fontSize=8, leading=10,
                                textColor=_GREY_TEXT),
        "value": ParagraphStyle("value", parent=base, fontSize=9, leading=11),
        "card_head": ParagraphStyle("card_head", parent=base, fontName="Helvetica-Bold", fontSize=9,
                                    leading=11, textColor=_BLUE),
        "body": ParagraphStyle("body", parent=base, fontSize=9, leading=12.5, spaceAfter=3),
        "item": ParagraphStyle("item", parent=base, fontSize=9, leading=12.5, leftIndent=18, bulletIndent=4,
                               spaceAfter=2),
        "note": ParagraphStyle("note", parent=base, fontSize=8.5, leading=11, textColor=_GREY_TEXT),
        "caption": ParagraphStyle("caption", parent=base, fontSize=9, leading=11.5, alignment=TA_CENTER),
        "caption_small": ParagraphStyle("caption_small", parent=base, fontSize=8, leading=10,
                                        alignment=TA_CENTER, textColor=_GREY_TEXT),
    }


# --- Page numbering ---------------------------------------------------------

def _numbered_canvas_class(declaration_id: int) -> type[Canvas]:
    """A Canvas that, once every page is known, stamps the header (pages 2+)
    and the footer "Declaración N° X — página i de n" on each page."""

    class NumberedCanvas(Canvas):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._saved_pages: list[dict[str, Any]] = []

        def showPage(self) -> None:
            self._saved_pages.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            total = len(self._saved_pages)
            for number, state in enumerate(self._saved_pages, start=1):
                self.__dict__.update(state)
                self._decorate(number, total)
                super().showPage()
            super().save()

        def _decorate(self, number: int, total: int) -> None:
            self.saveState()
            self.setFillColor(_GREY_TEXT)
            self.setStrokeColor(_GREY_LINE)
            self.setLineWidth(0.5)
            self.setFont("Helvetica", 8)
            if number > 1:
                self.drawString(_MARGIN_X, _PAGE_H - 30, f"{COMPANY_NAME} · {TITLE.capitalize()}")
                self.drawRightString(_PAGE_W - _MARGIN_X, _PAGE_H - 30, f"Declaración N° {declaration_id}")
                self.line(_MARGIN_X, _PAGE_H - 34, _PAGE_W - _MARGIN_X, _PAGE_H - 34)
            self.line(_MARGIN_X, 38, _PAGE_W - _MARGIN_X, 38)
            self.drawString(_MARGIN_X, 26, f"{COMPANY_NAME} · NIT 900.773.756-0")
            self.drawRightString(
                _PAGE_W - _MARGIN_X, 26, f"Declaración N° {declaration_id} — página {number} de {total}"
            )
            self.restoreState()

    return NumberedCanvas


# --- Building blocks --------------------------------------------------------

def _field_table(rows: list[list[tuple[str, str]]], styles: dict[str, ParagraphStyle]) -> Table:
    """Label/value grid. Each row is 1 or 2 (label, value) pairs; a 1-pair row
    spans the whole width. Values are already cleaned (unescaped)."""
    widths = [92, _CONTENT_W / 2 - 92, 92, _CONTENT_W / 2 - 92]
    data: list[list[Any]] = []
    commands: list[tuple[Any, ...]] = [
        ("GRID", (0, 0), (-1, -1), 0.5, _GREY_LINE),
        ("BACKGROUND", (0, 0), (0, -1), _GREY_BG),
        ("BACKGROUND", (2, 0), (2, -1), _GREY_BG),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for r, pairs in enumerate(rows):
        row: list[Any] = []
        for label, value in pairs:
            row.append(Paragraph(escape(label), styles["label"]))
            row.append(Paragraph(escape(value), styles["value"]))
        if len(pairs) == 1:
            row += ["", ""]
            commands.append(("SPAN", (1, r), (3, r)))
            commands.append(("BACKGROUND", (2, r), (2, r), colors.white))
        data.append(row)
    table = Table(data, colWidths=widths)
    table.setStyle(TableStyle(commands))
    return table


def _person_rows(person: dict[str, Any] | None) -> list[list[tuple[str, str]]]:
    return [
        [("Nombre completo", _text(person, "name")), ("Documento de identidad", _document(person))],
        [("Dirección de domicilio", _text(person, "address")), ("Barrio", _text(person, "neighborhood"))],
        [("Municipio", _text(person, "municipality")), ("Teléfono", _text(person, "phone"))],
        [("Correo electrónico", _text(person, "email"))],
    ]


def _acting_as_line(acting_as: str) -> str:
    known = {key for key, _ in _ACTING_AS_OPTIONS}
    parts = [f"[{'X' if key == acting_as else ' '}] {label}" for key, label in _ACTING_AS_OPTIONS]
    line = "      ".join(parts)
    if acting_as and acting_as not in known:
        line += f"      [X] {acting_as}"
    return line


def _card_block(
    card: DeclarationCardRecord, number: int, total: int, acting_as: str,
    styles: dict[str, ParagraphStyle],
) -> Flowable:
    head = Paragraph(f"TARJETA DE PROPIEDAD {number} de {total}", styles["card_head"])
    reason = _clean(card.reason)
    grid = _field_table(
        [
            [("Placa", _clean(card.plate_number)), ("Licencia de tránsito No.", _clean(card.transit_license))],
            [("No. Motor", _clean(card.engine_number)), ("No. Chasis", _clean(card.chassis_number))],
            [("Tipo de vehículo", _clean(card.vehicle_type)), ("Matriculado en", _clean(card.registered_in))],
            [("Marca", _clean(card.vehicle_brand)), ("Cantidad de placas", _clean(card.plate_quantity))],
            [("Motivo de la solicitud", _REASON_LABELS.get(reason, reason))],
        ],
        styles,
    )
    parts: list[Flowable] = [head, Spacer(1, 3), grid]
    owner = card.owner_data or {}
    if acting_as == "GESTOR" and (any(_text(owner, k) for k in owner) or card.owner_relation):
        relation = _clean(card.owner_relation)
        relation_label = _RELATION_LABELS.get(relation, relation)
        title = "Propietario o poseedor de esta tarjeta de propiedad"
        if relation_label:
            title += f" ({relation_label})"
        parts += [Spacer(1, 4), Paragraph(escape(title), styles["card_head"]), Spacer(1, 3),
                  _field_table(_person_rows(owner), styles)]
    parts.append(Spacer(1, 10))
    return KeepTogether(parts)


def _legal_flowables(styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    out: list[Flowable] = [
        Paragraph(escape(SECTION_DECLARATION), styles["section"]),
        Paragraph(escape(DECLARATION_INTRO), styles["body"]),
    ]
    out += [Paragraph(escape(item), styles["item"], bulletText=f"{chr(ord('a') + i)})")
            for i, item in enumerate(DECLARATION_ITEMS)]
    out.append(Paragraph(escape(SECTION_LEGAL_NOTICE), styles["section"]))
    out += [Paragraph(escape(p), styles["body"]) for p in LEGAL_NOTICE_PARAGRAPHS]
    out += [Paragraph(escape(o), styles["item"], bulletText="•") for o in LEGAL_NOTICE_OFFENSES]
    out.append(Paragraph(escape(LEGAL_NOTICE_CLOSING), styles["body"]))
    out.append(Paragraph(escape(SECTION_COMMITMENTS), styles["section"]))
    out += [Paragraph(escape(c), styles["item"], bulletText="•") for c in COMMITMENTS]
    return out


def _marketing_line(accepts_marketing: bool | None) -> str:
    """"[X] SÍ  [ ] NO autorizo ..." -- both boxes empty when not answered."""
    yes, no = ("X" if accepts_marketing is True else " "), ("X" if accepts_marketing is False else " ")
    return f"[{yes}] SÍ      [{no}] NO      {MARKETING_STATEMENT}"


def _data_policy_flowables(
    declaration: DeclarationRecord, styles: dict[str, ParagraphStyle],
) -> list[Flowable]:
    """Section 7: the personal-data authorization, the date the declarant
    accepted it and their SÍ / NO to commercial information."""
    accepted_at = declaration.accepted_data_policy_at
    assert accepted_at is not None, "only called for declarations that accepted it"
    return [
        Paragraph(escape(SECTION_DATA_POLICY), styles["section"]),
        Paragraph(escape(DATA_POLICY_PARAGRAPH_1), styles["body"]),
        Paragraph(escape(DATA_POLICY_PARAGRAPH_2), styles["body"]),
        Paragraph(escape(f"Autorización aceptada el {_format_date(accepted_at)}."), styles["note"]),
        Spacer(1, 3),
        Paragraph(escape(_marketing_line(declaration.accepts_marketing)), styles["body"]),
    ]


def _signature_image(signature_png: bytes | None) -> Image | None:
    """The signature as a flowable scaled to fit, or None if absent/unreadable."""
    if not signature_png:
        return None
    try:
        width, height = ImageReader(io.BytesIO(signature_png)).getSize()
        if width <= 0 or height <= 0:
            return None
        scale = min(_SIGNATURE_MAX_W / width, _SIGNATURE_MAX_H / height, 1.0)
        # mask="auto": the pad's PNG has a transparent background.
        image = Image(io.BytesIO(signature_png), width=width * scale, height=height * scale, mask="auto")
        image.hAlign = "CENTER"
        return image
    except Exception:   # noqa: BLE001 - any decoder failure just means "no image"
        return None


def _signature_block(
    declarant: dict[str, Any] | None, signature_png: bytes | None, acting_as: str,
    section_number: int, styles: dict[str, ParagraphStyle],
) -> Flowable:
    image = _signature_image(signature_png)
    role = "Gestor" if acting_as == "GESTOR" else "Declarante"
    sign_cell: list[Flowable] = [image] if image is not None else [Spacer(1, _SIGNATURE_MAX_H)]
    name_cell = [
        Paragraph(f"<b>Firma del {role.lower()}</b>", styles["caption"]),
        Paragraph(escape(_text(declarant, "name")), styles["caption"]),
        Paragraph(escape(_document(declarant)), styles["caption"]),
    ]
    box = Table([[sign_cell, ""], [name_cell, Paragraph("Huella índice derecho", styles["caption_small"])]],
                colWidths=[_CONTENT_W - 130, 130], rowHeights=[_SIGNATURE_MAX_H + 12, None])
    box.setStyle(TableStyle([
        ("VALIGN", (0, 0), (0, 0), "BOTTOM"),
        ("LINEABOVE", (0, 1), (0, 1), 0.8, colors.black),
        ("BOX", (1, 0), (1, 1), 0.8, colors.black),
        ("VALIGN", (1, 1), (1, 1), "BOTTOM"),
        ("VALIGN", (0, 1), (0, 1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, -1), 12),
        ("RIGHTPADDING", (0, 0), (0, -1), 12),
    ]))
    coverage = (
        "La firma del declarante cubre todas las tarjetas de propiedad de esta declaración."
    )
    return KeepTogether([
        Paragraph(f"{section_number}. {SECTION_SIGNATURE}", styles["section"]),
        Paragraph(escape(coverage), styles["note"]),
        Spacer(1, 6),
        box,
    ])


def _notary_block(styles: dict[str, ParagraphStyle]) -> Flowable:
    header = Paragraph("AUTENTICACIÓN NOTARIAL", styles["card_head"])
    hint = Paragraph(
        "Espacio reservado para la diligencia de presentación personal y autenticación ante Notaría "
        "(sello, firma y fecha).", styles["note"],
    )
    box = Table([[""]], colWidths=[_CONTENT_W], rowHeights=[_NOTARY_BOX_H])
    box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, colors.black)]))
    return KeepTogether([Spacer(1, 16), header, Spacer(1, 2), hint, Spacer(1, 4), box])


# --- Public API -------------------------------------------------------------

def build_declaration_pdf(
    *, declaration: DeclarationRecord, cards: list[DeclarationCardRecord],
    signature_png: bytes | None, issued_at: datetime,
) -> bytes:
    """The declaration as a PDF (bytes starting with `%PDF-`), ready to print
    and take to a notary. Signature omitted (blank signing space) when
    `signature_png` is None or not a readable image."""
    styles = _styles()
    data = declaration.submitted_data or {}
    declarant = data.get("declarant") or {}
    acting_as = _clean(declaration.acting_as or data.get("acting_as"))
    total_cards = len(cards)
    total_plates = sum(card.plate_quantity or 0 for card in cards)

    story: list[Flowable] = [
        Paragraph(escape(COMPANY_NAME), styles["brand"]),
        Paragraph(escape(COMPANY_TAGLINE), styles["brand_sub"]),
        Paragraph(escape(COMPANY_ADDRESS), styles["brand_sub"]),
        Paragraph(escape(COMPANY_CONTACT), styles["brand_sub"]),
        Paragraph(escape(TITLE), styles["title"]),
        Paragraph(escape(SUBTITLE), styles["subtitle"]),
        Spacer(1, 8),
        _field_table(
            [[("Declaración N°", str(declaration.id)), ("Fecha de emisión", _format_date(issued_at))],
             [("Declarada el", _format_date(declaration.created_at)),
              ("Total de placas", str(total_plates))]],
            styles,
        ),
        Spacer(1, 6),
    ]
    if any(_clean(card.reason) == _LOSS_REASON for card in cards):
        story.append(Paragraph(escape(NOTARY_NOTE), styles["note"]))

    story += [
        Paragraph("1. CALIDAD EN LA QUE ACTÚA", styles["section"]),
        Paragraph(escape(_acting_as_line(acting_as)), styles["body"]),
        Paragraph("2. DATOS DEL DECLARANTE" + (" (GESTOR)" if acting_as == "GESTOR" else ""), styles["section"]),
        _field_table(_person_rows(declarant), styles),
        Paragraph("3. TARJETAS DE PROPIEDAD", styles["section"]),
    ]
    story += [_card_block(card, i, total_cards, acting_as, styles) for i, card in enumerate(cards, start=1)]
    story += _legal_flowables(styles)
    signature_section = 7
    if declaration.accepted_data_policy_at is not None:
        story += _data_policy_flowables(declaration, styles)
        signature_section = 8
    story.append(_signature_block(declarant, signature_png, acting_as, signature_section, styles))
    story.append(_notary_block(styles))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter, leftMargin=_MARGIN_X, rightMargin=_MARGIN_X, topMargin=46, bottomMargin=52,
        title=f"Declaración juramentada N° {declaration.id}", author=COMPANY_NAME,
        invariant=True,   # fixed dates / document id: same input -> same bytes
    )
    doc.build(story, canvasmaker=_numbered_canvas_class(declaration.id))
    return buffer.getvalue()
