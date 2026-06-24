"""Flask app — pluggable ingestion for plate production.

Routes:
  GET  /            -> redirect to /ingest
  GET  /ingest      -> form (door 1 paste-JSON always; door 2 image only if API key)
  POST /ingest      -> run the chosen door, then the shared backend process_rows
  GET  /review      -> table of rows flagged for review (possible dup or big quantity)
  GET  /healthz     -> liveness probe
"""

from __future__ import annotations

import json
import os

from flask import Flask, redirect, render_template, request, url_for
from pydantic import ValidationError

from app.core import db
from app.core.models import Extraction
from app.core.process import process_rows
from app.web import extraction

app = Flask(__name__)


def _parse_pasted_json(text: str) -> Extraction:
    """Accept either a bare JSON array of rows or an object with a 'rows' key."""
    data = json.loads(text)
    if isinstance(data, list):
        data = {"rows": data}
    return Extraction.model_validate(data)


@app.get("/")
def index():
    return redirect(url_for("ingest"))


@app.get("/ingest")
def ingest():
    return render_template("ingest.html", api_enabled=extraction.api_enabled())


@app.post("/ingest")
def ingest_post():
    summary = None
    error = None
    api_on = extraction.api_enabled()

    try:
        upload = request.files.get("photo")
        pasted = (request.form.get("rows_json") or "").strip()

        if upload and upload.filename:
            # Door 2: image -> API -> rows (only when the API key is configured)
            if api_on:
                result = extraction.extract_from_image(upload.read(), upload.filename)
                summary = process_rows(result.rows)
            else:
                error = ("La opción de subir foto (Puerta 2) requiere configurar "
                         "ANTHROPIC_API_KEY. Por ahora pegá el JSON (Puerta 1).")
        elif pasted:
            # Door 1: pasted JSON -> rows
            result = _parse_pasted_json(pasted)
            summary = process_rows(result.rows)
        else:
            error = "Pegá el JSON, o subí una foto (si la Puerta 2 está activa)."
    except json.JSONDecodeError as e:
        error = f"El JSON pegado no es válido: {e}"
    except ValidationError as e:
        error = f"El JSON no coincide con el formato esperado: {e}"
    except Exception as e:  # surface DB/API errors to the user without crashing
        error = f"Error procesando: {e}"

    return render_template(
        "ingest.html",
        api_enabled=api_on,
        summary=summary.model_dump() if summary else None,
        error=error,
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


DEFAULT_CATEGORIES = ["MOTO PARTICULAR", "CARRO PARTICULAR", "CARRO PÚBLICO", "MOTO PÚBLICO"]


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


@app.post("/inventory/initial")
def inventory_initial():
    with db.get_conn() as conn:
        db.seed_initial_stock(
            conn, {request.form["category"].strip(): int(request.form.get("initial_stock") or 0)}
        )
        conn.commit()
    return redirect(url_for("inventory"))


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
