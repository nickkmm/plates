"""Parse INGSE's fixed-format "pedido" PDF into review-ready order lines.

This is a new ingestion door: instead of typing every plate into /ingest by
hand, the person uploads the client-facing pedido PDF, this module turns it
into the SAME shape the manual form uses, and they review/correct on screen
before saving through the usual `core.process.process_rows` backend.

Why `extract_tables()` and not `extract_text()`
-----------------------------------------------
`extract_text()` walks the raw character stream in emission order, which in at
least one real sample scrambles the static labels ("FECHA PEDIDO:" comes out
as "F O E R C D H E A N P D E E D IDO:"). `extract_tables()` reconstructs cells
from the ruling-line geometry instead, so it is immune to that and returns
clean rows for every sample we have. Never switch this to extract_text().

Robustness stance
-----------------
Nothing here validates plates or business rules -- real pedidos contain typos
(a 5-character moto plate, a missing type checkbox) and the whole point of the
flow is that a human reviews the result. The parser's job is to be *faithful*,
not clever: it reports what the document says, leaves genuinely unknown fields
empty for the person to fill, and raises `PdfExtractError` (never crashes) when
the document isn't a pedido at all.

Column positions are read from the document's own "ITEM / PLACA / ..." header
row rather than hardcoded, so a template revision that shifts or inserts a
column keeps working.
"""

from __future__ import annotations

import io
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import IO, Any

import pdfplumber
from pydantic import BaseModel

from app.core.models import ExtractedRow
from app.core.normalize import PLATE_TYPES, clean_plate

PdfSource = str | Path | bytes | bytearray | IO[bytes]


class PdfExtractError(Exception):
    """The upload isn't a readable pedido PDF. Message is user-facing Spanish."""


# --- text helpers -----------------------------------------------------------

_WS = re.compile(r"\s+")


def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def _cell_text(cell: object) -> str:
    """Cell value as clean single-line text ('UNION\\nVALLE' -> 'UNION VALLE')."""
    if cell is None:
        return ""
    return _WS.sub(" ", str(cell)).strip()


def _fold(text: str) -> str:
    """Uppercase, accent-stripped, single-spaced -- for label/header matching only."""
    return _WS.sub(" ", _strip_accents(text).upper()).strip()


def _key(text: str) -> str:
    """Header-cell matching key: folded, without the trailing dot ('CANT.' -> 'CANT')."""
    return _fold(text).rstrip(".")


def _split_label(text: str) -> tuple[str, str]:
    """Split a 'LABEL: value' cell into (folded_label, inline_value).

    Handles both layouts seen in the wild: the value in its own cell
    ('CLIENTE:' | 'MAGDA') and the value inline after the colon
    ('NIT / CEDULA: 1117521679'). Returns ('', '') when there is no colon,
    which is how a plain value cell is told apart from a label cell.
    """
    label, sep, rest = text.partition(":")
    if not sep:
        return "", ""
    return _fold(label), rest.strip()


# --- date parsing -----------------------------------------------------------

# The two real samples disagree on format for the SAME field: one exports
# '11/08/2026', the other the spelled-out '13 DE AGOSTO DE 2026'. Both are
# handled, plus ISO, so the v2 template (a real date cell) and legacy
# free-text documents all land on the same `date`.
_MONTHS = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6,
    "JULIO": 7, "AGOSTO": 8, "SEPTIEMBRE": 9, "SETIEMBRE": 9, "OCTUBRE": 10,
    "NOVIEMBRE": 11, "DICIEMBRE": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

_ISO_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_NUMERIC_RE = re.compile(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})")
# '13 DE AGOSTO DE 2026' and 'AGOSTO 13 DE 2026' -- \D+ absorbs the ' DE ' filler.
_SPANISH_DMY_RE = re.compile(rf"(\d{{1,2}})\D+({_MONTH_ALT})\D+(\d{{4}})")
_SPANISH_MDY_RE = re.compile(rf"({_MONTH_ALT})\D+(\d{{1,2}})\D+(\d{{4}})")


