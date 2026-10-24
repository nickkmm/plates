"""One entry point for an uploaded pedido, whatever format it came in.

The file is recognised by its content, never by its extension (people rename
files and browsers guess MIME types): PDF -> `pdf_extract`, .xlsx ->
`xlsx_extract`. Anything else raises `PdfExtractError` with a Spanish message
the routes show as is.
"""

from __future__ import annotations

from app.core.pdf_extract import ParsedPedido, PdfExtractError, parse_pedido_pdf
from app.core.xlsx_extract import parse_pedido_xlsx

_PDF_MAGIC = b"%PDF"
_ZIP_MAGIC = b"PK\x03\x04"           # .xlsx is a zip container
_OLE_MAGIC = b"\xd0\xcf\x11\xe0"     # legacy .xls

UNSUPPORTED_MESSAGE = "No reconocimos el archivo. Subí el PDF del pedido o el Excel (.xlsx)."


def parse_uploaded_pedido(data: bytes) -> ParsedPedido:
    """Parse an uploaded pedido (PDF or .xlsx). Raises PdfExtractError."""
    if not data:
        raise PdfExtractError("El archivo está vacío.")
    # The PDF spec allows a little junk before the header; readers look in the first KB.
    if _PDF_MAGIC in data[:1024]:
        return parse_pedido_pdf(data)
    if data.startswith(_ZIP_MAGIC):
        return parse_pedido_xlsx(data)
    if data.startswith(_OLE_MAGIC):
        raise PdfExtractError(
            "Ese es un Excel antiguo (.xls). Abrilo y guardalo como «Libro de Excel (.xlsx)», y volvé a subirlo."
        )
    raise PdfExtractError(UNSUPPORTED_MESSAGE)
