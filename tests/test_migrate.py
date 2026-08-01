"""Tests for app.migrate -- _key_val, _shift_future_dates, and _to_row.

Uses small synthetic pandas Series/dicts, no real Excel file. _key_val and
_shift_future_dates were originally nested inside main() and have been
hoisted to module level (behavior-preserving refactor) specifically so they
can be unit-tested here; _to_row was already module-level.

NOTE on plate_number whitespace: a concurrent change elsewhere may alter
_to_row's whitespace handling for plate_number. The test below
(`test_plate_number_keeps_internal_whitespace_current_behavior`) pins down
the CURRENT behavior observed in this worktree (internal whitespace is NOT
collapsed, only leading/trailing via ExtractedRow's own validator) --
intentionally, per task instructions, rather than asserting the "fixed"
behavior.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.migrate import _key_val, _shift_future_dates, _to_row


def _base_record(**overrides: object) -> dict:
    record: dict[str, object] = {
        "FECHA": pd.Timestamp("2026-03-15"),
        "ORDEN DE PRODUCCION": 598,
        "SOLICITADO POR": "PEDRO",
        "TIPO": "CARRO PARTICULAR",
        "RANGO": "HMF922",
        "CIUDAD": "CALI",
        "CANT": 1,
        "MATERIAL": None,
        "ALISTADO POR": None,
    }
    record.update(overrides)
    return record


# --------------------------------------------------------------------------
# _key_val
# --------------------------------------------------------------------------


class TestKeyVal:
    def test_int_and_string_collapse(self):
        assert _key_val(598) == _key_val("598") == "598"

    def test_float_and_int_collapse(self):
        assert _key_val(2.0) == _key_val(2) == _key_val("2") == "2"

    def test_none_is_empty_string(self):
        assert _key_val(None) == ""

    def test_nan_is_empty_string(self):
        assert _key_val(float("nan")) == ""

    def test_strings_are_stripped_and_uppercased(self):
        assert _key_val("  abc  ") == "ABC"

    def test_non_integer_float_keeps_decimal(self):
        assert _key_val(2.5) == "2.5"


# --------------------------------------------------------------------------
# _shift_future_dates
# --------------------------------------------------------------------------


class TestShiftFutureDates:
    def test_future_dated_row_shifts_back_one_year(self):
        dates = pd.Series([pd.Timestamp("2027-03-28")])
        today = pd.Timestamp("2026-06-01")
        shifted, n = _shift_future_dates(dates, today)
        assert n == 1
        assert shifted.iloc[0] == pd.Timestamp("2026-03-28")

    def test_past_dates_untouched(self):
        dates = pd.Series([pd.Timestamp("2026-01-01"), pd.Timestamp("2026-02-01")])
        today = pd.Timestamp("2026-06-01")
        shifted, n = _shift_future_dates(dates, today)
        assert n == 0
        assert list(shifted) == list(dates)

    def test_mixed_past_and_future(self):
        dates = pd.Series([
            pd.Timestamp("2027-01-01"),
            pd.Timestamp("2027-12-31"),
            pd.Timestamp("2025-01-01"),
        ])
        today = pd.Timestamp("2026-06-01")
        shifted, n = _shift_future_dates(dates, today)
        assert n == 2
        assert shifted.iloc[0] == pd.Timestamp("2026-01-01")
        assert shifted.iloc[1] == pd.Timestamp("2026-12-31")
        assert shifted.iloc[2] == pd.Timestamp("2025-01-01")  # unchanged

    def test_exact_today_is_not_shifted(self):
        today = pd.Timestamp("2026-06-01")
        dates = pd.Series([today])
        shifted, n = _shift_future_dates(dates, today)
        assert n == 0
        assert shifted.iloc[0] == today


# --------------------------------------------------------------------------
# _to_row
# --------------------------------------------------------------------------


class TestToRow:
    def test_realistic_record_maps_correctly(self):
        row = _to_row(_base_record())
        assert row.order_number == "598"
        assert row.order_date == date(2026, 3, 15)
        assert row.requested_by == "PEDRO"
        assert row.plate_type == "CARRO PARTICULAR"
        assert row.plate_number == "HMF922"
        assert row.city == "CALI"
        assert row.quantity == 1
        assert row.material is None
        assert row.prepared_by is None

    def test_order_number_float_coerced_via_model(self):
        row = _to_row(_base_record(**{"ORDEN DE PRODUCCION": 598.0}))
        assert row.order_number == "598"

    def test_order_number_non_numeric_kept(self):
        row = _to_row(_base_record(**{"ORDEN DE PRODUCCION": "NT 895"}))
        assert row.order_number == "NT 895"

    def test_timestamp_order_date_converted_to_date(self):
        row = _to_row(_base_record(FECHA=pd.Timestamp("2026-07-04")))
        assert row.order_date == date(2026, 7, 4)

    def test_blank_city_becomes_none(self):
        row = _to_row(_base_record(CIUDAD=""))
        assert row.city is None

    def test_nan_city_becomes_none(self):
        row = _to_row(_base_record(CIUDAD=float("nan")))
        assert row.city is None

    # --- CANT-vs-MATERIAL quantity fallback --------------------------------

    def test_quantity_from_cant_when_present(self):
        row = _to_row(_base_record(CANT=5, MATERIAL=None))
        assert row.quantity == 5

    def test_quantity_falls_back_to_material_when_cant_blank(self):
        """ALISTAMIENTO rows historically had the quantity written in
        MATERIAL instead of CANT."""
        row = _to_row(_base_record(CANT=None, MATERIAL=750))
        assert row.quantity == 750

    def test_quantity_falls_back_to_material_float(self):
        row = _to_row(_base_record(CANT=None, MATERIAL=750.0))
        assert row.quantity == 750

    def test_quantity_defaults_to_one_when_both_blank(self):
        row = _to_row(_base_record(CANT=None, MATERIAL=None))
        assert row.quantity == 1

    def test_quantity_defaults_to_one_when_material_non_numeric(self):
        """MATERIAL sometimes holds text like 'ANULADO'/'ORNAMENTAL' instead
        of a number -- int(float(...)) raises ValueError, caught, default 1."""
        row = _to_row(_base_record(CANT=None, MATERIAL="ORNAMENTAL"))
        assert row.quantity == 1

    def test_quantity_zero_in_cant_is_kept_not_treated_as_blank(self):
        row = _to_row(_base_record(CANT=0, MATERIAL=999))
        assert row.quantity == 0

    # --- current (unfixed) whitespace behavior ------------------------------

    def test_plate_number_keeps_internal_whitespace_current_behavior(self):
        """Pinning down CURRENT behavior, not the desired one: _clean() only
        treats a string as blank if IT strips to empty -- it does not strip
        non-blank strings itself. ExtractedRow's own blank-to-none validator
        strips leading/trailing whitespace, but nothing collapses internal
        whitespace at this stage (that only happens later, in
        normalize.clean_plate / normalize_row, if at all). See module
        docstring above."""
        row = _to_row(_base_record(RANGO=" HMF 922 "))
        assert row.plate_number == "HMF 922"  # internal space NOT removed

    def test_plate_number_with_no_extra_whitespace_unaffected(self):
        row = _to_row(_base_record(RANGO="HMF922"))
        assert row.plate_number == "HMF922"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_blank_plate_number_becomes_none(self, value):
        row = _to_row(_base_record(RANGO=value))
        assert row.plate_number is None
