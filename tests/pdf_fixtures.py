"""Build synthetic "pedido" PDFs for the pdf_extract tests.

The two REAL sample pedidos are gitignored business data that won't exist in
CI or a fresh clone, so no test may read them. Instead this generates a PDF
with reportlab whose table geometry mirrors the real document closely enough
to exercise the parser end to end: same 14-column plate table, same
'LABEL:' header fields (including a PEDIDO # decoy that must never be mistaken
for the production order), same TOTAL PEDIDO terminator and footer noise.

All data here is invented. Never copy values from the real samples into this
file — it is committed.
"""

from __future__ import annotations

import io
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

# The plate table's columns, in the real document's order.
COLUMNS = [
    "ITEM", "PLACA", "CIUDAD", "CANT.",
    "PARTICULAR", "PÚBLICA", "MOTO", "MOTOCARRO\nPART.", "MOTOCARRO\nPÚB.", "REMOLQUE",
    "Rango Inicial", "Rango Final", "VALOR PLACA", "VALOR\nTOTAL",
]
_WIDTHS = [26, 58, 62, 26, 52, 46, 40, 60, 58, 50, 56, 56, 48, 50]
_N = len(COLUMNS)

# Checkbox column name (as passed in a line spec) -> its column index.
CHECKBOX_INDEX = {
    "PARTICULAR": 4, "PÚBLICA": 5, "MOTO": 6,
    "MOTOCARRO PART.": 7, "MOTOCARRO PÚB.": 8, "REMOLQUE": 9,
}

# Mirrors the real samples: a single-plate line, a line whose ITEM number is
# missing (a real gap in the source document), a city+quantity line, a
# consecutive-range line, and a pre-printed empty line that has only an ITEM.
DEFAULT_LINES: list[dict[str, str]] = [
    {"item": "1", "placa": "ABC12D", "tipo": "MOTO"},
    {"item": "", "placa": "EFG34H", "tipo": "MOTO"},
    {"item": "3", "placa": "PUB100", "ciudad": "CIUDAD DEMO", "cant": "2", "tipo": "PÚBLICA"},
    {"item": "4", "rango_ini": "RNG001", "rango_fin": "RNG009", "cant": "9", "tipo": "PARTICULAR"},
    {"item": "5"},
]


def _blank_row() -> list[str]:
    return [""] * _N


def _field_row(label: str, value: str) -> list[str]:
    row = _blank_row()
    row[0] = label
    row[4] = value
    return row


def _data_row(spec: dict[str, str]) -> list[str]:
    row = _blank_row()
    row[0] = spec.get("item", "")
    row[1] = spec.get("placa", "")
    row[2] = spec.get("ciudad", "")
    row[3] = spec.get("cant", "")
    row[10] = spec.get("rango_ini", "")
    row[11] = spec.get("rango_fin", "")
    tipo = spec.get("tipo")
    if tipo:
        row[CHECKBOX_INDEX[tipo]] = spec.get("marca", "X")
    return row


def build_pedido_pdf(
    *,
    order_number: str = "9001",
    client: str = "CLIENTE DEMO",
    order_date: str = "11/08/2026",
    pedido_numero: str = "77",
    order_label: str = "ORDEN DE\nPRODUCCION:",
    order_row_leaks_bare_pedido_label: bool = False,
    lines: list[dict[str, str]] | None = None,
) -> bytes:
    """Render a pedido PDF and return its bytes.

    `order_date` is written verbatim, so tests can exercise every date format
    the real documents use. `pedido_numero` is the client-facing claim number
    that must NEVER be parsed as the production order number. `order_label`
    lets tests reproduce the older template generation, which called the same
    field 'NT:'. `order_row_leaks_bare_pedido_label` reproduces a real layout
    where a colon-less 'PEDIDO #' sits later in the SAME row as the order
    label -- confirmed happening for real, see pdf_extract._LABEL_PEDIDO_HASH_BARE.
    """
    specs = DEFAULT_LINES if lines is None else lines

    order_row = _field_row(order_label, order_number)
    if order_row_leaks_bare_pedido_label:
        order_row[6] = "PEDIDO #"

    table: list[list[Any]] = [
        _field_row("CLIENTE:", client),
        _field_row("FECHA PEDIDO:", order_date),
        order_row,
        _field_row(f"PEDIDO #: {pedido_numero}", ""),
        list(COLUMNS),
        *[_data_row(s) for s in specs],
    ]
    total_row = _blank_row()
    total_row[0] = "TOTAL PEDIDO:"
    table.append(total_row)
    for footer in ("RESPONSABLE DESPACHO (INGSE SAS):", "FORMA DE PAGO:", "OBSERVACIONES:"):
        row = _blank_row()
        row[0] = footer
        table.append(row)

    header_field_rows = range(4)  # the 'LABEL: value' rows above the plate table
    style: list[Any] = [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("FONTSIZE", (0, 0), (-1, -1), 6),
        ("LEADING", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    # Merge like the real form does: label across the left block, value across
    # the middle. This also reproduces the real tables' many `None` cells.
    for r in header_field_rows:
        style.append(("SPAN", (0, r), (3, r)))
        style.append(("SPAN", (4, r), (9, r)))
    last = len(table) - 1
    for r in range(last - 3, last + 1):  # TOTAL PEDIDO + footer rows span the width
        style.append(("SPAN", (0, r), (-1, r)))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(letter),
        leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36,
    )
    doc.build([Table(table, colWidths=_WIDTHS, style=TableStyle(style))])
    return buffer.getvalue()


def build_pdf_without_table(text: str = "Esto no es un pedido.") -> bytes:
    """A valid PDF with no table at all — the 'wrong document' case."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    doc.build([Paragraph(text, getSampleStyleSheet()["Normal"])])
    return buffer.getvalue()


def build_pdf_with_unrelated_table() -> bytes:
    """A PDF that HAS a table, just not a pedido's plate table.

    Distinct from `build_pdf_without_table`: this one gets past "did we find any
    table at all" and must be rejected by the ITEM/PLACA header check instead.
    """
    data = [
        ["FECHA", "CONCEPTO", "VALOR"],
        ["01/01/2026", "Servicio de mantenimiento", "100.000"],
        ["02/01/2026", "Transporte", "50.000"],
    ]
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    doc.build([Table(data, style=TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))])
    return buffer.getvalue()
