"""Flask app — plate production: form-based registration, review, inventory.

Routes:
  GET  /            -> redirect to /ingest
  GET  /ingest      -> the "Registrar" form (order header + plate lines)
  POST /ingest      -> build rows from the form, then the shared backend process_rows
  GET  /ingest/pdf  -> upload form for the client-facing pedido PDF
  POST /ingest/pdf  -> parse the PDF and RE-RENDER /ingest pre-filled for review
                       (saves nothing; the person still presses "Guardar orden")
  GET  /review      -> table of rows flagged for review (real duplicate plates)
  GET  /inventory   -> per-category inventory cards
  GET/POST /login   -> session login page (open)
  POST /logout      -> clear the session (open)
  GET  /healthz     -> liveness probe (open)

All routes except /login, /logout and /healthz require an authenticated
session. Most also require a full-access role (admin/producción); see
app.web.auth.login_required. A small allowlist of read-only lookup routes
(/records, /records/<id>) instead accepts any authenticated role, including
the read-only 'consulta' role -- see app.web.auth.consulta_allowed.
"""

from __future__ import annotations

import os
from datetime import timedelta

from flask import Flask, redirect, render_template, request, session, url_for

from app.core import db
from app.core.ingestion import find_duplicate_plate_numbers, process_rows, replace_order
from app.core.models import ExtractedRow
from app.core.normalize import clean_plate, is_real_plate, normalize_row, validate_plate
from app.core.pdf_extract import ParsedPedido, PdfExtractError, parse_pedido_pdf
from app.web import auth
from app.web.auth import consulta_allowed, login_required
from app.web.helpers import (
    FORM_PLATE_TYPES,
    INVENTORY_CATS,
    _options,
    _padded,
    _validate_order_header,
    today_iso,
)

app = Flask(__name__)

# Session signing key — required so cookies can't be forged. Generate one with:
#   python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY env var is required. Generate one with: "
        'python -c "import secrets; print(secrets.token_hex(32))"'
    )
app.secret_key = SECRET_KEY

# Mark the session cookie Secure only behind HTTPS (set COOKIE_SECURE=1 in
# production). Off by default so local plain-HTTP dev still works.
_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=_COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)

@app.context_processor
def _inject_full_access_roles() -> dict:
    """Expose auth.FULL_ACCESS_ROLES to templates, so they gate edit/delete UI
    on role membership (not a hardcoded '!= consulta') -- keeps templates in
    sync automatically if a role's access level ever changes again."""
    return {"full_access_roles": auth.FULL_ACCESS_ROLES}


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