def _make_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_order_date(value: str) -> date | None:
    """Parse a FECHA PEDIDO value in any format the pedido has ever used.

    Order of attempts: ISO (unambiguous), numeric day-first (Colombian
    convention, falling back to month-first only when day-first is an
    impossible date), then a Spanish month-name form. Returns None when the
    value is blank or unrecognizable -- the caller leaves the field empty for
    the person to fill rather than guessing a wrong date.
    """
    s = _cell_text(value)
    if not s:
        return None

    if m := _ISO_RE.fullmatch(s):
        return _make_date(int(m[1]), int(m[2]), int(m[3]))

    if m := _NUMERIC_RE.fullmatch(s):
        first, second, year = int(m[1]), int(m[2]), int(m[3])
        if year < 100:
            year += 2000
        # Day-first (11/08/2026 = 11 Aug). If that's impossible (08/13/2026),
        # the document must have been written month-first, so swap.
        return _make_date(year, second, first) or _make_date(year, first, second)

    folded = _fold(s)
    if m := _SPANISH_DMY_RE.search(folded):
        return _make_date(int(m[3]), _MONTHS[m[2]], int(m[1]))
    if m := _SPANISH_MDY_RE.search(folded):
        return _make_date(int(m[3]), _MONTHS[m[1]], int(m[2]))
    return None


def _parse_quantity(value: str) -> int:
    """CANT. cell -> quantity. Blank means 1, matching the manual form's default."""
    digits = re.sub(r"\D", "", _cell_text(value))
    if not digits:
        return 1
    try:
        return int(digits)
    except ValueError:  # pragma: no cover - digits-only string always parses
        return 1


# --- the pedido layout ------------------------------------------------------

# Header-field labels we care about. Everything else on the document (NIT,
# GUÍA #, VALOR, FECHA ENTREGA, FECHA TRANSF./PAGO, TRÁNSITO, the per-row
# money columns, TOTAL PEDIDO and the whole footer) has no home in the schema
# and is deliberately ignored.
_LABEL_CLIENTE = "CLIENTE"
_LABEL_FECHA = "FECHA PEDIDO"
# The production order carries two names across template generations: the
# current documents print 'ORDEN DE PRODUCCION:', older ones label the same
# field 'NT:'. Both are accepted so documents sent during the changeover keep
# working; the explicit label is preferred when a document somehow has both.
# 'PEDIDO #' is deliberately absent — it is the client-facing claim number.
_LABELS_ORDEN = ("ORDEN DE PRODUCCION", "NT")

# The plate-line table's checkbox columns -> the canonical plate TYPE each one
# means. Values must exist in normalize.PLATE_TYPES; the check below turns any
# future drift in that tuple into a loud import-time failure instead of rows
# that silently fail to save.
_CHECKBOX_COLUMNS = {
    "PARTICULAR": "CARRO PARTICULAR",
    "PUBLICA": "CARRO PÚBLICO",
    "MOTO": "MOTO PARTICULAR",
    "MOTOCARRO PART": "MOTO CARRO PARTICULAR",
    "MOTOCARRO PUB": "MOTO CARRO PÚBLICO",
    "REMOLQUE": "REMOLQUE",
}
_UNKNOWN_TYPES = set(_CHECKBOX_COLUMNS.values()) - set(PLATE_TYPES)
if _UNKNOWN_TYPES:  # pragma: no cover - guards against edits to PLATE_TYPES
    raise RuntimeError(
        f"pdf_extract checkbox map is out of sync with normalize.PLATE_TYPES: {sorted(_UNKNOWN_TYPES)}"
    )

_COL_ITEM = "ITEM"
_COL_PLACA = "PLACA"
_COL_CIUDAD = "CIUDAD"
_COL_CANT = "CANT"
_COL_RANGO_INI = "RANGO INICIAL"
_COL_RANGO_FIN = "RANGO FINAL"

# First cell of the row that ends the plate table.
_END_OF_LINES = "TOTAL PEDIDO"


class ParsedLine(BaseModel):
    """One plate line, already shaped like the /ingest form's table row."""

    line_type: str  # "placa" | "rango" -- the only kinds a pedido can express
    plate_type: str  # "" when the document marked no type checkbox
    plate_number: str | None = None
    plate_to: str | None = None
    quantity: int = 1
    city: str | None = None


