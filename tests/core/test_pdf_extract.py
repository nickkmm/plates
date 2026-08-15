"""Unit tests for app.core.pdf_extract — pure parsing, no DB, no Flask.

Every PDF here is generated on the fly by tests.pdf_fixtures; the real sample
pedidos are gitignored business data and must never be a test dependency.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.pdf_extract import (
    PdfExtractError,
    extract_rows_from_pdf,
    parse_order_date,
    parse_pedido_pdf,
)
from tests.pdf_fixtures import (
    build_pdf_with_unrelated_table,
    build_pdf_without_table,
    build_pedido_pdf,
)


class TestParseOrderDate:
    """The one field the two real samples disagree on: same template, two formats."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("11/08/2026", date(2026, 8, 11)),          # numeric, day-first (sample #1)
            ("13 DE AGOSTO DE 2026", date(2026, 8, 13)),  # spelled out (sample #2)
            ("13 de agosto de 2026", date(2026, 8, 13)),  # lower case
            ("2026-08-13", date(2026, 8, 13)),          # ISO (the v2 template exports this)
            ("13-08-2026", date(2026, 8, 13)),          # dashes
            ("13.08.2026", date(2026, 8, 13)),          # dots
            ("13/08/26", date(2026, 8, 13)),            # 2-digit year
            ("1 DE ENERO DE 2026", date(2026, 1, 1)),
            ("30 DE SETIEMBRE DE 2026", date(2026, 9, 30)),  # 'setiembre' spelling variant
            ("AGOSTO 13 DE 2026", date(2026, 8, 13)),   # month-first spelled out
            ("  13   DE   AGOSTO   DE   2026 ", date(2026, 8, 13)),  # ragged whitespace
        ],
    )
    def test_parses_every_format_the_documents_use(self, raw, expected):
        assert parse_order_date(raw) == expected

    def test_day_first_wins_when_both_readings_are_valid(self):
        """11/08 is 11 August (Colombian convention), never 8 November."""
        assert parse_order_date("11/08/2026") == date(2026, 8, 11)

    def test_falls_back_to_month_first_only_when_day_first_is_impossible(self):
        """08/13/2026 can only mean 13 August — day-first would be month 13."""
        assert parse_order_date("08/13/2026") == date(2026, 8, 13)

    @pytest.mark.parametrize("raw", ["", "   ", "PENDIENTE", "31/02/2026", "99/99/9999", "2026"])
    def test_returns_none_rather_than_guessing(self, raw):
        assert parse_order_date(raw) is None


class TestHeaderFields:
    def test_reads_the_production_order_number(self):
        assert parse_pedido_pdf(build_pedido_pdf(order_number="9001")).order_number == "9001"

    def test_never_confuses_pedido_number_with_production_order(self):
        """PEDIDO # is the client-facing claim number and a DIFFERENT number.
        Reading it as the production order would corrupt every saved record."""
        pedido = parse_pedido_pdf(build_pedido_pdf(order_number="9001", pedido_numero="28"))
        assert pedido.order_number == "9001"
        assert pedido.order_number != "28"

    def test_accepts_the_older_nt_label_for_the_same_field(self):
        """Documents from the previous template generation label the production
        order 'NT:'. They must keep parsing through the changeover."""
        pedido = parse_pedido_pdf(build_pedido_pdf(order_number="9001", order_label="NT:"))
        assert pedido.order_number == "9001"

    def test_nt_label_still_beats_the_pedido_decoy(self):
        pedido = parse_pedido_pdf(
            build_pedido_pdf(order_number="9001", order_label="NT:", pedido_numero="28")
        )
        assert pedido.order_number == "9001"

    def test_accented_order_label_from_the_v2_template_parses(self):
        """The revised template writes 'ORDEN DE PRODUCCIÓN:' with the accent."""
        pedido = parse_pedido_pdf(
            build_pedido_pdf(order_number="2900", order_label="ORDEN DE PRODUCCIÓN:")
        )
        assert pedido.order_number == "2900"

    def test_value_written_inline_after_the_colon_is_read(self):
        """Some cells hold 'LABEL: value' together in one cell (the real
        documents do this for NIT and PEDIDO #) rather than in a separate cell."""
        pedido = parse_pedido_pdf(
            build_pedido_pdf(order_label="ORDEN DE PRODUCCION: 2900", order_number="")
        )
        assert pedido.order_number == "2900"

    def test_missing_order_label_leaves_the_number_empty(self):
        pedido = parse_pedido_pdf(build_pedido_pdf(order_label="OTRO CAMPO:"))
        assert pedido.order_number == ""
        assert len(pedido.lines) == 4  # lines still recovered for manual completion

    def test_reads_the_client_as_requested_by(self):
        assert parse_pedido_pdf(build_pedido_pdf(client="CLIENTE DEMO")).requested_by == "CLIENTE DEMO"

    def test_reads_the_order_date(self):
        assert parse_pedido_pdf(build_pedido_pdf(order_date="11/08/2026")).order_date == date(2026, 8, 11)

    def test_spelled_out_date_reaches_the_same_value(self):
        pedido = parse_pedido_pdf(build_pedido_pdf(order_date="13 DE AGOSTO DE 2026"))
        assert pedido.order_date == date(2026, 8, 13)

    def test_blank_field_followed_by_another_label_reads_as_empty(self):
        """CLIENTE and NIT share a row in the real form. If CLIENTE's value is
        blank, the scan must stop at the NIT label rather than swallow it as
        the client's name."""
        pedido = parse_pedido_pdf(build_pedido_pdf(client="NIT / CÉDULA: 123456"))
        assert pedido.requested_by == ""
        assert len(pedido.lines) == 4  # the plate lines still parse

    def test_unreadable_date_leaves_the_field_empty_but_keeps_the_lines(self):
        """One bad header field must never cost the person the plate lines."""
        pedido = parse_pedido_pdf(build_pedido_pdf(order_date="POR CONFIRMAR"))
        assert pedido.order_date is None
        assert len(pedido.lines) == 4