def _edit_rows_from_form(form, *, order_number: str, order_date: str,
                         requested_by: str, order_voided: bool) -> list[tuple[int | None, ExtractedRow]]:
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
    """
    plate_types = form.getlist("plate_type")
    n = len(plate_types)
    rows: list[tuple[int | None, ExtractedRow]] = []
    for pid, pt, pn, pto, ct, qty, annulled in zip(
        _padded(form.getlist("plate_id"), n), plate_types,
        form.getlist("plate_number"), form.getlist("plate_to"),
        _padded(form.getlist("city"), n), form.getlist("quantity"),
        form.getlist("is_plate_annulled"), strict=False,
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
        )
        rows.append((int(pid) if pid else None, row))
    return rows


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


@app.get("/login")
def login():
    if session.get("user"):
        return redirect(auth.landing_url())
    next_url = request.args.get("next", "")
    return render_template("login.html", next=next_url if auth.is_safe_next(next_url) else "")


@app.post("/login")
def login_post():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    next_url = request.form.get("next") or ""
    if auth.verify_credentials(username, password):
        session.clear()
        session["user"] = username
        session["role"] = auth.role_for(username)
        session.permanent = True
        return redirect(next_url if auth.is_safe_next(next_url) else auth.landing_url())
    return (
        render_template(
            "login.html",
            error="Usuario o contraseña incorrectos.",
            next=next_url if auth.is_safe_next(next_url) else "",
        ),
        401,
    )


@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@consulta_allowed
def index():
    return redirect(auth.landing_url())


@app.get("/ingest")
@login_required
def ingest():
    with db.get_conn() as conn:
        requesters, cities = _options(conn)
    return render_template("ingest.html", requesters=requesters, cities=cities,
                           categories=FORM_PLATE_TYPES, today=today_iso())


@app.post("/ingest")
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


@app.route("/ingest/pdf", methods=["GET", "POST"])
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
        app.logger.exception("Unexpected failure parsing uploaded pedido PDF")
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


@app.get("/review")
@login_required
def review():
    with db.get_conn() as conn:
        rows = db.review_rows(conn)
    return render_template("review.html", rows=rows)


@app.post("/review/update")
@login_required
def review_update():
    plate_id = int(request.form["plate_id"])
    plate_number = (request.form.get("plate_number") or "").strip() or None
    with db.get_conn() as conn:
        db.update_plate(
            conn,
            plate_id,
            plate_type=(request.form.get("plate_type") or "").strip(),
            plate_number=plate_number,
            quantity=int(request.form.get("quantity") or 0),
            is_voided=request.form.get("is_voided") == "on",
        )
        db.recalc_inventory(conn)  # quantity/void changes affect consumed
        conn.commit()
    return redirect(url_for("review"))


@app.post("/review/resolve")
@login_required
def review_resolve():
    with db.get_conn() as conn:
        db.resolve_plate(conn, int(request.form["plate_id"]))
        conn.commit()
    return redirect(url_for("review"))


@app.post("/review/delete")
@login_required
def review_delete():
    with db.get_conn() as conn:
        db.delete_plate(conn, int(request.form["plate_id"]))
        db.recalc_inventory(conn)
        conn.commit()
    return redirect(url_for("review"))


@app.get("/inventory")
@login_required
def inventory():
    with db.get_conn() as conn:
        rows = db.inventory_view(conn)
        ornamental = db.ornamental_count(conn)
    categories = sorted({*INVENTORY_CATS, *(r["category"] for r in rows)})
    return render_template("inventory.html", rows=rows, categories=categories,
                           ornamental=ornamental)


@app.post("/inventory/alistado")
@login_required
def inventory_alistado():
    with db.get_conn() as conn:
        db.insert_blank_preparation(
            conn,
            prep_date=request.form.get("prep_date") or None,
            category=request.form["category"].strip(),
            quantity=int(request.form.get("quantity") or 0),
            prepared_by=(request.form.get("prepared_by") or "").strip() or None,
        )
        db.recalc_inventory(conn)
        conn.commit()
    return redirect(url_for("inventory"))


@app.get("/records")
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


@app.get("/records/<int:order_id>")
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
    if not order:
        return redirect(url_for("records"))
    return render_template("record_detail.html", order=order, plates=plates,
                           cities=cities, requesters=requesters, categories=FORM_PLATE_TYPES,
                           today=today_iso(), order_voided=_order_voided(plates),
                           back_query=back_query)


def _record_detail_error(order_id: int, error: str):
    """Re-render the Registro detail page with a validation error, instead of
    losing the person's edits on a bad plate number. Shared by record_save."""
    with db.get_conn() as conn:
        order, plates = db.order_detail(conn, order_id)
        requesters, cities = _options(conn)
    return render_template("record_detail.html", order=order, plates=plates,
                           cities=cities, requesters=requesters, categories=FORM_PLATE_TYPES,
                           today=today_iso(), order_voided=_order_voided(plates), error=error), 400


@app.post("/records/<int:order_id>/save")
@login_required
def record_save(order_id):
    """Save every change made on the Registro detail page in one shot: the
    order header (date/number/solicitado por/anulada), edits to existing
    plates, any new plate rows added, and the removal of any row the person
    deleted client-side -- one form, one button, same shape as /ingest's
    "Guardar orden" rather than a separate save per row."""
    order_number = (request.form.get("order_number") or "").strip()
    order_date = request.form.get("order_date") or ""
    requested_by = (request.form.get("requested_by") or "").strip()
    order_voided = request.form.get("order_voided") == "on"

    try:
        _validate_order_header(requested_by=requested_by, order_date=order_date)
        edits = _edit_rows_from_form(request.form, order_number=order_number,
                                     order_date=order_date, requested_by=requested_by,
                                     order_voided=order_voided)
        with db.get_conn() as conn:
            existing_order, existing_plates = db.order_detail(conn, order_id)
            if not existing_order:
                return redirect(url_for("records"))

            db.update_order(conn, order_id, order_number=order_number,
                            order_date=order_date, requested_by=requested_by)

            submitted_ids = {pid for pid, _row in edits if pid is not None}
            existing_ids = {p["id"] for p in existing_plates}
            for removed_id in existing_ids - submitted_ids:
                db.delete_plate(conn, removed_id)

            for plate_id, row in edits:
                normalized = normalize_row(row)
                if plate_id is not None:
                    db.update_plate_full(
                        conn, plate_id, plate_type=normalized.plate_type,
                        plate_number=normalized.plate_number, plate_to=normalized.plate_to,
                        city=normalized.city, quantity=normalized.quantity,
                        is_voided=normalized.is_voided, is_plate_annulled=normalized.is_plate_annulled,
                        is_batch_replacement=normalized.is_batch_replacement,
                    )
                else:
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
    return redirect(url_for("record_detail", order_id=order_id))


@app.post("/records/search-pdf")
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
        app.logger.exception("Unexpected failure parsing uploaded pedido PDF for search")
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


@app.post("/records/delete")
@login_required
def records_delete():
    with db.get_conn() as conn:
        db.delete_order(conn, int(request.form["order_id"]))
        db.recalc_inventory(conn)  # removing plates changes consumed/waste
        conn.commit()
    return redirect(url_for("records"))


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
