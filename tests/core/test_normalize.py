"""Tests for app.core.normalize -- pure functions, no DB.

Highest-value module in the suite: normalization/annulment-detection bugs
here silently corrupt inventory numbers or merge distinct customers. See the
module docstring in app/core/normalize.py for the business rules being
tested.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.models import ExtractedRow
from app.core.normalize import (
    _PLATE_DESC,
    INVENTORY_CATEGORIES,
    PLATE_TYPES,
    annulment_kind,
    clean_plate,
    inventory_category,
    is_real_plate,
    normalize_city,
    normalize_plate_type,
    normalize_requested_by,
    normalize_row,
    validate_plate,
)


def make_row(**overrides: object) -> ExtractedRow:
    """Build a minimal valid ExtractedRow, overriding only what a test cares about."""
    defaults: dict[str, object] = {
        "order_number": "1",
        "order_date": date(2026, 1, 1),
        "requested_by": "TEST",
        "plate_type": "CARRO PARTICULAR",
    }
    defaults.update(overrides)
    return ExtractedRow(**defaults)


# --------------------------------------------------------------------------
# normalize_plate_type
# --------------------------------------------------------------------------


class TestNormalizePlateType:
    def test_none_passthrough(self):
        assert normalize_plate_type(None) is None

    def test_empty_string_passthrough(self):
        assert normalize_plate_type("") == ""

    @pytest.mark.parametrize(
        "value",
        ["MOTO PARTICULAR", "moto particular", "  Moto Particular  "],
    )
    def test_moto_particular(self, value):
        assert normalize_plate_type(value) == "MOTO PARTICULAR"

    @pytest.mark.parametrize(
        "value",
        ["CARRO PARTICULAR", "carro particular"],
    )
    def test_carro_particular(self, value):
        assert normalize_plate_type(value) == "CARRO PARTICULAR"

    def test_historical_moto_publico_label(self):
        """The old ledger label 'MOTO PUBLICO' means today's público moto-carro."""
        assert normalize_plate_type("MOTO PUBLICO") == "MOTO CARRO PÚBLICO"
        assert normalize_plate_type("moto publico") == "MOTO CARRO PÚBLICO"
        assert normalize_plate_type("MOTO PÚBLICO") == "MOTO CARRO PÚBLICO"

    def test_moto_carro_particular_detection(self):
        assert normalize_plate_type("MOTO CARRO PARTICULAR") == "MOTO CARRO PARTICULAR"
        assert normalize_plate_type("motocarro particular") == "MOTO CARRO PARTICULAR"

    def test_moto_carro_publico_detection(self):
        assert normalize_plate_type("MOTO CARRO PUBLICO") == "MOTO CARRO PÚBLICO"
        assert normalize_plate_type("MOTOCARRO PÚBLICO") == "MOTO CARRO PÚBLICO"

    def test_carro_publico(self):
        assert normalize_plate_type("CARRO PÚBLICO") == "CARRO PÚBLICO"
        assert normalize_plate_type("carro publico") == "CARRO PÚBLICO"

    def test_bare_publico(self):
        assert normalize_plate_type("PÚBLICO") == "CARRO PÚBLICO"

    def test_remolque(self):
        assert normalize_plate_type("REMOLQUE") == "REMOLQUE"
        assert normalize_plate_type("remolque") == "REMOLQUE"

    def test_remolque_substring_variant_is_canonicalized(self):
        """A value where REMOLQUE is a substring (not the whole string) must
        still canonicalize to the bare 'REMOLQUE' type -- distinguishes the
        `"REMOLQUE" in f` branch actually firing from merely falling through
        to the final `return f`, which happens to coincide for the exact
        bare-string case above."""
        assert normalize_plate_type("REMOLQUE TIPO 2") == "REMOLQUE"

    def test_ornamental(self):
        assert normalize_plate_type("ORNAMENTAL") == "ORNAMENTAL"
        assert normalize_plate_type("ornamental") == "ORNAMENTAL"

    def test_ornamental_takes_precedence(self):
        """ORNAMENTAL is checked before público/remolque -- a value containing
        both should resolve to ORNAMENTAL (matches the first `if` in the
        function)."""
        assert normalize_plate_type("ORNAMENTAL PÚBLICO") == "ORNAMENTAL"


