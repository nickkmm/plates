"""/precios -- admin-only editor for plate_type_prices (Fase 5a).

Pure web layer: every business rule for this table (which plate_type values
are valid, the "sin tarifa" default, the ON CONFLICT upsert) already lives in
app/core/db/pricing.py (Fase 2) -- this module only parses/validates the
POSTed price and calls db.price_list/db.set_price, never touching SQL or
normalize.PLATE_TYPES directly.

@login_required (admin-only), NOT @pos_allowed -- the plan is explicit that
'punto_venta' edits orders/payments, not tariffs, so it must 403 here same as
'consulta'/'produccion'.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, redirect, render_template, request, session, url_for

from app.core import db
from app.web.auth import login_required

bp = Blueprint("pricing", __name__)


def _parse_unit_price(raw: str | None) -> Decimal:
    """Parse a POSTed price as a positive Decimal.

    Never mixes Decimal with float -- Decimal(str) is exact, unlike
    Decimal(float(raw)) which would introduce binary-float rounding error
    before the value ever reaches the DB. Raises ValueError (Spanish message)
    on blank/non-numeric/zero/negative input; the route catches this and
    re-renders the form instead of a 500 or silently accepting garbage.
    """
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("El precio no puede estar vacío.")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValueError(f"'{raw}' no es un precio válido.") from None
    if value <= 0:
        raise ValueError("El precio tiene que ser mayor que cero.")
    return value


@bp.get("/precios")
@login_required
def precios():
    with db.get_conn() as conn:
        rows = db.price_list(conn)
    return render_template("pricing.html", rows=rows)


@bp.post("/precios/<plate_type>")
@login_required
def precios_update(plate_type: str):
    try:
        unit_price = _parse_unit_price(request.form.get("unit_price"))
        with db.get_conn() as conn:
            # db.set_price re-validates plate_type against normalize.PLATE_TYPES
            # itself and raises the same kind of Spanish ValueError -- caught
            # below, so a tampered/unknown value in the URL re-renders the form
            # instead of a raw traceback.
            db.set_price(conn, plate_type, unit_price, updated_by=session["user"])
            conn.commit()
    except ValueError as e:
        with db.get_conn() as conn:
            rows = db.price_list(conn)
        return render_template("pricing.html", rows=rows, error=str(e)), 400
    return redirect(url_for("pricing.precios"))
