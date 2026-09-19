"""/ingest and /ingest/pdf -- the "Registrar" form (order header + plate
lines, built by hand or pre-filled from an uploaded pedido PDF)."""

from __future__ import annotations

from flask import Blueprint, current_app, render_template, request

from app.core import db
from app.core.ingestion import find_duplicate_plate_numbers, process_rows, replace_order
from app.core.models import ExtractedRow
from app.core.normalize import validate_plate
from app.core.pdf_extract import ParsedPedido, PdfExtractError, parse_pedido_pdf
from app.web.auth import login_required
from app.web.helpers import FORM_PLATE_TYPES, _options, _padded, _validate_order_header, today_iso

bp = Blueprint("ingest", __name__)


def _rows_from_form(form) -> list[ExtractedRow]:
    """Build ExtractedRow(s) from the order header + repeated plate lines.

    line_type drives the flags so the user never types special words:
      placa          -> single plate
      rango          -> consecutive plates: requires 'desde' (plate_number) and 'hasta' (plate_to)
      reposicion     -> big non-consecutive lot (no plate, no range)
      anulada_orden  -> voided order (NOT counted in inventory)
      muestra        -> sample (plate_number = MUESTRA)

    Ciudad is per LINE, not per order: one order can ship plates to several
    cities, so each line carries its own (no header field, no inheritance).
    """
    order_number = form["order_number"].strip()
    order_date = form["order_date"]
    requested_by = form["requested_by"].strip()
    _validate_order_header(requested_by=requested_by, order_date=order_date)

    rows: list[ExtractedRow] = []
    for lt, pt, pn, pto, qty, ct in zip(
        form.getlist("line_type"), form.getlist("plate_type"),
        form.getlist("plate_number"), form.getlist("plate_to"), form.getlist("quantity"),
        _padded(form.getlist("city"), len(form.getlist("plate_type"))), strict=False,
    ):
        if not (pt or "").strip():
            continue  # skip empty line
        plate_number = (pn or "").strip() or None
        plate_to = (pto or "").strip() or None
        is_batch = is_voided = is_plate_annulled = False
        ptype = pt.strip()
        if lt == "reposicion":
            is_batch, plate_number, plate_to = True, None, None
        elif lt == "anulada_orden":
            is_voided, plate_number, plate_to = True, None, None
        elif lt == "muestra":
            plate_number, plate_to = plate_number or "MUESTRA", None
        elif lt == "rango":
            if not (plate_number and plate_to):
                raise ValueError("En 'Rango' tenés que indicar desde y hasta.")
            plate_number = validate_plate(plate_number, ptype)  # desde
            plate_to = validate_plate(plate_to, ptype)          # hasta
        elif lt == "placa_anulada":
            # blank was lost -> counts as consumed, but flagged for waste tracking
            plate_to, is_plate_annulled = None, True
            if plate_number:
                plate_number = validate_plate(plate_number, ptype)
        else:  # placa (single)
            plate_to = None
            if plate_number:
                plate_number = validate_plate(plate_number, ptype)
        rows.append(ExtractedRow(
            order_number=order_number, order_date=order_date, requested_by=requested_by,
            city=(ct or "").strip() or None,
            plate_type=pt.strip(), plate_number=plate_number, plate_to=plate_to,
            quantity=int(qty or 1), is_batch_replacement=is_batch, is_voided=is_voided,
            is_plate_annulled=is_plate_annulled,
        ))
    return rows


def _sticky_lines_from_form(form) -> list[dict]:
    """Re-derive the plate-line table as-typed, to redisplay the form without
    losing work when a duplicate warning interrupts the save."""
    return [
        {"line_type": lt, "plate_type": pt, "plate_number": pn, "plate_to": pto,
         "quantity": qty, "city": ct}
        for lt, pt, pn, pto, qty, ct in zip(
            form.getlist("line_type"), form.getlist("plate_type"),
            form.getlist("plate_number"), form.getlist("plate_to"), form.getlist("quantity"),
            _padded(form.getlist("city"), len(form.getlist("plate_type"))),
            strict=False,
        )
        if (pt or "").strip()
    ]


def _form_from_pedido(pedido: ParsedPedido) -> tuple[dict, list[dict], list[str]]:
    """Turn a parsed PDF into (header, lines, notices) for the /ingest form.

    Deliberately produces the exact same `header`/`lines` shapes the duplicate-
    warning path builds (see `_sticky_lines_from_form`), so the review step is
    the real form, pre-filled — not a second UI that could drift from it.

    Anything the PDF left ambiguous is surfaced as a notice and left BLANK
    rather than guessed, so a wrong value can't ride along unnoticed.
    """
    notices: list[str] = []
    if not pedido.order_number:
        notices.append("No se pudo leer el N° de orden de producción — completalo antes de guardar.")
    if pedido.order_date is None:
        notices.append("No se pudo leer la fecha del pedido — completala antes de guardar.")
    elif pedido.order_date.isoformat() > today_iso():
        notices.append(
            f"La fecha del PDF ({pedido.order_date.isoformat()}) es futura — corregila antes de guardar."
        )
    if not pedido.requested_by:
        notices.append("No se pudo leer el cliente — completalo antes de guardar.")

    no_type = [str(i) for i, line in enumerate(pedido.lines, start=1) if not line.plate_type]
    if no_type:
        notices.append(
            f"El PDF no marca el tipo de placa en la(s) línea(s) {', '.join(no_type)} — "
            "elegilo antes de guardar."
        )

    # City is stored per plate, so a pedido listing several cities needs no
    # disambiguation: each line just carries its own, exactly as the PDF has it.
    header = {
        "order_date": pedido.order_date.isoformat() if pedido.order_date else "",
        "order_number": pedido.order_number,
        "requested_by": pedido.requested_by,
    }
    lines = [
        {
            "line_type": line.line_type,
            "plate_type": line.plate_type,
            "plate_number": line.plate_number,
            "plate_to": line.plate_to,
            "quantity": line.quantity,
            "city": line.city or "",
        }
        for line in pedido.lines
    ]
    return header, lines, notices