# --------------------------------------------------------------------------
# inventory_category
# --------------------------------------------------------------------------


class TestInventoryCategory:
    def test_all_plate_types_map_to_a_known_category_or_none(self):
        for pt in PLATE_TYPES:
            result = inventory_category(pt)
            assert result is None or result in INVENTORY_CATEGORIES

    def test_moto_particular(self):
        assert inventory_category("MOTO PARTICULAR") == "MOTO PARTICULAR"

    def test_carro_particular(self):
        assert inventory_category("CARRO PARTICULAR") == "CARRO PARTICULAR"

    def test_moto_carro_particular_maps_to_moto_particular(self):
        assert inventory_category("MOTO CARRO PARTICULAR") == "MOTO PARTICULAR"

    def test_moto_carro_publico_maps_to_publico(self):
        assert inventory_category("MOTO CARRO PÚBLICO") == "PÚBLICO"

    def test_carro_publico_maps_to_publico(self):
        assert inventory_category("CARRO PÚBLICO") == "PÚBLICO"

    def test_remolque_maps_to_publico(self):
        assert inventory_category("REMOLQUE") == "PÚBLICO"

    def test_ornamental_maps_to_none(self):
        assert inventory_category("ORNAMENTAL") is None

    def test_unknown_type_passthrough(self):
        """Documents current fallback: an unrecognized type is returned as-is
        (not None), since it matches none of the special-case branches."""
        assert inventory_category("SOMETHING ELSE") == "SOMETHING ELSE"

    def test_none_passthrough(self):
        assert inventory_category(None) is None


# --------------------------------------------------------------------------
# clean_plate
# --------------------------------------------------------------------------


class TestCleanPlate:
    def test_lowercase_with_space(self):
        assert clean_plate("arn 911") == "ARN911"

    def test_already_clean(self):
        assert clean_plate("HMF922") == "HMF922"

    def test_strips_surrounding_whitespace(self):
        assert clean_plate("  kwd91g  ") == "KWD91G"

    def test_collapses_internal_whitespace(self):
        assert clean_plate("AB C 123") == "ABC123"

    def test_tabs_and_newlines(self):
        assert clean_plate("hmf\t922\n") == "HMF922"


# --------------------------------------------------------------------------
# validate_plate
# --------------------------------------------------------------------------


class TestValidatePlate:
    def test_moto_particular_valid(self):
        assert validate_plate("KWD91G", "MOTO PARTICULAR") == "KWD91G"

    def test_moto_particular_valid_lowercase(self):
        assert validate_plate("kwd91g", "MOTO PARTICULAR") == "KWD91G"

    def test_carro_particular_valid(self):
        assert validate_plate("HMF922", "CARRO PARTICULAR") == "HMF922"

    def test_remolque_valid_r_prefix(self):
        assert validate_plate("R12345", "REMOLQUE") == "R12345"

    def test_remolque_valid_s_prefix(self):
        assert validate_plate("S12345", "REMOLQUE") == "S12345"

    def test_moto_carro_particular_valid(self):
        assert validate_plate("923NAA", "MOTO CARRO PARTICULAR") == "923NAA"

    def test_wrong_format_raises(self):
        with pytest.raises(ValueError):
            validate_plate("KWD91G", "CARRO PARTICULAR")

    def test_wrong_format_raises_for_moto_carro(self):
        with pytest.raises(ValueError):
            validate_plate("HMF922", "MOTO CARRO PARTICULAR")

    def test_too_short_raises(self):
        with pytest.raises(ValueError):
            validate_plate("HM92", "CARRO PARTICULAR")

    def test_unknown_type_skips_validation(self):
        """A plate_type absent from PLATE_PATTERNS has no validation applied
        (pattern lookup returns None, which short-circuits the check) -- the
        cleaned value is returned unconditionally."""
        assert validate_plate("anything 123", "SOME UNKNOWN TYPE") == "ANYTHING123"

    def test_error_message_mentions_plate_and_type(self):
        with pytest.raises(ValueError, match="HMF922"):
            validate_plate("HMF922", "MOTO PARTICULAR")

    def test_error_message_includes_the_format_description(self):
        """Not just that it raises -- the message must include the actual
        expected-format hint (_PLATE_DESC[plate_type]), so the user knows
        what to fix. Also guards the `_PLATE_DESC.get(plate_type, ...)`
        lookup: it must use the real plate_type as the key."""
        with pytest.raises(ValueError) as exc_info:
            validate_plate("HMF922", "MOTO PARTICULAR")
        assert _PLATE_DESC["MOTO PARTICULAR"] in str(exc_info.value)


