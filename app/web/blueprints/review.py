"""/review/* -- the queue of plates flagged is_possible_duplicate for a human
to resolve by hand (edit, mark not-a-duplicate, or delete)."""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, url_for

from app.core import db
from app.web.auth import login_required

bp = Blueprint("review", __name__)


@bp.get("/review")
@login_required
def review():
    with db.get_conn() as conn:
        rows = db.review_rows(conn)
    return render_template("review.html", rows=rows)


@bp.post("/review/update")
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
    return redirect(url_for("review.review"))


@bp.post("/review/resolve")
@login_required
def review_resolve():
    with db.get_conn() as conn:
        db.resolve_plate(conn, int(request.form["plate_id"]))
        conn.commit()
    return redirect(url_for("review.review"))


@bp.post("/review/delete")
@login_required
def review_delete():
    with db.get_conn() as conn:
        db.delete_plate(conn, int(request.form["plate_id"]))
        db.recalc_inventory(conn)
        conn.commit()
    return redirect(url_for("review.review"))
