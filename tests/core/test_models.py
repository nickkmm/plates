"""Tests for app.core.models -- ExtractedRow's before-validators.

These validators run BEFORE app.core.normalize ever sees the data (they only
coerce/clean at the type level; the richer business normalization -- casing,
canonical names, annulment inference -- is normalize.py's job and is tested
separately in test_normalize.py).
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.core.models import ExtractedRow, Extraction


def make_row(**overrides: object) -> ExtractedRow:
    defaults: dict[str, object] = {
        "order_number": "1",
        "order_date": date(2026, 1, 1),
        "requested_by": "TEST",
        "plate_type": "CARRO PARTICULAR",
    }
    defaults.update(overrides)
    return ExtractedRow(**defaults)


# --------------------------------------------------------------------------
# order_number coercion
# --------------------------------------------------------------------------


class TestOrderNumberCoercion:
    def test_int_becomes_string(self):
        row = make_row(order_number=598)
        assert row.order_number == "598"
        assert isinstance(row.order_number, str)

    def test_integer_valued_float_becomes_clean_string(self):
        """598.0 must become '598', not '598.0'."""
        row = make_row(order_number=598.0)
        assert row.order_number == "598"

    def test_non_numeric_string_kept_as_is(self):
        row = make_row(order_number="NT 895")
        assert row.order_number == "NT 895"

    def test_numeric_string_kept_as_is(self):
        row = make_row(order_number="598")
        assert row.order_number == "598"

    def test_string_is_stripped(self):
        row = make_row(order_number="  598  ")
        assert row.order_number == "598"


# --------------------------------------------------------------------------
# blank-string -> None for optional fields
# --------------------------------------------------------------------------


class TestBlankToNone:
    @pytest.mark.parametrize("field", ["city", "plate_number", "plate_to", "material", "prepared_by"])
    def test_empty_string_becomes_none(self, field):
        row = make_row(**{field: ""})
        assert getattr(row, field) is None

    @pytest.mark.parametrize("field", ["city", "plate_number", "plate_to", "material", "prepared_by"])
    def test_whitespace_only_becomes_none(self, field):
        row = make_row(**{field: "   "})
        assert getattr(row, field) is None

    def test_non_blank_value_is_kept_and_stripped(self):
        row = make_row(city="  Cali  ")
        assert row.city == "Cali"

    def test_field_omitted_defaults_to_none(self):
        row = make_row()
        assert row.city is None
        assert row.plate_number is None
        assert row.plate_to is None
        assert row.material is None
        assert row.prepared_by is None


# --------------------------------------------------------------------------
# quantity: ge=0
# --------------------------------------------------------------------------


class TestQuantity:
    def test_default_is_one(self):
        row = make_row()
        assert row.quantity == 1

    def test_negative_quantity_rejected(self):
        with pytest.raises(ValidationError):
            make_row(quantity=-1)

    def test_zero_quantity_allowed(self):
        row = make_row(quantity=0)
        assert row.quantity == 0

    def test_positive_quantity_allowed(self):
        row = make_row(quantity=42)
        assert row.quantity == 42


# --------------------------------------------------------------------------
# required text fields (requested_by, plate_type) are stripped
# --------------------------------------------------------------------------


class TestRequiredTextStripping:
    def test_requested_by_is_stripped(self):
        row = make_row(requested_by="  PEDRO  ")
        assert row.requested_by == "PEDRO"

    def test_plate_type_is_stripped(self):
        row = make_row(plate_type="  CARRO PARTICULAR  ")
        assert row.plate_type == "CARRO PARTICULAR"

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            # Deliberately omitting the required order_number to exercise
            # pydantic's runtime validation -- mypy would (correctly) flag a
            # real caller for this, but that's exactly what's under test.
            ExtractedRow(order_date=date(2026, 1, 1), requested_by="X", plate_type="Y")  # type: ignore[call-arg]


# --------------------------------------------------------------------------
# defaults for flags
# --------------------------------------------------------------------------


class TestFlagDefaults:
    def test_flags_default_false(self):
        row = make_row()
        assert row.is_batch_replacement is False
        assert row.is_voided is False
        assert row.is_plate_annulled is False


# --------------------------------------------------------------------------
# Extraction (top-level list wrapper)
# --------------------------------------------------------------------------


class TestExtraction:
    def test_wraps_a_list_of_rows(self):
        extraction = Extraction(rows=[make_row().model_dump(mode="json")])
        assert len(extraction.rows) == 1
        assert isinstance(extraction.rows[0], ExtractedRow)

    def test_empty_rows_list_is_valid(self):
        extraction = Extraction(rows=[])
        assert extraction.rows == []