# --------------------------------------------------------------------------
# normalize_requested_by
# --------------------------------------------------------------------------


class TestNormalizeRequestedBy:
    def test_known_variant_maps_to_canonical(self):
        assert normalize_requested_by("PTOV") == "PTO.V"

    def test_known_variant_case_insensitive(self):
        assert normalize_requested_by("ptov") == "PTO.V"

    def test_known_variant_ignores_spacing(self):
        assert normalize_requested_by("PTO V") == "PTO.V"

    def test_distinct_mapped_keys_stay_distinct(self):
        """DIANAP and DIANAZ are curated as two different people -- must not
        collapse to the same canonical form."""
        assert normalize_requested_by("DIANAP") == "DIANA.P"
        assert normalize_requested_by("DIANAZ") == "DIANA.Z"
        assert normalize_requested_by("DIANAP") != normalize_requested_by("DIANAZ")

    def test_spelling_fallback_unmapped_name(self):
        assert normalize_requested_by("  jose perez  ") == "JOSE PEREZ"

    def test_spelling_fallback_keeps_accents(self):
        assert normalize_requested_by("andrés   gómez") == "ANDRÉS GÓMEZ"

    def test_spelling_fallback_collapses_redundant_dot_spacing(self):
        assert normalize_requested_by("juan . perez") == "JUAN.PEREZ"

    def test_spelling_fallback_drops_trailing_dot(self):
        assert normalize_requested_by("MARIA.") == "MARIA"

    def test_numeric_passthrough_untouched(self):
        """A misaligned row that put a number in this column is left alone
        entirely -- not even stripped -- per _NUMERIC."""
        assert normalize_requested_by("598") == "598"

    def test_numeric_passthrough_preserves_original_whitespace(self):
        """The numeric branch returns the ORIGINAL value, not the
        stripped/cleaned one -- pins down this exact (surprising) detail."""
        assert normalize_requested_by("  598  ") == "  598  "

    def test_numeric_float_like_passthrough(self):
        assert normalize_requested_by("2.0") == "2.0"

    def test_empty_string_passthrough(self):
        assert normalize_requested_by("") == ""

    def test_blank_whitespace_passthrough(self):
        assert normalize_requested_by("   ") == "   "

    # --- Regression: distinct-sounding unmapped names must NEVER merge -----

    def test_magda_vs_magda_isabel_rubiano_stay_distinct(self):
        magda = normalize_requested_by("MAGDA")
        magda_isabel = normalize_requested_by("MAGDA ISABEL RUBIANO")
        assert magda == "MAGDA"
        assert magda_isabel == "MAGDA ISABEL RUBIANO"
        assert magda != magda_isabel

    def test_muva_granada_vs_muva_sergio_stay_distinct(self):
        """Named explicitly in the module docstring as intermediaries that
        must never be auto-merged despite the shared 'MUVA' prefix."""
        granada = normalize_requested_by("MUVA GRANADA")
        sergio = normalize_requested_by("MUVA SERGIO")
        assert granada == "MUVA GRANADA"
        assert sergio == "MUVA SERGIO"
        assert granada != sergio

    def test_similar_unmapped_names_only_spelling_cleaned_never_merged(self):
        """Two distinct, similarly-spelled names that are NOT in the curated
        map must each get independent spelling cleanup, never fuzzy-merged
        into one canonical form."""
        a = normalize_requested_by("carlos gomez")
        b = normalize_requested_by("carlos guzman")
        assert a == "CARLOS GOMEZ"
        assert b == "CARLOS GUZMAN"
        assert a != b