class TestPlateLines:
    @pytest.fixture
    def pedido(self):
        return parse_pedido_pdf(build_pedido_pdf())

    def test_keeps_only_rows_that_carry_a_plate_or_a_range(self, pedido):
        """The fixture has 5 table rows; the 5th is a pre-printed empty line
        with only an ITEM number and must not become a phantom plate."""
        assert len(pedido.lines) == 4

    def test_single_plate_line(self, pedido):
        line = pedido.lines[0]
        assert line.line_type == "placa"
        assert line.plate_number == "ABC12D"
        assert line.plate_type == "MOTO PARTICULAR"
        assert line.quantity == 1  # blank CANT. means 1, like the manual form
        assert line.plate_to is None
        assert line.city is None

    def test_line_with_a_missing_item_number_is_still_kept(self, pedido):
        """A real sample has a plate whose ITEM cell is blank — rows are keyed
        off PLACA, never off ITEM being sequential or even present."""
        line = pedido.lines[1]
        assert line.plate_number == "EFG34H"
        assert line.plate_type == "MOTO PARTICULAR"

    def test_city_and_quantity_line(self, pedido):
        line = pedido.lines[2]
        assert line.plate_number == "PUB100"
        assert line.plate_type == "CARRO PÚBLICO"
        assert line.city == "CIUDAD DEMO"
        assert line.quantity == 2

    def test_range_line_maps_to_plate_number_and_plate_to(self, pedido):
        """Neither real sample has a filled range row, so this path is only
        covered here — it must still produce a 'rango' line the form accepts."""
        line = pedido.lines[3]
        assert line.line_type == "rango"
        assert line.plate_number == "RNG001"
        assert line.plate_to == "RNG009"
        assert line.plate_type == "CARRO PARTICULAR"
        assert line.quantity == 9

    def test_distinct_cities_are_reported_in_order_of_appearance(self):
        pedido = parse_pedido_pdf(build_pedido_pdf(lines=[
            {"item": "1", "placa": "AAA111", "ciudad": "PALMIRA", "tipo": "PARTICULAR"},
            {"item": "2", "placa": "BBB222", "ciudad": "CALI", "tipo": "PARTICULAR"},
            {"item": "3", "placa": "CCC333", "ciudad": "PALMIRA", "tipo": "PARTICULAR"},
        ]))
        assert pedido.cities == ["PALMIRA", "CALI"]

    def test_multi_word_city_keeps_one_space(self):
        """A wrapped city cell arrives as 'UNION\\nVALLE' and must not keep the newline."""
        pedido = parse_pedido_pdf(build_pedido_pdf(lines=[
            {"item": "1", "placa": "AAA111", "ciudad": "UNION\nVALLE", "tipo": "PARTICULAR"},
        ]))
        assert pedido.lines[0].city == "UNION VALLE"

    def test_plate_is_uppercased_and_despaced(self):
        pedido = parse_pedido_pdf(build_pedido_pdf(lines=[
            {"item": "1", "placa": "abc 12d", "tipo": "MOTO"},
        ]))
        assert pedido.lines[0].plate_number == "ABC12D"

    def test_malformed_plate_is_preserved_not_rejected(self):
        """A real sample contains a 5-character moto plate. The parser reports
        what the document says; the person fixes it in the review form."""
        pedido = parse_pedido_pdf(build_pedido_pdf(lines=[
            {"item": "1", "placa": "KHC71", "tipo": "MOTO"},
        ]))
        assert pedido.lines[0].plate_number == "KHC71"

    def test_footer_rows_after_total_pedido_are_ignored(self, pedido):
        plates = [line.plate_number for line in pedido.lines]
        assert not any(p and "RESPONSABLE" in p for p in plates)


