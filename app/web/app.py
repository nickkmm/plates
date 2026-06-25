"""Flask app — plate production: form-based registration, review, inventory.

Routes:
  GET  /            -> redirect to /ingest
  GET  /ingest      -> the "Registrar" form (order header + plate lines)
  POST /ingest      -> build rows from the form, then the shared backend process_rows
  GET  /review      -> table of rows flagged for review (real duplicate plates)
  GET  /inventory   -> per-category inventory cards
  GET  /healthz     -> liveness probe
"""

from __future__ import annotations

from datetime import date

from flask import Flask, redirect, render_template, request, url_for

from app.core import db
from app.core.models import ExtractedRow
from app.core.normalize import validate_plate
from app.core.process import process_rows

app = Flask(__name__)

DEFAULT_CATEGORIES = ["MOTO PARTICULAR", "CARRO PARTICULAR", "CARRO PÚBLICO", "MOTO PÚBLICO"]


def _options(conn):
    return db.distinct_requested_by(conn), db.distinct_cities(conn)


def _rows_from_form(form) -> list[ExtractedRow]:
    """Build ExtractedRow(s) from the order header + repeated plate lines.

    line_type drives the flags so the user never types special words:
      placa          -> single plate
      rango          -> consecutive plates: requires 'desde' (plate_number) and 'hasta' (plate_to)
      reposicion     -> big non-consecutive lot (no plate, no range)
      anulada_orden  -> voided order (NOT counted in inventory)
      muestra        -> sample (plate_number = MUESTRA)
    """
    order_number = form["order_number"].strip()
    order_date = form["order_date"]
    requested_by = form["requested_by"].strip()
    city = (form.get("city") or "").strip() or None

    # Order-level validations.
    if any(ch.isdigit() for ch in requested_by):
        raise ValueError("'Solicitado por' no debería contener números (parece una placa). Revisá ese campo.")
    if order_date and order_date > date.today().isoformat():
        raise ValueError(f"La fecha {order_date} es futura. Corregí la fecha.")

    rows: list[ExtractedRow] = []
    for lt, pt, pn, pto, qty in zip(
        form.getlist("line_type"), form.getlist("plate_type"),
        form.getlist("plate_number"), form.getlist("plate_to"), form.getlist("quantity"),
    ):
        if not (pt or "").strip():
            continue  # skip empty line
        plate_number = (pn or "").strip() or None
        plate_to = (pto or "").strip() or None
        is_batch = is_voided = False
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
        else:  # placa (single)
            plate_to = None
            if plate_number:
                plate_number = validate_plate(plate_number, ptype)
        rows.append(ExtractedRow(
            order_number=order_number, order_date=order_date, requested_by=requested_by,
            city=city, plate_type=pt.strip(), plate_number=plate_number, plate_to=plate_to,
            quantity=int(qty or 1), is_batch_replacement=is_batch, is_voided=is_voided,
        ))
    return rows


@app.get("/")
def index():
    return redirect(url_for("ingest"))


@app.get("/ingest")
def ingest():
    with db.get_conn() as conn:
        requesters, cities = _options(conn)
    return render_template("ingest.html", requesters=requesters, cities=cities,
                           categories=DEFAULT_CATEGORIES, today=date.today().isoformat())


@app.post("/ingest")
def ingest_post():
    summary = None
    error = None
    try:
        rows = _rows_from_form(request.form)
        if not rows:
            error = "Agregá al menos una placa."
        else:
            summary = process_rows(rows)
    except Exception as e:  # surface validation/DB errors without crashing
        error = f"Error: {e}"

    with db.get_conn() as conn:
        requesters, cities = _options(conn)
    return render_template(
        "ingest.html", requesters=requesters, cities=cities,
        categories=DEFAULT_CATEGORIES, today=date.today().isoformat(),
        summary=summary.model_dump() if summary else None, error=error,
    )


@app.get("/review")
def review():
    with db.get_conn() as conn:
        rows = db.review_rows(conn)
    return render_template("review.html", rows=rows)


@app.post("/review/update")
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
def review_resolve():
    with db.get_conn() as conn:
        db.resolve_plate(conn, int(request.form["plate_id"]))
        conn.commit()
    return redirect(url_for("review"))


@app.post("/review/delete")
def review_delete():
    with db.get_conn() as conn:
        db.delete_plate(conn, int(request.form["plate_id"]))
        db.recalc_inventory(conn)
        conn.commit()
    return redirect(url_for("review"))


@app.get("/inventory")
def inventory():
    with db.get_conn() as conn:
        rows = db.inventory_view(conn)
    categories = sorted({*DEFAULT_CATEGORIES, *(r["category"] for r in rows)})
    return render_template("inventory.html", rows=rows, categories=categories)


@app.post("/inventory/alistado")
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


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
