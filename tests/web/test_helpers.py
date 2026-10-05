"""Pure-function tests for app/web/helpers.py -- no DB dependency."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.web.helpers import customer_fields_from_form, format_cop


class TestFormatCop:
    def test_formats_thousands_with_dots(self):
        assert format_cop(Decimal("1954000")) == "$1.954.000"

    def test_small_amount_has_no_separator(self):
        assert format_cop(Decimal("500")) == "$500"

    def test_none_is_an_em_dash_never_a_silent_zero(self):
        assert format_cop(None) == "—"

    def test_rounds_to_whole_pesos_for_display(self):
        assert format_cop(Decimal("150000.00")) == "$150.000"


class TestCustomerFieldsFromForm:
    """Per-type applicability, enforced server-side (a raw POST can carry stale values)."""

    FULL_FORM = {
        "customer_document_type": "CÉDULA", "customer_document_number": " 123 ",
        "customer_document_relation": "DUEÑO", "customer_phone": "300",
        "customer_email": "a@b.com", "customer_address": " CALLE 1 ",
        "customer_neighborhood": "CENTRO", "customer_municipality": "CALI",
    }

    def _fields(self, customer_type):
        form = dict(self.FULL_FORM)
        if customer_type is not None:
            form["customer_type"] = customer_type
        return customer_fields_from_form(form)

    def test_cliente_keeps_everything(self):
        f = self._fields("CLIENTE")
        assert f["type"] == "CLIENTE"
        assert f["document_relation"] == "DUEÑO"
        assert (f["address"], f["neighborhood"], f["municipality"]) == ("CALLE 1", "CENTRO", "CALI")
        assert f["document_number"] == "123"

    def test_gestor_keeps_person_fields_but_drops_document_relation(self):
        f = self._fields("GESTOR")
        assert f["document_relation"] is None
        assert (f["address"], f["neighborhood"], f["municipality"]) == ("CALLE 1", "CENTRO", "CALI")

    @pytest.mark.parametrize("customer_type", ["EMPRESA", "TRÁNSITO", None])
    def test_other_types_drop_stale_relation_and_person_fields(self, customer_type):
        f = self._fields(customer_type)
        assert f["document_relation"] is None
        assert f["address"] is None
        assert f["neighborhood"] is None
        assert f["municipality"] is None
        # the common fields survive
        assert f["document_type"] == "CÉDULA"
        assert f["document_number"] == "123"
        assert f["phone"] == "300"
        assert f["email"] == "a@b.com"

    def test_blank_person_fields_become_none(self):
        f = customer_fields_from_form({"customer_type": "CLIENTE", "customer_address": "  "})
        assert f["address"] is None

    def test_transit_license_is_no_longer_a_customer_field(self):
        assert "transit_license" not in self._fields("CLIENTE")
