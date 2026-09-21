"""Cuadre de caja (daily cash-reconciliation report) -- Fase 5b.

The real orchestration logic behind `/cuadre`: aggregating money already
stored on `plates`/`production_orders` into the exact buckets the manual
daily cuadre needs, plus the missing-price warning list. This has real logic
beyond CRUD (grouping, exclusion rules, the "never silently zero a missing
price" rule), so per PLAN_ARQUITECTURA_Y_POS.md's "Fase 5b" section it gets
its own orchestration module instead of living directly in a app/core/db/*.py
file.

**Transaction contract**: `build_cash_summary` takes an already-open `conn`
and never calls commit()/rollback() -- same convention as every function in
app/core/db/ (see app/core/db/__init__.py's module docstring), even though
this module lives outside that package.

**Live-computed, not a saved snapshot**: no new table, no locking -- every
call just runs a query over [desde, hasta]. See the plan's "Cuadre de caja"
section for the full reasoning.

Money stays `decimal.Decimal` end to end (never mixed with float) -- both in
the aggregation and in the PDF export below.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import groupby

import psycopg
from psycopg.rows import DictRow
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# The bucket a NULL production_orders.payment_method falls into. Every
# historical order has payment_method IS NULL today (Fase 3 only started
# requiring it going forward) -- without this explicit bucket, a plain
# GROUP BY would silently hide or drop every one of them. Text is Spanish
# on purpose: it's a label the (Colombian, Spanish-speaking) user reads.
MISSING_PAYMENT_METHOD_LABEL = "SIN FORMA DE PAGO"


@dataclass
class CashSummaryRow:
    """One (order_date, payment_method) bucket's totals.

    `total` is the sum of unit_price * quantity for plates in this bucket
    that DO have a unit_price -- Postgres' SUM() already skips NULLs, so a
    bucket that mixes priced and unpriced plates totals only the priced
    ones. The unpriced ones are never folded in here; they show up
    separately in CashSummary.missing_price instead.
    """

    payment_method: str
    n_plates: int
    total_qty: int
    total: Decimal


@dataclass
class CashSummaryDay:
    """One order_date's buckets, plus that date's own subtotal."""

    order_date: date
    rows: list[CashSummaryRow]
    subtotal: Decimal


@dataclass
class MissingPriceWarning:
    """One plate in range with unit_price IS NULL -- surfaced explicitly so
    it can never be mistaken for a silent 0 in the totals above."""

    order_id: int
    order_number: str
    plate_type: str


@dataclass
class CashSummary:
    days: list[CashSummaryDay]
    missing_price: list[MissingPriceWarning]
    grand_total: Decimal


def build_cash_summary(conn: psycopg.Connection[DictRow], *, desde: date, hasta: date) -> CashSummary:
    """Aggregate paid production for [desde, hasta] (both inclusive dates),
    grouped by `order_date` then `payment_method` -- see the module docstring
    and PLAN_ARQUITECTURA_Y_POS.md's "Cuadre de caja" section for the full
    reasoning behind every rule enforced here:

    - Always grouped by order_date, never payment_date/fecha de transferencia.
    - COALESCE(payment_method, 'SIN FORMA DE PAGO') is mandatory (see
      MISSING_PAYMENT_METHOD_LABEL).
    - is_voided (never produced) and is_plate_annulled (blank wasted, never
      charged) plates are excluded from every total.
    - A plate with unit_price IS NULL is NEVER coalesced to 0 -- it's
      dropped from `total` (Postgres SUM() skips NULLs) and surfaced
      separately, in full, via `missing_price`.
    """
    if desde > hasta:
        raise ValueError("La fecha 'desde' no puede ser posterior a la fecha 'hasta'.")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.order_date,
                   COALESCE(o.payment_method, %(missing_label)s) AS payment_method,
                   count(*) AS n_plates,
                   COALESCE(SUM(p.quantity), 0) AS total_qty,
                   SUM(p.unit_price * p.quantity) AS total
            FROM plates p
            JOIN production_orders o ON o.id = p.order_id
            WHERE o.order_date BETWEEN %(desde)s AND %(hasta)s
              AND p.is_voided = false
              AND p.is_plate_annulled = false
            GROUP BY o.order_date, COALESCE(o.payment_method, %(missing_label)s)
            ORDER BY o.order_date, 2
            """,
            {"missing_label": MISSING_PAYMENT_METHOD_LABEL, "desde": desde, "hasta": hasta},
        )
        grouped = cur.fetchall()

        cur.execute(
            """
            SELECT o.id AS order_id, o.order_number, p.plate_type
            FROM plates p
            JOIN production_orders o ON o.id = p.order_id
            WHERE o.order_date BETWEEN %(desde)s AND %(hasta)s
              AND p.is_voided = false
              AND p.is_plate_annulled = false
              AND p.unit_price IS NULL
            ORDER BY o.order_date, o.order_number, p.id
            """,
            {"desde": desde, "hasta": hasta},
        )
        missing_rows = cur.fetchall()

    days: list[CashSummaryDay] = []
    grand_total = Decimal("0")
    for order_date_value, day_rows in groupby(grouped, key=lambda r: r["order_date"]):
        rows: list[CashSummaryRow] = []
        subtotal = Decimal("0")
        for r in day_rows:
            total = r["total"] if r["total"] is not None else Decimal("0")
            rows.append(
                CashSummaryRow(
                    payment_method=r["payment_method"],
                    n_plates=r["n_plates"],
                    total_qty=r["total_qty"],
                    total=total,
                )
            )
            subtotal += total
        days.append(CashSummaryDay(order_date=order_date_value, rows=rows, subtotal=subtotal))
        grand_total += subtotal

    missing_price = [
        MissingPriceWarning(order_id=r["order_id"], order_number=r["order_number"], plate_type=r["plate_type"])
        for r in missing_rows
    ]

    return CashSummary(days=days, missing_price=missing_price, grand_total=grand_total)


