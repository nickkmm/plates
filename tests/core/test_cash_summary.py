"""Unit/integration tests for app.core.cash_summary (Fase 5b, /cuadre).

The contract under test -- see PLAN_ARQUITECTURA_Y_POS.md's "Cuadre de caja"
section for the full reasoning behind each rule:
- Always grouped by order_date, never payment_date.
- COALESCE(payment_method, 'SIN FORMA DE PAGO') is mandatory -- every
  historical order has payment_method IS NULL today.
- is_voided/is_plate_annulled plates are excluded from every total.
- A NULL unit_price is NEVER coalesced to 0 -- it's dropped from the
  bucket's total and surfaced separately via missing_price.
- Decimal end to end, never float.
- render_cash_summary_pdf produces a real, non-empty PDF.

Uses db.insert_order/db.insert_plate directly (bypassing app.core.ingestion),
same convention as tests/core/db/ -- a failure here should point at
cash_summary.py's aggregation, not at ingestion or normalization.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

import pdfplumber
import pytest

from app.core import db
from app.core.cash_summary import (
    MISSING_PAYMENT_METHOD_LABEL,
    build_cash_summary,
    render_cash_summary_pdf,
)
from app.core.models import ExtractedRow


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
    row = _make_row(**overrides)
    return db.insert_order(conn, row=row, customer_id=None, created_by=None)


def _insert_plate(conn, order_id: int, **overrides: object) -> None:
    row = _make_row(**overrides)
    db.insert_plate(conn, order_id=order_id, row=row, is_possible_duplicate=False)


class TestGrouping:
    def test_groups_by_order_date_then_payment_method(self, db_conn):
        order1 = _insert_order(
            db_conn, order_number="1", order_date=date(2026, 1, 1), payment_method="EFECTIVO"
        )
        _insert_plate(db_conn, order1, unit_price=Decimal("10000"), quantity=2)

        order2 = _insert_order(
            db_conn, order_number="2", order_date=date(2026, 1, 1), payment_method="TRANSFERENCIA"
        )
        _insert_plate(db_conn, order2, unit_price=Decimal("5000"), quantity=1)

        order3 = _insert_order(
            db_conn, order_number="3", order_date=date(2026, 1, 2), payment_method="EFECTIVO"
        )
        _insert_plate(db_conn, order3, unit_price=Decimal("7000"), quantity=1)

        summary = build_cash_summary(db_conn, desde=date(2026, 1, 1), hasta=date(2026, 1, 2))

        assert [d.order_date for d in summary.days] == [date(2026, 1, 1), date(2026, 1, 2)]
        day1 = summary.days[0]
        methods = {r.payment_method: r for r in day1.rows}
        assert set(methods) == {"EFECTIVO", "TRANSFERENCIA"}
        assert methods["EFECTIVO"].total == Decimal("20000")
        assert methods["EFECTIVO"].total_qty == 2
        assert methods["EFECTIVO"].n_plates == 1
        assert methods["TRANSFERENCIA"].total == Decimal("5000")
        assert day1.subtotal == Decimal("25000")

        day2 = summary.days[1]
        assert day2.rows[0].payment_method == "EFECTIVO"
        assert day2.rows[0].total == Decimal("7000")
        assert day2.subtotal == Decimal("7000")

        assert summary.grand_total == Decimal("32000")

    def test_two_orders_same_date_and_payment_method_combine_into_one_bucket(self, db_conn):
        order1 = _insert_order(db_conn, order_number="1", payment_method="EFECTIVO")
        _insert_plate(db_conn, order1, unit_price=Decimal("1000"), quantity=1)
        order2 = _insert_order(db_conn, order_number="2", payment_method="EFECTIVO")
        _insert_plate(db_conn, order2, unit_price=Decimal("2000"), quantity=1)

        summary = build_cash_summary(db_conn, desde=date(2026, 1, 1), hasta=date(2026, 1, 1))

        assert len(summary.days) == 1
        assert len(summary.days[0].rows) == 1
        assert summary.days[0].rows[0].total == Decimal("3000")
        assert summary.days[0].rows[0].n_plates == 2

    def test_date_range_excludes_orders_outside_it(self, db_conn):
        inside = _insert_order(db_conn, order_number="1", order_date=date(2026, 1, 5))
        _insert_plate(db_conn, inside, unit_price=Decimal("1000"))
        before = _insert_order(db_conn, order_number="2", order_date=date(2026, 1, 4))
        _insert_plate(db_conn, before, unit_price=Decimal("1000"))
        after = _insert_order(db_conn, order_number="3", order_date=date(2026, 1, 6))
        _insert_plate(db_conn, after, unit_price=Decimal("1000"))

        summary = build_cash_summary(db_conn, desde=date(2026, 1, 5), hasta=date(2026, 1, 5))

        assert len(summary.days) == 1
        assert summary.days[0].order_date == date(2026, 1, 5)
        assert summary.grand_total == Decimal("1000")

    def test_desde_after_hasta_raises_value_error(self, db_conn):
        with pytest.raises(ValueError, match="desde"):
            build_cash_summary(db_conn, desde=date(2026, 1, 2), hasta=date(2026, 1, 1))

    def test_empty_range_returns_empty_summary(self, db_conn):
        summary = build_cash_summary(db_conn, desde=date(2026, 1, 1), hasta=date(2026, 1, 1))
        assert summary.days == []
        assert summary.missing_price == []
        assert summary.grand_total == Decimal("0")


class TestSinFormaDePago:
    def test_null_payment_method_buckets_as_sin_forma_de_pago(self, db_conn):
        """Every historical order has payment_method IS NULL -- without this
        bucket a plain GROUP BY would silently hide/drop them all."""
        order = _insert_order(db_conn, order_number="1", payment_method=None)
        _insert_plate(db_conn, order, unit_price=Decimal("1000"), quantity=1)

        summary = build_cash_summary(db_conn, desde=date(2026, 1, 1), hasta=date(2026, 1, 1))

        assert len(summary.days) == 1
        assert len(summary.days[0].rows) == 1
        assert summary.days[0].rows[0].payment_method == MISSING_PAYMENT_METHOD_LABEL
        assert summary.days[0].rows[0].total == Decimal("1000")

    def test_null_and_real_payment_methods_stay_in_separate_buckets(self, db_conn):
        order1 = _insert_order(db_conn, order_number="1", payment_method=None)
        _insert_plate(db_conn, order1, unit_price=Decimal("1000"))
        order2 = _insert_order(db_conn, order_number="2", payment_method="EFECTIVO")
        _insert_plate(db_conn, order2, unit_price=Decimal("2000"))

        summary = build_cash_summary(db_conn, desde=date(2026, 1, 1), hasta=date(2026, 1, 1))

        methods = {r.payment_method for r in summary.days[0].rows}
        assert methods == {MISSING_PAYMENT_METHOD_LABEL, "EFECTIVO"}


class TestExclusions:
    def test_voided_plate_excluded_from_totals(self, db_conn):
        order = _insert_order(db_conn, order_number="1")
        _insert_plate(db_conn, order, unit_price=Decimal("1000"), quantity=1)
        _insert_plate(db_conn, order, unit_price=Decimal("999999"), quantity=1, is_voided=True)

        summary = build_cash_summary(db_conn, desde=date(2026, 1, 1), hasta=date(2026, 1, 1))

        assert summary.grand_total == Decimal("1000")
        assert summary.days[0].rows[0].n_plates == 1

    def test_plate_annulled_excluded_from_totals(self, db_conn):
        order = _insert_order(db_conn, order_number="1")
        _insert_plate(db_conn, order, unit_price=Decimal("1000"), quantity=1)
        _insert_plate(db_conn, order, unit_price=Decimal("999999"), quantity=1, is_plate_annulled=True)

        summary = build_cash_summary(db_conn, desde=date(2026, 1, 1), hasta=date(2026, 1, 1))

        assert summary.grand_total == Decimal("1000")
        assert summary.days[0].rows[0].n_plates == 1

    def test_voided_and_annulled_plate_never_generates_a_missing_price_warning_either(self, db_conn):
        """Excluded plates are excluded everywhere, not just from the total --
        a voided/annulled plate with no price must not clutter the warning
        list either (nobody needs to price a plate that was never sold)."""
        order = _insert_order(db_conn, order_number="1")
        _insert_plate(db_conn, order, unit_price=None, is_voided=True)
        _insert_plate(db_conn, order, unit_price=None, is_plate_annulled=True)

        summary = build_cash_summary(db_conn, desde=date(2026, 1, 1), hasta=date(2026, 1, 1))

        assert summary.missing_price == []
        assert summary.days == []


class TestMissingPrice:
    def test_null_unit_price_generates_a_warning_and_is_not_counted_as_zero(self, db_conn):
        order = _insert_order(db_conn, order_number="42")
        _insert_plate(db_conn, order, unit_price=None, plate_type="MOTO PARTICULAR", quantity=1)

        summary = build_cash_summary(db_conn, desde=date(2026, 1, 1), hasta=date(2026, 1, 1))

        # The bucket still appears (n_plates counts it), but contributes 0 to
        # total -- never a fabricated non-zero value, and the plate is
        # visible in `missing_price`, not silently absorbed.
        assert len(summary.days) == 1
        assert summary.days[0].rows[0].total == Decimal("0")
        assert summary.days[0].rows[0].n_plates == 1
        assert summary.grand_total == Decimal("0")

        assert len(summary.missing_price) == 1
        warning = summary.missing_price[0]
        assert warning.order_number == "42"
        assert warning.plate_type == "MOTO PARTICULAR"
        assert warning.order_id == order

    def test_mixed_priced_and_unpriced_plates_only_totals_the_priced_ones(self, db_conn):
        order = _insert_order(db_conn, order_number="1")
        _insert_plate(db_conn, order, unit_price=Decimal("5000"), quantity=1)
        _insert_plate(db_conn, order, unit_price=None, quantity=1)

        summary = build_cash_summary(db_conn, desde=date(2026, 1, 1), hasta=date(2026, 1, 1))

        assert summary.grand_total == Decimal("5000")
        assert len(summary.missing_price) == 1

    def test_priced_plate_never_appears_in_missing_price(self, db_conn):
        order = _insert_order(db_conn, order_number="1")
        _insert_plate(db_conn, order, unit_price=Decimal("5000"))

        summary = build_cash_summary(db_conn, desde=date(2026, 1, 1), hasta=date(2026, 1, 1))

        assert summary.missing_price == []


class TestDecimalTyping:
    def test_totals_are_decimal_never_float(self, db_conn):
        order = _insert_order(db_conn, order_number="1")
        _insert_plate(db_conn, order, unit_price=Decimal("1234.56"), quantity=3)

        summary = build_cash_summary(db_conn, desde=date(2026, 1, 1), hasta=date(2026, 1, 1))

        row = summary.days[0].rows[0]
        assert isinstance(row.total, Decimal)
        assert isinstance(summary.days[0].subtotal, Decimal)
        assert isinstance(summary.grand_total, Decimal)
        assert row.total == Decimal("3703.68")


class TestRenderCashSummaryPdf:
    def test_returns_a_real_non_empty_pdf(self, db_conn):
        order = _insert_order(db_conn, order_number="1", payment_method="EFECTIVO")
        _insert_plate(db_conn, order, unit_price=Decimal("10000"), quantity=1)
        summary = build_cash_summary(db_conn, desde=date(2026, 1, 1), hasta=date(2026, 1, 1))

        pdf_bytes = render_cash_summary_pdf(summary, desde=date(2026, 1, 1), hasta=date(2026, 1, 1))

        assert pdf_bytes[:4] == b"%PDF"
        assert len(pdf_bytes) > 500

    def test_pdf_text_contains_totals_and_payment_method(self, db_conn):
        order = _insert_order(db_conn, order_number="1", payment_method="EFECTIVO")
        _insert_plate(db_conn, order, unit_price=Decimal("10000"), quantity=1)
        summary = build_cash_summary(db_conn, desde=date(2026, 1, 1), hasta=date(2026, 1, 1))

        pdf_bytes = render_cash_summary_pdf(summary, desde=date(2026, 1, 1), hasta=date(2026, 1, 1))

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        assert "EFECTIVO" in text
        assert "10.000" in text  # cop-formatted unit total
        assert "TOTAL" in text

    def test_pdf_lists_missing_price_warnings(self, db_conn):
        order = _insert_order(db_conn, order_number="77")
        _insert_plate(db_conn, order, unit_price=None, plate_type="REMOLQUE")
        summary = build_cash_summary(db_conn, desde=date(2026, 1, 1), hasta=date(2026, 1, 1))

        pdf_bytes = render_cash_summary_pdf(summary, desde=date(2026, 1, 1), hasta=date(2026, 1, 1))

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        assert "77" in text
        assert "REMOLQUE" in text

    def test_pdf_with_no_missing_price_has_no_warning_section(self, db_conn):
        order = _insert_order(db_conn, order_number="1")
        _insert_plate(db_conn, order, unit_price=Decimal("1000"))
        summary = build_cash_summary(db_conn, desde=date(2026, 1, 1), hasta=date(2026, 1, 1))

        pdf_bytes = render_cash_summary_pdf(summary, desde=date(2026, 1, 1), hasta=date(2026, 1, 1))

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        assert "sin precio" not in text.lower()
