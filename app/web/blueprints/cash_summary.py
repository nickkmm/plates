"""/cuadre -- daily cash-reconciliation report (cuadre de caja, Fase 5b).

Pure web layer: every aggregation rule (grouping by order_date then
payment_method, the SIN FORMA DE PAGO bucket, excluding voided/annulled
plates, and the missing-unit_price warning list) lives in
app/core/cash_summary.py -- this module only parses/validates the desde/
hasta query params, calls build_cash_summary/render_cash_summary_pdf, and
renders or streams the result. No SQL here, per docs/CORE.md's rule that no
query/business rule lives outside app/core/.

@pos_allowed (admin + punto_venta) on BOTH routes -- the plan is explicit
both the HTML view and the PDF export need it: PTO.V is exactly who reads
this report daily and prints it for the admin.
"""

from __future__ import annotations

from datetime import date

from flask import Blueprint, Response, render_template, request

from app.core import db
from app.core.cash_summary import build_cash_summary, render_cash_summary_pdf
from app.web.auth import pos_allowed
from app.web.helpers import today_iso

bp = Blueprint("cash_summary", __name__)


def _parse_date_param(raw: str | None, *, label: str) -> date:
    """Parse one `desde`/`hasta` query param, defaulting to today (Bogota
    time, via today_iso()) when blank -- the route is expected to be opened
    with no params at all as its normal daily-use case."""
    raw = (raw or "").strip() or today_iso()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError(
            f"'{raw}' no es una fecha válida para '{label}' (formato AAAA-MM-DD)."
        ) from None


def _date_range_from_request() -> tuple[date, date]:
    desde = _parse_date_param(request.args.get("desde"), label="desde")
    hasta = _parse_date_param(request.args.get("hasta"), label="hasta")
    return desde, hasta


def _sticky_range() -> tuple[str, str]:
    """The desde/hasta strings to redisplay in the form -- whatever was
    actually submitted (even if invalid), so a typo doesn't silently reset
    the person's chosen range back to today."""
    return request.args.get("desde") or today_iso(), request.args.get("hasta") or today_iso()


@bp.get("/cuadre")
@pos_allowed
def cuadre():
    try:
        desde, hasta = _date_range_from_request()
        with db.get_conn() as conn:
            summary = build_cash_summary(conn, desde=desde, hasta=hasta)
    except ValueError as e:
        sticky_desde, sticky_hasta = _sticky_range()
        return (
            render_template(
                "cash_summary.html", summary=None, error=str(e), desde=sticky_desde, hasta=sticky_hasta
            ),
            400,
        )
    return render_template(
        "cash_summary.html", summary=summary, error=None, desde=desde.isoformat(), hasta=hasta.isoformat()
    )


@bp.get("/cuadre/pdf")
@pos_allowed
def cuadre_pdf():
    try:
        desde, hasta = _date_range_from_request()
        with db.get_conn() as conn:
            summary = build_cash_summary(conn, desde=desde, hasta=hasta)
    except ValueError as e:
        sticky_desde, sticky_hasta = _sticky_range()
        return (
            render_template(
                "cash_summary.html", summary=None, error=str(e), desde=sticky_desde, hasta=sticky_hasta
            ),
            400,
        )
    pdf_bytes = render_cash_summary_pdf(summary, desde=desde, hasta=hasta)
    filename = f"cuadre_{desde.isoformat()}_{hasta.isoformat()}.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