class ParsedPedido(BaseModel):
    """A parsed pedido: header fields plus its plate lines.

    Header fields may be empty/None when the document left them blank or wrote
    them unrecognizably -- that is deliberate, so one bad field never costs the
    person the other 30 correctly-parsed plate lines.
    """

    order_number: str = ""
    order_date: date | None = None
    requested_by: str = ""
    lines: list[ParsedLine] = []

    @property
    def cities(self) -> list[str]:
        """Distinct non-empty per-line cities, in order of first appearance."""
        return list(dict.fromkeys(line.city for line in self.lines if line.city))

    def to_rows(self) -> list[ExtractedRow]:
        """Convert to the shared ingestion shape. Requires a complete header."""
        missing = [
            name
            for name, value in (
                ("N° de orden de producción", self.order_number),
                ("fecha del pedido", self.order_date),
                ("cliente", self.requested_by),
            )
            if not value
        ]
        if missing:
            raise PdfExtractError(
                "No se pudo leer del PDF: " + ", ".join(missing) + ". Completá esos datos a mano."
            )
        assert self.order_date is not None  # narrowed by the `missing` check above
        return [
            ExtractedRow(
                order_number=self.order_number,
                order_date=self.order_date,
                requested_by=self.requested_by,
                plate_type=line.plate_type,
                city=line.city,
                plate_number=line.plate_number,
                plate_to=line.plate_to,
                quantity=line.quantity,
            )
            for line in self.lines
        ]


# --- extraction -------------------------------------------------------------


def _open_pdf(source: PdfSource) -> Any:
    if isinstance(source, bytes | bytearray):
        return pdfplumber.open(io.BytesIO(bytes(source)))
    if isinstance(source, str | Path):
        return pdfplumber.open(source)
    # Any other binary file object (e.g. an uploaded FileStorage stream) is
    # read into memory first, so pdfplumber always gets a seekable buffer even
    # if the caller handed us a one-shot stream.
    return pdfplumber.open(io.BytesIO(source.read()))


def _table_rows(source: PdfSource) -> list[list[Any]]:
    """Every table row on every page, concatenated.

    Real pedidos are one page with one table, but concatenating means a
    two-page order would keep its overflow lines instead of silently losing
    them.
    """
    try:
        with _open_pdf(source) as pdf:
            rows: list[list[Any]] = []
            for page in pdf.pages:
                for table in page.extract_tables():
                    rows.extend(table)
            return rows
    except Exception as e:  # noqa: BLE001 - any pdfminer/pypdfium failure means "not a usable PDF"
        raise PdfExtractError(
            "No se pudo leer el archivo. Verificá que sea un PDF de pedido válido (no una imagen escaneada)."
        ) from e


def _find_plate_header(rows: list[list[Any]]) -> tuple[int, dict[str, int]]:
    """Locate the 'ITEM | PLACA | ...' row and map each header name to its column.

    Reading positions off the document itself (instead of hardcoding indices)
    is what lets one parser serve both the legacy documents and a revised
    template whose columns may sit elsewhere.
    """
    for i, row in enumerate(rows):
        keys = [_key(_cell_text(c)) for c in row]
        if not keys or keys[0] != _COL_ITEM or _COL_PLACA not in keys:
            continue
        columns: dict[str, int] = {}
        for j, k in enumerate(keys):
            if k and k not in columns:  # first occurrence wins
                columns[k] = j
        return i, columns
    raise PdfExtractError(
        "El PDF no tiene el formato de pedido esperado: no se encontró la tabla de placas "
        "(fila con ITEM / PLACA). Ingresá la orden manualmente."
    )


def _find_header_value(rows: list[list[Any]], label: str) -> str:
    """Value of a 'LABEL:' field, whether it sits inline or in a later cell.

    Matches the label exactly (not as a substring) so 'FECHA PEDIDO' and
    'PEDIDO #' can never be confused -- which matters because PEDIDO # is the
    client-facing claim number and must NEVER be read as the production order.
    """
    target = _fold(label)
    for row in rows:
        for i, cell in enumerate(row):
            found, inline = _split_label(_cell_text(cell))
            if found != target:
                continue
            if inline:
                return inline
            for nxt in row[i + 1:]:
                text = _cell_text(nxt)
                if not text:
                    continue
                next_label, _ = _split_label(text)
                if next_label:
                    return ""  # next filled cell is another field's label: this one is blank
                return text
            return ""
    return ""


