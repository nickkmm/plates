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

# Quantity above which a plate row is surfaced for review (computed dynamically;
# no schema change). Tune via REVIEW_QTY_THRESHOLD.
REVIEW_QTY_THRESHOLD = int(os.environ.get("REVIEW_QTY_THRESHOLD", "500"))


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

        if api_on and upload and upload.filename:
            # Door 2: image -> API -> rows
            result = extraction.extract_from_image(upload.read(), upload.filename)
            summary = process_rows(result.rows)
        elif pasted:
            # Door 1: pasted JSON -> rows
            result = _parse_pasted_json(pasted)
            summary = process_rows(result.rows)
        else:
            error = "Pegá el JSON, o subí una foto si la Puerta 2 está activa."
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
        rows = db.review_rows(conn, REVIEW_QTY_THRESHOLD)
    return render_template("review.html", rows=rows, threshold=REVIEW_QTY_THRESHOLD)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
