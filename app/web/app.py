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
session (see app.web.auth.login_required).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, redirect, render_template, request, session, url_for

from app.core import db
from app.core.models import ExtractedRow
from app.core.normalize import INVENTORY_CATEGORIES, PLATE_TYPES, clean_plate, is_real_plate, validate_plate
from app.core.pdf_extract import ParsedPedido, PdfExtractError, parse_pedido_pdf
from app.core.process import find_duplicate_plate_numbers, process_rows
from app.web import auth
from app.web.auth import login_required

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

# Plate TYPES are what the user picks per plate (granular, 5). Inventory
# CATEGORIES are the 3 materials those types discount from (público merged).
FORM_PLATE_TYPES = list(PLATE_TYPES)
INVENTORY_CATS = list(INVENTORY_CATEGORIES)

# The container runs in UTC; compute "today" in Colombia so the date default
# and the future-date validation use local time, not UTC.
BOGOTA = ZoneInfo("America/Bogota")


def _today_iso() -> str:
    return datetime.now(BOGOTA).date().isoformat()


def _options(conn):
    return db.distinct_requested_by(conn), db.distinct_cities(conn)


def _padded(values: list[str], size: int) -> list[str]:
    """Pad a per-line field list to `size` with blanks.

    The line zips are strict=False, so a request that omits an optional per-line
    field (city) would otherwise truncate the zip and silently drop plate lines.
    """
    return values + [""] * (size - len(values))


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

    # Order-level validations.
    if any(ch.isdigit() for ch in requested_by):
        raise ValueError("'Solicitado por' no debería contener números (parece una placa). Revisá ese campo.")
    if order_date and order_date > _today_iso():
        raise ValueError(f"La fecha {order_date} es futura. Corregí la fecha.")

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
    elif pedido.order_date.isoformat() > _today_iso():
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
        return redirect(url_for("ingest"))
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
        session.permanent = True
        return redirect(next_url if auth.is_safe_next(next_url) else url_for("ingest"))
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
@login_required
def index():
    return redirect(url_for("ingest"))


@app.get("/ingest")
@login_required
def ingest():
    with db.get_conn() as conn:
        requesters, cities = _options(conn)
    return render_template("ingest.html", requesters=requesters, cities=cities,
                           categories=FORM_PLATE_TYPES, today=_today_iso())


@app.post("/ingest")
@login_required
def ingest_post():
    summary = None
    error = None
    duplicates = None
    header = None
    lines = None
    try:
        rows = _rows_from_form(request.form)
        if not rows:
            error = "Agregá al menos una placa."
        else:
            dup_plate_numbers = find_duplicate_plate_numbers(rows)
            if dup_plate_numbers and not request.form.get("confirm_duplicate"):
                with db.get_conn() as conn:
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
                summary = process_rows(rows)
    except Exception as e:  # surface validation/DB errors without crashing
        error = f"Error: {e}"

    with db.get_conn() as conn:
        requesters, cities = _options(conn)
    return render_template(
        "ingest.html", requesters=requesters, cities=cities,
        categories=FORM_PLATE_TYPES, today=_today_iso(),
        summary=summary.model_dump() if summary else None, error=error,
        duplicates=duplicates, header=header, lines=lines,
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
        requesters, cities = _options(conn)
    return render_template(
        "ingest.html", requesters=requesters, cities=cities,
        categories=FORM_PLATE_TYPES, today=_today_iso(),
        header=header, lines=lines, notices=notices,
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
@login_required
def records():
    query = (request.args.get("q") or "").strip()
    with db.get_conn() as conn:
        if query:
            results = db.search_plates_by_number(conn, query)
            rows = None
        else:
            results = None
            rows = db.recent_orders(conn)
    return render_template("records.html", rows=rows, query=query, results=results, pdf_results=None)


@app.get("/records/<int:order_id>")
@login_required
def record_detail(order_id):
    with db.get_conn() as conn:
        order, plates = db.order_detail(conn, order_id)
    if not order:
        return redirect(url_for("records"))
    return render_template("record_detail.html", order=order, plates=plates)


@app.post("/records/search-pdf")
@login_required
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
