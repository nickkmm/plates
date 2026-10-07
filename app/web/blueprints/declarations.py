"""/declaraciones -- internal review screen for sworn declarations.

The public form (/declaracion) stores a declaration; when it hits a conflict
it is held as `pending_review` with flags. This screen is where staff read
what was declared, see the real context behind each flag (previous owner,
last fabrication date, ...) and, for admins, approve or reject it.

Access: listing, detail and file download are @pos_allowed (admin +
point_of_sale -- the counter needs them); approve/reject are @login_required
(admin only). The read-only 'production' role gets 403 on everything here: it
handles identity documents and signatures. The flags context (previous owner,
fabrication dates) is shown ONLY here, never to the anonymous submitter.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import psycopg
from dateutil.relativedelta import relativedelta
from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from psycopg.rows import DictRow

from app.core import db
from app.core import declarations as decl
from app.core.db.declarations import (
    FLAG_CARD_DATA_MISMATCH,
    FLAG_CARD_OTHER_OWNER,
    FLAG_CUSTOMER_DOCUMENT_MISMATCH,
    FLAG_CUSTOMER_TYPE_MISMATCH,
    FLAG_PLATE_MADE_RECENTLY,
    STATUS_APPROVED,
    STATUS_PENDING_REVIEW,
    STATUS_REJECTED,
)
from app.core.normalize import clean_plate
from app.web.auth import login_required, pos_allowed
from app.web.uploads import UploadError, resolve_upload_path

bp = Blueprint("declarations", __name__)

STATUS_LABELS = {
    STATUS_PENDING_REVIEW: "Pendiente",
    STATUS_APPROVED: "Aprobada",
    STATUS_REJECTED: "Rechazada",
}
# Filter tabs: (query value, label). "all" means no status filter.
FILTERS = (
    (STATUS_PENDING_REVIEW, "Pendientes"),
    (STATUS_APPROVED, "Aprobadas"),
    (STATUS_REJECTED, "Rechazadas"),
    ("all", "Todas"),
)
FLAG_LABELS = {
    FLAG_PLATE_MADE_RECENTLY: "Placa reciente",
    FLAG_CARD_OTHER_OWNER: "Tarjeta de otro dueño",
    FLAG_CARD_DATA_MISMATCH: "Datos no cuadran",
    FLAG_CUSTOMER_TYPE_MISMATCH: "Tipo de cliente",
    FLAG_CUSTOMER_DOCUMENT_MISMATCH: "Documento distinto",
}
# kind -> (DeclarationRecord attribute holding the stored name, mimetype)
FILE_KINDS = {
    "owner_signature": ("owner_signature_path", "image/png"),
    "gestor_signature": ("gestor_signature_path", "image/png"),
    "notarized_pdf": ("notarized_pdf_path", "application/pdf"),
}


def _fmt_date(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def _card_line(conn: psycopg.Connection[DictRow], card: db.OwnershipCardRecord) -> str:
    owner = db.get_customer(conn, card.customer_id)
    owner_name = owner.name if owner else f"cliente #{card.customer_id}"
    return (
        f"licencia {card.transit_license}, placa {card.plate_number}, "
        f"a nombre de {owner_name}."
    )


def _flag_details(
    conn: psycopg.Connection[DictRow], declaration: db.DeclarationRecord,
) -> list[dict[str, Any]]:
    """One explanation per flag with the real context staff need."""
    data = declaration.submitted_data
    owner = data.get("owner") or {}
    gestor: dict[str, Any] = data.get("gestor") or {}
    vehicle = data.get("vehicle") or {}
    plate = clean_plate(str(vehicle.get("plate_number") or ""))
    license_ = str(vehicle.get("transit_license") or "")
    details: list[dict[str, Any]] = []

    for flag in declaration.review_flags:
        lines: list[str] = []
        if flag == FLAG_PLATE_MADE_RECENTLY:
            title = "La placa se fabricó hace menos de 6 meses"
            last = db.last_fabrication_date(conn, plate)
            if last is not None:
                again = last + relativedelta(months=decl.MIN_MONTHS_BETWEEN_PLATES)
                lines.append(f"Placa {plate}: última fabricación el {_fmt_date(last)}.")
                lines.append(f"Podría fabricarse de nuevo a partir del {_fmt_date(again)}.")
            else:
                lines.append(f"Placa {plate}: ya no hay una fabricación vigente registrada.")
            for order in db.find_orders_for_plate(conn, plate):
                lines.append(
                    f"Orden {order['order_number']} del {_fmt_date(order['order_date'])} "
                    f"({order['requested_by']})."
                )
        elif flag in (FLAG_CARD_OTHER_OWNER, FLAG_CARD_DATA_MISMATCH):
            if flag == FLAG_CARD_OTHER_OWNER:
                title = "La tarjeta de propiedad ya existe a nombre de otro cliente"
            else:
                title = "La licencia de tránsito y la placa no cuadran entre sí"
            by_license = db.get_ownership_card_by_transit_license(conn, license_) if license_ else None
            by_plate = db.get_ownership_card_by_plate_number(conn, plate) if plate else None
            if by_license is not None:
                lines.append(f"Tarjeta existente por licencia: {_card_line(conn, by_license)}")
            else:
                lines.append(f"No existe una tarjeta con la licencia {license_}.")
            if by_plate is not None:
                lines.append(f"Tarjeta existente por placa: {_card_line(conn, by_plate)}")
            else:
                lines.append(f"No existe una tarjeta con la placa {plate}.")
            lines.append(f"Declarado: licencia {license_}, placa {plate}.")
        elif flag == FLAG_CUSTOMER_TYPE_MISMATCH:
            title = "Un cliente existente tiene un tipo distinto al esperado"
            for person, expected, label in ((owner, "CLIENTE", "Propietario/poseedor"),
                                            (gestor, "GESTOR", "Gestor")):
                existing = db.get_customer_by_name(conn, str(person.get("name") or "")) if person else None
                if existing is not None and existing.type != expected:
                    lines.append(
                        f"{label} '{existing.name}' ya existe con tipo "
                        f"{existing.type or 'sin definir'}; se esperaba {expected}."
                    )
        elif flag == FLAG_CUSTOMER_DOCUMENT_MISMATCH:
            title = "Un cliente existente con el mismo nombre tiene otro documento"
            for person, label in ((owner, "Propietario/poseedor"), (gestor, "Gestor")):
                existing = db.get_customer_by_name(conn, str(person.get("name") or "")) if person else None
                if existing is not None and existing.document_number:
                    lines.append(
                        f"{label} '{existing.name}': documento guardado {existing.document_number}, "
                        f"documento declarado {person.get('document_number') or '—'}."
                    )
        else:
            title = flag
        details.append({"flag": flag, "title": title, "lines": lines})
    return details


def _render_detail(declaration_id: int, *, error: str | None = None):
    with db.get_conn() as conn:
        declaration = db.get_declaration(conn, declaration_id)
        if declaration is None:
            return redirect(url_for("declarations.declarations"))
        customer = db.get_customer(conn, declaration.customer_id) if declaration.customer_id else None
        gestor = db.get_customer(conn, declaration.gestor_id) if declaration.gestor_id else None
        card = (
            db.get_ownership_card(conn, declaration.ownership_card_id)
            if declaration.ownership_card_id else None
        )
        flag_details = (
            _flag_details(conn, declaration) if declaration.status == STATUS_PENDING_REVIEW else []
        )
    page = render_template(
        "declaration_detail.html", d=declaration, customer=customer, gestor=gestor, card=card,
        flag_details=flag_details, flag_labels=FLAG_LABELS, status_labels=STATUS_LABELS,
        error=error, pending_status=STATUS_PENDING_REVIEW,
    )
    return (page, 400) if error else page


@bp.get("/declaraciones")
@pos_allowed
def declarations():
    status_filter = request.args.get("status") or STATUS_PENDING_REVIEW
    if status_filter not in {value for value, _ in FILTERS}:
        status_filter = STATUS_PENDING_REVIEW
    query = (request.args.get("q") or "").strip()
    with db.get_conn() as conn:
        rows = db.list_declarations(
            conn, status=None if status_filter == "all" else status_filter, query=query or None,
        )
        pending_count = len(db.list_declarations(conn, status=STATUS_PENDING_REVIEW))
    return render_template(
        "declarations.html", rows=rows, query=query, status_filter=status_filter,
        filters=FILTERS, pending_count=pending_count, flag_labels=FLAG_LABELS,
        status_labels=STATUS_LABELS,
    )


@bp.get("/declaraciones/<int:declaration_id>")
@pos_allowed
def declaration_detail(declaration_id: int):
    return _render_detail(declaration_id)


@bp.get("/declaraciones/<int:declaration_id>/file/<kind>")
@pos_allowed
def declaration_file(declaration_id: int, kind: str):
    spec = FILE_KINDS.get(kind)
    if spec is None:
        abort(404)
    attribute, mimetype = spec
    with db.get_conn() as conn:
        declaration = db.get_declaration(conn, declaration_id)
    if declaration is None:
        abort(404)
    stored_name = getattr(declaration, attribute)
    if not stored_name:
        abort(404)
    try:
        path = resolve_upload_path(stored_name)
    except UploadError:
        abort(404)
    if not path.is_file():
        abort(404)
    response = send_file(path, mimetype=mimetype, as_attachment=False)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


def _decide(declaration_id: int, decision: str):
    """Shared approve/reject flow: unknown id -> list; ValueError -> detail
    with the message (400, nothing changed); success -> commit + redirect."""
    user_id = session["user_id"]
    with db.get_conn() as conn:
        if db.get_declaration(conn, declaration_id) is None:
            return redirect(url_for("declarations.declarations"))
    note = (request.form.get("note") or "").strip()
    try:
        with db.get_conn() as conn:
            if decision == "approve":
                decl.approve_declaration(
                    conn, declaration_id, user_id,
                    reassign_card=request.form.get("reassign_card") is not None,
                    note=note or None,
                )
            else:
                decl.reject_declaration(conn, declaration_id, user_id, note)
            conn.commit()
    except ValueError as exc:
        return _render_detail(declaration_id, error=str(exc))
    current_app.logger.info(
        "declaration review: user=%s declaration_id=%s decision=%s",
        session.get("user"), declaration_id, decision,
    )
    return redirect(url_for("declarations.declaration_detail", declaration_id=declaration_id))


@bp.post("/declaraciones/<int:declaration_id>/approve")
@login_required
def declaration_approve(declaration_id: int):
    return _decide(declaration_id, "approve")


@bp.post("/declaraciones/<int:declaration_id>/reject")
@login_required
def declaration_reject(declaration_id: int):
    return _decide(declaration_id, "reject")
