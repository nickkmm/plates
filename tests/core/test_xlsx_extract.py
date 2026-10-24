"""Unit tests for app.core.xlsx_extract / pedido_upload -- no DB, no Flask.

The .xlsx files are generated on the fly from the same cell grid the PDF
fixtures use, so every pedido here exists in both formats.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime

import openpyxl
import pytest

from app.core import xlsx_extract
from app.core.pdf_extract import PdfExtractError, parse_pedido_pdf
from app.core.pedido_upload import parse_uploaded_pedido
from app.core.xlsx_extract import parse_pedido_xlsx
from tests.pdf_fixtures import build_pedido_pdf, build_pedido_xlsx

FULL = {
    "nit": "900123456", "guia": "G-55", "fecha_entrega": "15/08/2026", "fecha_pago": "12/08/2026",
    "responsable": "JUAN", "telefono": "3001234567", "recibe": "MARIA", "forma_pago": "EFECTIVO",
    "observaciones": "URGENTE",
}


class TestSameResultAsThePdf:
    def test_default_pedido(self):
        assert parse_pedido_xlsx(build_pedido_xlsx()) == parse_pedido_pdf(build_pedido_pdf())

    def test_every_header_and_footer_field(self):
        assert parse_pedido_xlsx(build_pedido_xlsx(**FULL)) == parse_pedido_pdf(build_pedido_pdf(**FULL))

    def test_old_nt_label_and_pedido_number_decoy(self):
        kwargs = {"order_label": "NT:", "order_row_leaks_bare_pedido_label": True, "pedido_numero": "28"}
        pedido = parse_pedido_xlsx(build_pedido_xlsx(**kwargs))
        assert pedido == parse_pedido_pdf(build_pedido_pdf(**kwargs))
        assert pedido.order_number == "9001" and pedido.client_order_number == "28"

    def test_nit_in_the_next_row_is_never_picked_up(self):
        pedido = parse_pedido_xlsx(build_pedido_xlsx(nit="999", nit_value_in_next_row=True))
        assert pedido.customer_document_number == ""


class TestTypedCells:
    def test_a_real_date_cell_becomes_the_order_date(self):
        assert parse_pedido_xlsx(build_pedido_xlsx(order_date="13/08/2026")).order_date == date(2026, 8, 13)

    def test_text_dates_still_work(self):
        pedido = parse_pedido_xlsx(build_pedido_xlsx(order_date="13 DE AGOSTO DE 2026", typed_dates=False))
        assert pedido.order_date == date(2026, 8, 13)

    def test_numbers_have_no_decimal_point(self):
        pedido = parse_pedido_xlsx(build_pedido_xlsx(nit="1117521679"))
        assert pedido.customer_document_number == "1117521679"
        assert [line.quantity for line in pedido.lines] == [1, 1, 2, 9]

    def test_cell_value_conversion(self):
        convert = xlsx_extract._cell_value
        assert convert(None) == "" and convert(True) == ""
        assert convert(1117521679.0) == "1117521679" and convert(2.5) == "2.5" and convert(7) == "7"
        assert convert(datetime(2026, 8, 13, 0, 0)) == "2026-08-13" and convert(date(2026, 8, 13)) == "2026-08-13"
        assert convert("  texto ") == "  texto "

    def test_a_helper_sheet_before_the_pedido_is_ignored(self):
        data = build_pedido_xlsx(extra_sheets_first=True)
        assert parse_pedido_xlsx(data) == parse_pedido_pdf(build_pedido_pdf())

    def test_accepts_a_file_object(self):
        assert parse_pedido_xlsx(io.BytesIO(build_pedido_xlsx())).order_number == "9001"


class TestFailureModes:
    def test_not_a_zip(self):
        with pytest.raises(PdfExtractError, match="Excel"):
            parse_pedido_xlsx(b"no es un excel")

    def test_zip_without_a_workbook(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("doc.txt", "hola")
        with pytest.raises(PdfExtractError, match="Excel"):
            parse_pedido_xlsx(buf.getvalue())

    def test_zip_that_decompresses_to_too_much(self, monkeypatch):
        monkeypatch.setattr(xlsx_extract, "MAX_UNCOMPRESSED_BYTES", 100)
        with pytest.raises(PdfExtractError, match="demasiado grande"):
            parse_pedido_xlsx(build_pedido_xlsx())

    def test_workbook_without_the_plate_table(self):
        wb = openpyxl.Workbook()
        wb.active.append(["otra cosa", "x"])
        buf = io.BytesIO()
        wb.save(buf)
        with pytest.raises(PdfExtractError, match="ITEM / PLACA"):
            parse_pedido_xlsx(buf.getvalue())

    def test_plate_table_entirely_empty(self):
        with pytest.raises(PdfExtractError, match="ninguna placa"):
            parse_pedido_xlsx(build_pedido_xlsx(lines=[{"item": "1"}, {"item": "2"}]))


class TestUploadDispatch:
    def test_pdf(self):
        assert parse_uploaded_pedido(build_pedido_pdf()).order_number == "9001"

    def test_xlsx(self):
        assert parse_uploaded_pedido(build_pedido_xlsx()).order_number == "9001"

    def test_empty(self):
        with pytest.raises(PdfExtractError, match="vacío"):
            parse_uploaded_pedido(b"")

    def test_legacy_xls(self):
        with pytest.raises(PdfExtractError, match=r"\.xlsx"):
            parse_uploaded_pedido(b"\xd0\xcf\x11\xe0" + b"\x00" * 32)

    def test_anything_else(self):
        with pytest.raises(PdfExtractError, match="PDF"):
            parse_uploaded_pedido(b"hola mundo")
