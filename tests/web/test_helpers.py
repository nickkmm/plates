"""Pure-function tests for app/web/helpers.py -- no DB dependency."""

from __future__ import annotations

from decimal import Decimal

from app.web.helpers import format_cop


class TestFormatCop:
    def test_formats_thousands_with_dots(self):
        assert format_cop(Decimal("1954000")) == "$1.954.000"

    def test_small_amount_has_no_separator(self):
        assert format_cop(Decimal("500")) == "$500"

    def test_none_is_an_em_dash_never_a_silent_zero(self):
        assert format_cop(None) == "—"

    def test_rounds_to_whole_pesos_for_display(self):
        assert format_cop(Decimal("150000.00")) == "$150.000"
