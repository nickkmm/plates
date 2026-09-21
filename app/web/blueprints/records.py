"""/records/* -- browse/search existing orders, view and edit one order's
full detail, and a PDF-based consult-only duplicate check."""

from __future__ import annotations

from flask import Blueprint, current_app, redirect, render_template, request, session, url_for

from app.core import db
from app.core.ingestion import find_duplicate_plate_numbers
from app.core.models import ExtractedRow
from app.core.normalize import (
    PAYMENT_METHODS,
    REASONS,
    RECEIPT_TYPES,
    clean_plate,
    is_real_plate,
    normalize_choice,
    normalize_row,
    validate_plate,
)
from app.core.pdf_extract import PdfExtractError, parse_pedido_pdf
from app.web.auth import consulta_allowed, login_required, pos_allowed
from app.web.helpers import FORM_PLATE_TYPES, _options, _padded, _validate_order_header, today_iso

bp = Blueprint("records", __name__)


def _order_level_fields_from_form(form) -> dict[str, str | None]:
    """The Fase 3 POS fields that live on production_orders (one real value
    per order), read once from the Registro-detail edit form -- same fields
    /ingest's _rows_from_form reads, minus payment_method's "required"
    enforcement: record_save also edits orders that predate this feature
    (payment_method genuinely NULL), so it stays a plain optional <select>
    here rather than forcing a guess just to save an unrelated correction
    (e.g. fixing a typo in a plate number).
    """
    return {
        "receipt_type": normalize_choice(form.get("receipt_type"), RECEIPT_TYPES),
        "receipt_number": (form.get("receipt_number") or "").strip() or None,
        "payment_method": normalize_choice(form.get("payment_method"), PAYMENT_METHODS),
        "tracking_number": (form.get("tracking_number") or "").strip() or None,
        "client_order_number": (form.get("client_order_number") or "").strip() or None,
        "delivery_date": (form.get("delivery_date") or "").strip() or None,
        "payment_date": (form.get("payment_date") or "").strip() or None,
        "dispatched_by": (form.get("dispatched_by") or "").strip() or None,
        "received_by": (form.get("received_by") or "").strip() or None,
        "notes": (form.get("notes") or "").strip() or None,
    }


def _edit_rows_from_form(form, *, order_number: str, order_date: str, requested_by: str,
                         order_voided: bool,
                         order_level_fields: dict[str, str | None]) -> list[tuple[int | None, ExtractedRow]]:
    """Parse the Registro edit form's plate rows into (plate_id, ExtractedRow)
    pairs -- a None plate_id means a newly-added row (INSERT), otherwise it's
    an existing plate being saved (UPDATE). The caller infers deletes from
    which existing ids are simply absent here (removed client-side before
    saving).

    is_voided means "the whole order was voided" -- a single header-level
    checkbox (order_voided), applied uniformly to every row here -- not a
    per-plate choice; the schema just happens to store it on `plates` rather
    than `production_orders`. is_plate_annulled stays genuinely per-plate
    (one specific blank was lost during production).

    order_level_fields (Fase 3) is stamped onto every row's ExtractedRow,
    same as order_number/order_date/requested_by -- it's only actually
    WRITTEN via db.update_order (once, at the order level), never re-read off
    these per-row copies, but ExtractedRow needs a value for every field
    regardless of which layer ultimately uses it. reason/vehicle_brand/
    unit_price are genuinely per-line -- read straight off each row's own
    columns, _padded(...)-wrapped like city already is (an optional per-line
    text field that's shorter than the required plate_type list would
    otherwise silently truncate the zip and drop rows).
    """
    plate_types = form.getlist("plate_type")
    n = len(plate_types)
    rows: list[tuple[int | None, ExtractedRow]] = []
    for pid, pt, pn, pto, ct, qty, annulled, rs, vb, up in zip(
        _padded(form.getlist("plate_id"), n), plate_types,
        form.getlist("plate_number"), form.getlist("plate_to"),
        _padded(form.getlist("city"), n), form.getlist("quantity"),
        form.getlist("is_plate_annulled"),
        _padded(form.getlist("reason"), n),
        _padded(form.getlist("vehicle_brand"), n),
        _padded(form.getlist("unit_price"), n),
        strict=False,
    ):
        if not (pt or "").strip():
            continue  # skip empty line
        ptype = pt.strip()
        plate_number = (pn or "").strip() or None
        plate_to = (pto or "").strip() or None
        if plate_number:
            plate_number = validate_plate(plate_number, ptype)
        if plate_to:
            plate_to = validate_plate(plate_to, ptype)
        row = ExtractedRow(
            order_number=order_number, order_date=order_date, requested_by=requested_by,
            city=(ct or "").strip() or None, plate_type=ptype,
            plate_number=plate_number, plate_to=plate_to, quantity=int(qty or 1),
            is_voided=order_voided, is_plate_annulled=annulled == "on",
            reason=normalize_choice(rs, REASONS), vehicle_brand=(vb or "").strip() or None,
            unit_price=(up or "").strip() or None,
            **order_level_fields,
        )
        rows.append((int(pid) if pid else None, row))
    return rows


