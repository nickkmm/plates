"""/review/* -- the queue of plates flagged is_possible_duplicate for a human
to resolve by hand (edit, mark not-a-duplicate, or delete)."""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, url_for

from app.auth.decorators import permission_required
from app.core import db
from app.inventory import repository as inventory_repository

bp = Blueprint("review", __name__)


@bp.get("/review")
@permission_required("duplicates.review")
def review():
    with db.get_conn() as conn:
        rows = db.review_rows(conn)
    return render_template("review.html", rows=rows)


@bp.post("/review/update")
@permission_required("duplicates.review")
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
        inventory_repository.recalc_inventory(conn)  # quantity/void changes affect consumed
        conn.commit()
    return redirect(url_for("review.review"))


@bp.post("/review/resolve")
@permission_required("duplicates.review")
def review_resolve():
    with db.get_conn() as conn:
        db.resolve_plate(conn, int(request.form["plate_id"]))
        conn.commit()
    return redirect(url_for("review.review"))


@bp.post("/review/delete")
@permission_required("duplicates.review")
def review_delete():
    with db.get_conn() as conn:
        db.delete_plate(conn, int(request.form["plate_id"]))
        inventory_repository.recalc_inventory(conn)
        conn.commit()
    return redirect(url_for("review.review"))