def _find_first_header_value(rows: list[list[Any]], labels: tuple[str, ...]) -> str:
    """First non-empty value among several accepted labels for one field."""
    for label in labels:
        value = _find_header_value(rows, label)
        if value:
            return value
    return ""


def _plate_type_for(row: list[Any], columns: dict[str, int]) -> str:
    """Which type checkbox is marked. '' when none is -- never guessed."""
    for header, plate_type in _CHECKBOX_COLUMNS.items():
        if _cell_at(row, columns, header):
            return plate_type
    return ""


def _cell_at(row: list[Any], columns: dict[str, int], header: str) -> str:
    j = columns.get(header)
    if j is None or j >= len(row):
        return ""
    return _cell_text(row[j])


def _parse_lines(rows: list[list[Any]], columns: dict[str, int]) -> list[ParsedLine]:
    """Plate lines, from just after the header row to TOTAL PEDIDO.

    A row counts as a plate line only if it actually carries a plate or a
    range start. That single rule handles both real-world quirks at once: the
    pre-printed empty rows that have an ITEM number and nothing else (skipped)
    and the rows with a plate but a MISSING ITEM number (kept) -- so ITEM is
    never assumed to be sequential or even present.
    """
    lines: list[ParsedLine] = []
    for row in rows:
        if row and _fold(_cell_text(row[0])).startswith(_END_OF_LINES):
            break
        plate = clean_plate(_cell_at(row, columns, _COL_PLACA))
        rango_ini = clean_plate(_cell_at(row, columns, _COL_RANGO_INI))
        rango_fin = clean_plate(_cell_at(row, columns, _COL_RANGO_FIN))
        if not (plate or rango_ini):
            continue

        if rango_ini and rango_fin:
            # A filled Rango Final is unambiguous evidence of a consecutive run.
            line_type, plate_number, plate_to = "rango", rango_ini, rango_fin
        else:
            line_type, plate_number, plate_to = "placa", plate or rango_ini, None

        lines.append(
            ParsedLine(
                line_type=line_type,
                plate_type=_plate_type_for(row, columns),
                plate_number=plate_number or None,
                plate_to=plate_to,
                quantity=_parse_quantity(_cell_at(row, columns, _COL_CANT)),
                city=_cell_at(row, columns, _COL_CIUDAD) or None,
            )
        )
    return lines


def parse_pedido_pdf(source: PdfSource) -> ParsedPedido:
    """Parse a pedido PDF. Raises PdfExtractError if it isn't one.

    `source` may be a path, raw bytes, or an open binary file object (e.g.
    Flask's uploaded `FileStorage.stream`).
    """
    rows = _table_rows(source)
    if not rows:
        raise PdfExtractError(
            "El PDF no contiene ninguna tabla. Si es un documento escaneado, "
            "ingresá la orden manualmente."
        )

    header_index, columns = _find_plate_header(rows)
    header_rows = rows[:header_index]  # fields live above the plate table

    pedido = ParsedPedido(
        # ORDEN DE PRODUCCION (or its older name NT), never PEDIDO # -- they
        # are different numbers and only this one identifies the production order.
        order_number=_find_first_header_value(header_rows, _LABELS_ORDEN),
        order_date=parse_order_date(_find_header_value(header_rows, _LABEL_FECHA)),
        requested_by=_find_header_value(header_rows, _LABEL_CLIENTE),
        lines=_parse_lines(rows[header_index + 1:], columns),
    )
    if not pedido.lines:
        raise PdfExtractError(
            "No se encontró ninguna placa en el PDF. Revisá el archivo o ingresá la orden manualmente."
        )
    return pedido


def extract_rows_from_pdf(source: PdfSource) -> list[ExtractedRow]:
    """Parse a pedido PDF straight into the shared ingestion shape.

    Convenience for non-interactive callers. The web flow uses
    `parse_pedido_pdf` instead, because it tolerates an incomplete header and
    lets the person fill the gaps before anything is saved.
    """
    return parse_pedido_pdf(source).to_rows()