@bp.get("/records")
@consulta_allowed
def records():
    query = (request.args.get("q") or "").strip()
    with db.get_conn() as conn:
        if query:
            results = db.search_plates_by_number(conn, query)
            order_matches = db.search_orders_by_number(conn, query)
            rows = None
        else:
            results = None
            order_matches = None
            rows = db.recent_orders(conn)
    return render_template("records.html", rows=rows, query=query, results=results,
                           order_matches=order_matches, pdf_results=None)


def _order_voided(plates: list[dict]) -> bool:
    """Whether the ORDER is voided -- stored per-plate in the schema, but
    meant as one whole-order fact (an 'anulada_orden' line always applies to
    every plate at once), so the UI shows/edits it once at the header."""
    return any(p["is_voided"] for p in plates)


@bp.get("/records/<int:order_id>")
@consulta_allowed
def record_detail(order_id):
    # ?q= only ever arrives here as a passthrough from a Registros search
    # result's own "Ver" link (see records.html/_orders_table.html), so
    # "Volver a Registros" can send the person back to their actual search
    # instead of always landing on the unfiltered recent-orders list.
    back_query = (request.args.get("q") or "").strip()
    with db.get_conn() as conn:
        order, plates = db.order_detail(conn, order_id)
        requesters, cities = _options(conn)
        prices = {pt: str(price) for pt, price in db.current_prices(conn).items()}
    if not order:
        return redirect(url_for("records.records"))
    return render_template("record_detail.html", order=order, plates=plates,
                           cities=cities, requesters=requesters, categories=FORM_PLATE_TYPES,
                           receipt_types=RECEIPT_TYPES, payment_methods=PAYMENT_METHODS,
                           reasons=REASONS, prices=prices,
                           today=today_iso(), order_voided=_order_voided(plates),
                           back_query=back_query)


def _record_detail_error(order_id: int, error: str):
    """Re-render the Registro detail page with a validation error, instead of
    losing the person's edits on a bad plate number. Shared by record_save."""
    with db.get_conn() as conn:
        order, plates = db.order_detail(conn, order_id)
        requesters, cities = _options(conn)
        prices = {pt: str(price) for pt, price in db.current_prices(conn).items()}
    return render_template("record_detail.html", order=order, plates=plates,
                           cities=cities, requesters=requesters, categories=FORM_PLATE_TYPES,
                           receipt_types=RECEIPT_TYPES, payment_methods=PAYMENT_METHODS,
                           reasons=REASONS, prices=prices,
                           today=today_iso(), order_voided=_order_voided(plates), error=error), 400