# --------------------------------------------------------------------------
# is_real_plate
# --------------------------------------------------------------------------


class TestIsRealPlate:
    def test_none_is_not_real(self):
        assert is_real_plate(None) is False

    def test_empty_string_is_not_real(self):
        assert is_real_plate("") is False

    def test_muestra_is_not_real(self):
        assert is_real_plate("MUESTRA") is False

    def test_muestra_lowercase_is_not_real(self):
        assert is_real_plate("muestra") is False

    def test_muestras_plural_is_not_real(self):
        assert is_real_plate("MUESTRAS") is False

    def test_ingse_is_not_real(self):
        assert is_real_plate("INGSE") is False

    def test_kwp_is_not_real(self):
        assert is_real_plate("KWP") is False

    def test_serial_note_is_not_real(self):
        assert is_real_plate("S/43050") is False

    def test_bare_serial_prefix_is_not_real(self):
        assert is_real_plate("S/") is False

    def test_real_plate_is_real(self):
        assert is_real_plate("HMF922") is True

    def test_real_plate_with_whitespace_and_case_is_real(self):
        assert is_real_plate("  hmf922  ") is True

    def test_plate_starting_with_s_but_not_slash_is_real(self):
        """Must not be confused with the 'S/' serial-note prefix check."""
        assert is_real_plate("S12345") is True


# --------------------------------------------------------------------------
# normalize_city
# --------------------------------------------------------------------------