@bp.get("/ingest")
@login_required
def ingest():
    with db.get_conn() as conn:
        requesters, cities = _options(conn)
    return render_template("ingest.html", requesters=requesters, cities=cities,
                           categories=FORM_PLATE_TYPES, today=today_iso())


@bp.post("/ingest")
@login_required
def ingest_post():
    summary = None
    error = None
    duplicates = None
    order_duplicates = None
    header = None
    lines = None
    try:
        rows = _rows_from_form(request.form)
        if not rows:
            error = "Agregá al menos una placa."
        elif request.form.get("replace_order"):
            # Explicit, opt-in fuse (see the "Reemplazar" button below): wipe
            # every existing order under this order_number and replace it with
            # exactly one new order built from these rows. The plate-number
            # duplicate check below is moot for this choice -- we're about to
            # delete every plate currently under this order_number outright,
            # so nothing already there can register as "already existing"
            # against the fresh rows being inserted in its place.
            with db.get_conn() as conn:
                summary = replace_order(conn, rows[0].order_number, rows)
        else:
            # One connection for the whole route from here down: the
            # order-number collision check, the plate-number dup check, and
            # the eventual insert all need to see each other's work within a
            # single transaction (previously each opened its own connection,
            # so a single POST /ingest ran across several separate
            # transactions -- not atomic).
            with db.get_conn() as conn:
                # Order-number collision check runs BEFORE the plate-number dup
                # check: a reposición placeholder order re-saved via a re-uploaded
                # PDF has plate_number=NULL, so it would sail right through the
                # plate check and silently create a second production_orders row
                # under the same order_number (see replace_order's docstring for
                # the real WCY449/pedido 1030 case this guards against).
                order_duplicates = db.find_orders_by_number(conn, rows[0].order_number)
                if order_duplicates and not request.form.get("confirm_order_duplicate"):
                    header = {
                        "order_date": request.form.get("order_date", ""),
                        "order_number": request.form.get("order_number", ""),
                        "requested_by": request.form.get("requested_by", ""),
                    }
                    lines = _sticky_lines_from_form(request.form)
                else:
                    order_duplicates = None  # not a collision the user needs to see anymore
                    dup_plate_numbers = find_duplicate_plate_numbers(conn, rows)
                    if dup_plate_numbers and not request.form.get("confirm_duplicate"):
                        duplicates = {
                            pn: db.find_orders_for_plate(conn, pn) for pn in sorted(dup_plate_numbers)
                        }
                        header = {
                            "order_date": request.form.get("order_date", ""),
                            "order_number": request.form.get("order_number", ""),
                            "requested_by": request.form.get("requested_by", ""),
                        }
                        lines = _sticky_lines_from_form(request.form)
                    else:
                        summary = process_rows(conn, rows)
    except Exception as e:  # surface validation/DB errors without crashing
        error = f"Error: {e}"

    with db.get_conn() as conn:
        requesters, cities = _options(conn)
    return render_template(
        "ingest.html", requesters=requesters, cities=cities,
        categories=FORM_PLATE_TYPES, today=today_iso(),
        summary=summary.model_dump() if summary else None, error=error,
        duplicates=duplicates, order_duplicates=order_duplicates, header=header, lines=lines,
    )


@bp.route("/ingest/pdf", methods=["GET", "POST"])
@login_required
def ingest_pdf():
    """Upload a pedido PDF (GET) and review the parse in the normal form (POST).

    POST intentionally does NOT save: it renders `ingest.html` pre-filled, so
    the person reviews/corrects and then submits to POST /ingest exactly like a
    hand-typed order — including the duplicate-plate warning gate, for free.
    """
    if request.method == "GET":
        return render_template("ingest_pdf.html")

    upload = request.files.get("pdf")
    if upload is None or not upload.filename:
        return render_template("ingest_pdf.html", error="Elegí un archivo PDF para subir."), 400

    try:
        pedido = parse_pedido_pdf(upload.read())
    except PdfExtractError as e:
        return render_template("ingest_pdf.html", error=str(e)), 400
    except Exception:  # never 500 on a bad upload — always offer manual entry
        current_app.logger.exception("Unexpected failure parsing uploaded pedido PDF")
        return render_template(
            "ingest_pdf.html",
            error="No se pudo procesar el PDF. Ingresá la orden manualmente.",
        ), 400

    header, lines, notices = _form_from_pedido(pedido)
    with db.get_conn() as conn:
        # Read-only: surface "esta orden ya existe" as soon as the PDF is
        # previewed, not only after a first failed POST /ingest save attempt.
        # This route never writes to the DB either way.
        order_duplicates = (
            db.find_orders_by_number(conn, pedido.order_number) if pedido.order_number else []
        )
        requesters, cities = _options(conn)
    return render_template(
        "ingest.html", requesters=requesters, cities=cities,
        categories=FORM_PLATE_TYPES, today=today_iso(),
        header=header, lines=lines, notices=notices, order_duplicates=order_duplicates,
    )