@bp.post("/records/<int:order_id>/save")
@pos_allowed
def record_save(order_id):
    """Save every change made on the Registro detail page in one shot: the
    order header (date/number/solicitado por/anulada/POS fields), edits to
    existing plates, any new plate rows added, and the removal of any row the
    person deleted client-side -- one form, one button, same shape as
    /ingest's "Guardar orden" rather than a separate save per row.

    Fase 3: the customer this order points to is re-resolved from
    `requested_by` on every save (db.resolve_or_create_customer, name only)
    -- per the plan's B5, this may only change WHICH customer the order
    points to, never rewrite an existing customer's own stored attributes
    (type/document/phone/etc; that's out of scope here, deferred to a future
    dedicated customers screen), so no customer attribute is read from this
    form at all.
    """
    order_number = (request.form.get("order_number") or "").strip()
    order_date = request.form.get("order_date") or ""
    requested_by = (request.form.get("requested_by") or "").strip()
    order_voided = request.form.get("order_voided") == "on"
    order_level_fields = _order_level_fields_from_form(request.form)

    try:
        _validate_order_header(requested_by=requested_by, order_date=order_date)
        edits = _edit_rows_from_form(request.form, order_number=order_number,
                                     order_date=order_date, requested_by=requested_by,
                                     order_voided=order_voided, order_level_fields=order_level_fields)
        with db.get_conn() as conn:
            existing_order, existing_plates = db.order_detail(conn, order_id)
            if not existing_order:
                return redirect(url_for("records.records"))

            customer_id = db.resolve_or_create_customer(conn, requested_by)
            db.update_order(conn, order_id, order_number=order_number,
                            order_date=order_date, requested_by=requested_by,
                            customer_id=customer_id, **order_level_fields)

            submitted_ids = {pid for pid, _row in edits if pid is not None}
            existing_ids = {p["id"] for p in existing_plates}
            for removed_id in existing_ids - submitted_ids:
                db.delete_plate(conn, removed_id)

            # Snapshot once for the whole save -- only actually used as a
            # fallback for a brand-new row left without an explicit price
            # (mirrors app.core.ingestion._snapshot_unit_price; an edited
            # EXISTING plate never consults this at all, see update_plate_full).
            prices = db.current_prices(conn)

            for plate_id, row in edits:
                normalized = normalize_row(row)
                if plate_id is not None:
                    db.update_plate_full(
                        conn, plate_id, plate_type=normalized.plate_type,
                        plate_number=normalized.plate_number, plate_to=normalized.plate_to,
                        city=normalized.city, quantity=normalized.quantity,
                        is_voided=normalized.is_voided, is_plate_annulled=normalized.is_plate_annulled,
                        is_batch_replacement=normalized.is_batch_replacement,
                        reason=normalized.reason, vehicle_brand=normalized.vehicle_brand,
                    )
                else:
                    if normalized.unit_price is None:
                        normalized = normalized.model_copy(
                            update={"unit_price": prices.get(normalized.plate_type)}
                        )
                    # Route this through ingestion.py's own duplicate-detection helper
                    # instead of querying db.existing_plate_numbers directly -- no SQL
                    # query or business rule should live outside app/core/ (docs/CORE.md),
                    # and duplicating the is_real_plate/is_voided filter inline here risked
                    # drifting from process_rows'/replace_order's actual definition of
                    # "possible duplicate".
                    is_dup = normalized.plate_number in find_duplicate_plate_numbers(conn, [row])
                    db.insert_plate(conn, order_id=order_id, row=normalized, is_possible_duplicate=is_dup)

            db.recalc_inventory(conn)
            conn.commit()
    except Exception as e:
        return _record_detail_error(order_id, f"Error: {e}")
    current_app.logger.info(
        "record saved: user=%s order_id=%d order_number=%s",
        session.get("user"), order_id, order_number,
    )
    return redirect(url_for("records.record_detail", order_id=order_id))


@bp.post("/records/search-pdf")
@consulta_allowed
def records_search_pdf():
    """Upload a pedido PDF to check its plates against the DB. Consult only:
    this NEVER inserts/updates/deletes anything, unlike /ingest/pdf which leads
    toward saving. Works even if the PDF's header (order number/date/client) is
    unreadable -- unlike /ingest/pdf's flow, only the plate lines matter here."""
    upload = request.files.get("pdf")
    if upload is None or not upload.filename:
        return render_template("records.html", rows=None, query=None,
                               pdf_error="Elegí un archivo PDF para buscar."), 400

    try:
        pedido = parse_pedido_pdf(upload.read())
    except PdfExtractError as e:
        return render_template("records.html", rows=None, query=None, pdf_error=str(e)), 400
    except Exception:
        current_app.logger.exception("Unexpected failure parsing uploaded pedido PDF for search")
        return render_template("records.html", rows=None, query=None,
                               pdf_error="No se pudo procesar el PDF."), 400

    candidates: list[str] = []
    seen: set[str] = set()
    for line in pedido.lines:
        for raw in (line.plate_number, line.plate_to):
            if raw and is_real_plate(raw):
                cleaned = clean_plate(raw)
                if cleaned not in seen:
                    seen.add(cleaned)
                    candidates.append(cleaned)

    with db.get_conn() as conn:
        pdf_results = [
            {"plate_number": pn, "matches": db.find_orders_for_plate(conn, pn)}
            for pn in candidates
        ]
    return render_template("records.html", rows=None, query=None,
                           pdf_results=pdf_results, pdf_filename=upload.filename)


@bp.post("/records/delete")
@login_required
def records_delete():
    with db.get_conn() as conn:
        db.delete_order(conn, int(request.form["order_id"]))
        db.recalc_inventory(conn)  # removing plates changes consumed/waste
        conn.commit()
    return redirect(url_for("records.records"))