class TestNormalizeCity:
    def test_none_returns_none(self):
        assert normalize_city(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_city("") is None

    def test_lowercase_becomes_uppercase(self):
        assert normalize_city("cali") == "CALI"

    def test_keeps_accents(self):
        assert normalize_city("  Bogotá  ") == "BOGOTÁ"

    def test_collapses_internal_whitespace(self):
        assert normalize_city("Cali   Norte") == "CALI NORTE"

    def test_punctuation_only_dash_returns_none(self):
        assert normalize_city("-") is None

    def test_punctuation_only_slashes_returns_none(self):
        assert normalize_city("//") is None


# --------------------------------------------------------------------------
# annulment_kind (helper used by normalize_row)
# --------------------------------------------------------------------------


class TestAnnulmentKind:
    def test_no_annulment_words_returns_none(self):
        assert annulment_kind("TEST", "HMF922", "") is None

    def test_anulad_and_orden_returns_order(self):
        assert annulment_kind("", "ANULADA ORDEN", "") == "order"

    def test_repite_alone_returns_order(self):
        assert annulment_kind("", "REPITE", "") == "order"

    def test_anulad_and_placa_returns_plate(self):
        assert annulment_kind("", "ANULADA PLACA", "") == "plate"

    def test_bare_anulad_returns_bare(self):
        assert annulment_kind("", "ANULADO", "") == "bare"


# --------------------------------------------------------------------------
# normalize_row -- annulment-detection integration (business-critical)
# --------------------------------------------------------------------------


class TestNormalizeRow:
    def test_reposicion_sets_batch_replacement_and_clears_plate_number(self):
        row = make_row(plate_number="REPOSICION")
        result = normalize_row(row)
        assert result.is_batch_replacement is True
        assert result.plate_number is None
        assert result.is_voided is False

    def test_anulad_orden_sets_voided_and_clears_plate_number(self):
        row = make_row(plate_number="ANULADA ORDEN")
        result = normalize_row(row)
        assert result.is_voided is True
        assert result.plate_number is None
        assert result.is_batch_replacement is False

    def test_repite_sets_voided_and_clears_plate_number(self):
        row = make_row(plate_number="REPITE")
        result = normalize_row(row)
        assert result.is_voided is True
        assert result.plate_number is None

    def test_bare_anulad_sets_voided_and_clears_plate_number(self):
        """A bare 'ANULAD' with no ORDEN/PLACA qualifier (often a misread)
        is classified "bare" by annulment_kind() and treated conservatively
        as not-consumed, same as "order" -- normalize_row must actually act
        on that (`if kind in ("order", "bare")`), not just classify it."""
        row = make_row(plate_number="ANULADO")
        result = normalize_row(row)
        assert result.is_voided is True
        assert result.plate_number is None

    def test_anulad_placa_current_behavior(self):
        """Documents the CURRENT observed behavior for the 'placa anulada'
        (blank lost but still consumed) case, per annulment_kind's "plate"
        classification.

        NOTE: annulment_kind() computes kind == "plate" for ANULAD+PLACA
        text (see its docstring: "the blank WAS lost -> consumed"), correctly
        leaving is_voided False here. However normalize_row() only branches
        on `kind in ("order", "bare")` -- there is no branch reacting to
        kind == "plate" at all. As implemented, this means:
          - plate_number is cleared to None (the generic "ANULAD ... in
            text" elif fires the same way it does for order/bare/REPITE),
            NOT preserved.
          - is_plate_annulled is NOT set True by this function (the flag is
            only ever set True in app/web/app.py's `placa_anulada` form
            line-type, never inferred here from ledger text).
        This looks like a real gap between annulment_kind's documented
        intent and normalize_row's handling of its "plate" result (flagged
        to the user rather than silently patched, per task instructions).
        """
        row = make_row(plate_number="ANULADA PLACA")
        result = normalize_row(row)
        assert result.plate_number is None
        assert result.is_voided is False
        assert result.is_plate_annulled is False

    def test_anulad_placa_with_embedded_real_plate_still_clears_it(self):
        """Same gap as above, shown with a real plate number embedded
        alongside the annotation -- it is still discarded entirely."""
        row = make_row(plate_number="HMF922 ANULADA PLACA")
        result = normalize_row(row)
        assert result.plate_number is None

    def test_real_plate_is_kept_and_uppercased(self):
        row = make_row(plate_number="hmf922")
        result = normalize_row(row)
        assert result.plate_number == "HMF922"

    def test_empty_plate_number_stays_none(self):
        row = make_row(plate_number=None)
        result = normalize_row(row)
        assert result.plate_number is None

    def test_requested_by_is_normalized(self):
        row = make_row(requested_by="ptov")
        result = normalize_row(row)
        assert result.requested_by == "PTO.V"

    def test_plate_type_is_normalized(self):
        row = make_row(plate_type="moto publico")
        result = normalize_row(row)
        assert result.plate_type == "MOTO CARRO PÚBLICO"

    def test_city_annulment_marker_cleared(self):
        row = make_row(city="ANULADO")
        result = normalize_row(row)
        assert result.city is None

    def test_city_is_normalized_and_uppercased(self):
        """A normal (non-annulment) city must actually go through
        normalize_city() and come back uppercased -- pins down that the
        normalized value lands on the `city` field itself."""
        row = make_row(city="cali")
        result = normalize_row(row)
        assert result.city == "CALI"

    def test_never_clears_a_preset_true_flag(self):
        """normalize_row only ADDS inferences; a True flag set upstream
        (e.g. by the web form) must survive even if the text gives no
        independent reason to set it."""
        row = make_row(plate_number="HMF922", is_plate_annulled=True)
        result = normalize_row(row)
        assert result.is_plate_annulled is True
        assert result.plate_number == "HMF922"