def _cop(value: Decimal) -> str:
    """Colombian peso display formatting for the PDF export: thousands
    separated by '.', no decimals (e.g. Decimal('1954000') -> '$1.954.000').

    Deliberately duplicated (not imported) from app/web/helpers.format_cop's
    formula -- app/core/ never imports from app/web/ (core is the layer web
    depends on, not the other way around; see docs/CORE.md), and this
    module's PDF rendering is otherwise pure/framework-free, so a one-line
    local copy is simpler than introducing a shared formatting module for a
    single formula. Both must stay in sync if the format ever changes.
    """
    return f"${value:,.0f}".replace(",", ".")


def render_cash_summary_pdf(summary: CashSummary, *, desde: date, hasta: date) -> bytes:
    """Render `summary` as a simple PDF table of totals, for PTO.V to print
    and hand the admin (see the plan's "Exportar a PDF" requirement).

    Deliberately simple -- one table row per (date, payment_method) bucket
    plus a grand-total row, and (if any) a second table listing the
    missing-price warnings -- no logos, no charts, mirrors the HTML view.
    Uses the same reportlab building blocks (SimpleDocTemplate + Table/
    TableStyle, portrait `letter` page size) as tests/pdf_fixtures.py's
    synthetic pedido PDFs, for consistency within the codebase.
    """
    styles = getSampleStyleSheet()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36
    )

    elements: list[object] = [
        Paragraph(f"Cuadre de caja: {desde.isoformat()} a {hasta.isoformat()}", styles["Title"]),
        Spacer(1, 12),
    ]

    header = ["Fecha", "Forma de pago", "Placas", "Cantidad", "Total"]
    data: list[list[str]] = [header]
    for day in summary.days:
        for row in day.rows:
            data.append(
                [
                    day.order_date.isoformat(),
                    row.payment_method,
                    str(row.n_plates),
                    str(row.total_qty),
                    _cop(row.total),
                ]
            )
    data.append(["", "", "", "TOTAL", _cop(summary.grand_total)])

    table_style = TableStyle(
        [
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ]
    )
    elements.append(Table(data, style=table_style))

    if summary.missing_price:
        elements.append(Spacer(1, 18))
        elements.append(Paragraph("Placas sin precio (no incluidas en el total)", styles["Heading3"]))
        warn_header = ["Orden", "Tipo de placa"]
        warn_data: list[list[str]] = [warn_header] + [
            [w.order_number, w.plate_type] for w in summary.missing_price
        ]
        warn_style = TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
        elements.append(Table(warn_data, style=warn_style))

    doc.build(elements)
    return buffer.getvalue()
