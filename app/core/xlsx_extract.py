"""Parse INGSE's "pedido" Excel template (.xlsx) into review-ready order lines.

Same job as `pdf_extract.parse_pedido_pdf`, other door: the client fills the
Excel template and sends the .xlsx as is. The sheet is the same cell grid the
PDF tables yield (CLIENTE:, FECHA PEDIDO:, the ITEM | PLACA | ... header row,
TOTAL PEDIDO:, the footer), so this module only turns worksheet rows into
`list[list[str]]` and hands them to the shared `pdf_extract.parse_pedido_rows`.

Excel hands over typed values; they are rendered as the text the PDF would
have shown (dates ISO, whole numbers without ".0") because the shared parser
works on text. The files come from outside INGSE, so the zip is checked
before openpyxl opens it and only a bounded block of cells is read.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime
from typing import IO, Any

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

from app.core.pdf_extract import ParsedPedido, PdfExtractError, parse_pedido_rows

XlsxSource = bytes | bytearray | IO[bytes]

MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024  # zip-bomb guard (uploads are capped at 12 MB)
MAX_ZIP_ENTRIES = 1000
MAX_ROWS = 500   # a pedido is ~45 rows; the plate table has 20
MAX_COLUMNS = 40  # the template uses 14

_NOT_XLSX = "No se pudo leer el archivo. Verificá que sea un Excel (.xlsx) del formato de pedido."


def _check_zip(data: bytes) -> None:
    """Reject anything that is not a plain, reasonably sized .xlsx container."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            names = {info.filename for info in infos}
            if "xl/workbook.xml" not in names:
                raise PdfExtractError(_NOT_XLSX)
            if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
                raise PdfExtractError("El archivo Excel es demasiado grande para ser un pedido.")
    except zipfile.BadZipFile as e:
        raise PdfExtractError(_NOT_XLSX) from e


def _cell_value(value: Any) -> str:
    """A worksheet value as the text the shared parser expects."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return ""  # a stray TRUE/FALSE is never pedido content
    if isinstance(value, float) and value.is_integer():
        return str(int(value))  # 1117521679.0 -> "1117521679"
    return str(value)


def _sheet_rows(sheet: Any) -> list[list[Any]]:
    return [
        [_cell_value(v) for v in row]
        for row in sheet.iter_rows(max_row=MAX_ROWS, max_col=MAX_COLUMNS, values_only=True)
    ]


def parse_pedido_xlsx(source: XlsxSource) -> ParsedPedido:
    """Parse a pedido .xlsx. Raises PdfExtractError if it isn't one.

    Uses the first sheet that has the plate table (ITEM / PLACA header row),
    so helper sheets in a customised template are ignored.
    """
    data = bytes(source) if isinstance(source, bytes | bytearray) else source.read()
    _check_zip(data)
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (zipfile.BadZipFile, InvalidFileException, KeyError, ValueError, OSError) as e:
        raise PdfExtractError(_NOT_XLSX) from e
    try:
        errors: list[PdfExtractError] = []
        for sheet in workbook.worksheets:
            try:
                return parse_pedido_rows(_sheet_rows(sheet))
            except PdfExtractError as e:
                errors.append(e)
        raise errors[0] if errors else PdfExtractError(_NOT_XLSX)
    finally:
        workbook.close()
