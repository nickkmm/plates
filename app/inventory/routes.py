"""/inventory/* -- per-material inventory cards and the ALISTAMIENTO
(blanks-prepared) intake form."""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, url_for

from app.inventory import repository
from app.shared import db
from app.shared.auth import permission_required
from app.web.helpers import INVENTORY_CATS

bp = Blueprint("inventory", __name__)


@bp.get("/inventory")
@permission_required("inventory.manage")
def inventory():
    with db.get_conn() as conn:
        rows = repository.inventory_view(conn)
        ornamental = repository.ornamental_count(conn)
    categories = sorted({*INVENTORY_CATS, *(r["category"] for r in rows)})
    return render_template("inventory.html", rows=rows, categories=categories,
                           ornamental=ornamental)


@bp.post("/inventory/alistado")
@permission_required("inventory.manage")
def inventory_alistado():
    with db.get_conn() as conn:
        repository.insert_blank_preparation(
            conn,
            prep_date=request.form.get("prep_date") or None,
            category=request.form["category"].strip(),
            quantity=int(request.form.get("quantity") or 0),
            prepared_by=(request.form.get("prepared_by") or "").strip() or None,
        )
        repository.recalc_inventory(conn)
        conn.commit()
    return redirect(url_for("inventory.inventory"))