class TestPlateTypeCheckboxes:
    @pytest.mark.parametrize(
        ("column", "expected_type"),
        [
            ("PARTICULAR", "CARRO PARTICULAR"),
            ("PÚBLICA", "CARRO PÚBLICO"),
            ("MOTO", "MOTO PARTICULAR"),
            ("MOTOCARRO PART.", "MOTO CARRO PARTICULAR"),
            ("MOTOCARRO PÚB.", "MOTO CARRO PÚBLICO"),
            ("REMOLQUE", "REMOLQUE"),
        ],
    )
    def test_each_checkbox_column_maps_to_its_canonical_type(self, column, expected_type):
        pedido = parse_pedido_pdf(build_pedido_pdf(lines=[
            {"item": "1", "placa": "AAA111", "tipo": column},
        ]))
        assert pedido.lines[0].plate_type == expected_type

    def test_a_lowercase_mark_still_counts(self):
        pedido = parse_pedido_pdf(build_pedido_pdf(lines=[
            {"item": "1", "placa": "AAA111", "tipo": "REMOLQUE", "marca": "x"},
        ]))
        assert pedido.lines[0].plate_type == "REMOLQUE"

    def test_unmarked_row_leaves_the_type_blank_instead_of_guessing(self):
        """No checkbox means the document didn't say. Blank forces the person
        to choose in the form (the select is `required`) rather than silently
        defaulting to whichever type happens to be first."""
        pedido = parse_pedido_pdf(build_pedido_pdf(lines=[
            {"item": "1", "placa": "AAA111"},
        ]))
        assert pedido.lines[0].plate_number == "AAA111"
        assert pedido.lines[0].plate_type == ""


class TestSourceTypes:
    def test_accepts_bytes(self):
        assert parse_pedido_pdf(build_pedido_pdf()).order_number == "9001"

    def test_accepts_a_filesystem_path(self, tmp_path):
        path = tmp_path / "pedido.pdf"
        path.write_bytes(build_pedido_pdf())
        assert parse_pedido_pdf(str(path)).order_number == "9001"

    def test_accepts_an_open_binary_file_object(self, tmp_path):
        path = tmp_path / "pedido.pdf"
        path.write_bytes(build_pedido_pdf())
        with open(path, "rb") as fh:
            assert parse_pedido_pdf(fh).order_number == "9001"


class TestExtractRowsFromPdf:
    def test_produces_extracted_rows_carrying_the_header_onto_every_line(self):
        rows = extract_rows_from_pdf(build_pedido_pdf(
            order_number="9001", client="CLIENTE DEMO", order_date="11/08/2026",
        ))
        assert len(rows) == 4
        assert {r.order_number for r in rows} == {"9001"}
        assert {r.order_date for r in rows} == {date(2026, 8, 11)}
        assert {r.requested_by for r in rows} == {"CLIENTE DEMO"}

    def test_row_values_match_the_document(self):
        rows = extract_rows_from_pdf(build_pedido_pdf())
        first, ranged = rows[0], rows[3]
        assert (first.plate_number, first.plate_type, first.quantity) == ("ABC12D", "MOTO PARTICULAR", 1)
        assert first.city is None
        assert rows[2].city == "CIUDAD DEMO"
        assert (ranged.plate_number, ranged.plate_to, ranged.quantity) == ("RNG001", "RNG009", 9)

    def test_refuses_to_build_rows_from_an_incomplete_header(self):
        with pytest.raises(PdfExtractError, match="fecha del pedido"):
            extract_rows_from_pdf(build_pedido_pdf(order_date="POR CONFIRMAR"))

    def test_missing_client_is_named_in_the_error(self):
        with pytest.raises(PdfExtractError, match="cliente"):
            extract_rows_from_pdf(build_pedido_pdf(client=""))


class TestFailureModes:
    """Bad input must always raise PdfExtractError (a clear Spanish message the
    route shows), never a raw exception that would surface as a 500."""

    def test_not_a_pdf_at_all(self):
        with pytest.raises(PdfExtractError, match="PDF"):
            parse_pedido_pdf(b"esto no es un PDF")

    def test_empty_upload(self):
        with pytest.raises(PdfExtractError):
            parse_pedido_pdf(b"")

    def test_valid_pdf_that_is_not_a_pedido(self):
        with pytest.raises(PdfExtractError, match="tabla"):
            parse_pedido_pdf(build_pdf_without_table())

    def test_pdf_whose_table_is_not_a_plate_table(self):
        """Gets past 'is there a table?' but has no ITEM/PLACA header row."""
        with pytest.raises(PdfExtractError, match="ITEM / PLACA"):
            parse_pedido_pdf(build_pdf_with_unrelated_table())

    def test_pedido_whose_plate_table_is_entirely_empty(self):
        with pytest.raises(PdfExtractError, match="ninguna placa"):
            parse_pedido_pdf(build_pedido_pdf(lines=[{"item": "1"}, {"item": "2"}]))
