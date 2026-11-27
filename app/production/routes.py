"""/produccion -- the production board: every order the point of sale created,
filtered by its production status, and the order detail where production
moves it one step pending -> in_progress -> ready -> delivered (or undoes the
last step).

The transition rules (and the optimistic-concurrency check on
`expected_status`) live in app/core/production.py; this module only decides
WHO may make each step, from permissions:
- `production.view` opens the board, the detail and the photos;
- `production.manage` makes every step, forward and back;
- `production.deliver` (the point of sale) only marks a ready order delivered
  and undoes that delivery.

The detail shows the order, its plates and the declarations linked to it,
without prices, payment, receipt, the signature or the notarized PDF. The
photos are served by routes of their own that check the declaration is
linked to the order (and the card to the declaration); no route here serves
the signature or the PDF.

The Spanish status labels (PRODUCTION_STATUS_LABELS) are the ones every
screen shows: app.py exposes them to templates, where
_production_badge.html renders the `prod-<status>` badge.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, abort, current_app, redirect, render_template, request, session, url_for

from app.auth.decorators import has_permission, permission_required
from app.core import db
from app.core.db.declarations import REASON_LOSS
from app.core.db.production import (
    STATUS_DELIVERED,
    STATUS_IN_PROGRESS,
    STATUS_PENDING,
    STATUS_READY,
)
from app.core.production import advance_order, revert_order
from app.shared.normalize import inventory_category
from app.shared.uploads import send_stored_file
from app.web.blueprints.declarations import CARD_PHOTO_SIDES, FILE_KINDS

bp = Blueprint("production", __name__)

PRODUCTION_STATUS_LABELS = {
    STATUS_PENDING: "Por realizar",
    STATUS_IN_PROGRESS: "En realización",
    STATUS_READY: "Lista para entregar",
    STATUS_DELIVERED: "Entregada",
}
# Status tabs: (query value, label, list_production_orders(status=...)). "all"
# = no status filter; the default tab is the first one (the work queue).
STATUS_FILTERS = (
    (STATUS_PENDING, "Por realizar", STATUS_PENDING),
    (STATUS_IN_PROGRESS, "En realización", STATUS_IN_PROGRESS),
    (STATUS_READY, "Listas para entregar", STATUS_READY),
    (STATUS_DELIVERED, "Entregadas", STATUS_DELIVERED),
    ("all", "Todas", None),
)
# The tabs that show how many orders they hold.
COUNTED_STATUSES = (STATUS_PENDING, STATUS_IN_PROGRESS, STATUS_READY)
# The forward button of each status (none once delivered).
ADVANCE_LABELS = {
    STATUS_PENDING: "Empezar realización",
    STATUS_IN_PROGRESS: "Marcar lista para entregar",
    STATUS_READY: "Marcar entregada",
}
# Only the cédula photos: the signature and the notarized PDF are never served here.
ID_PHOTO_KINDS = ("id_front", "id_back")


def _may_advance(status: str) -> bool:
    if has_permission("production.manage"):
        return True
    return status == STATUS_READY and has_permission("production.deliver")


def _may_undo(status: str) -> bool:
    if has_permission("production.manage"):
        return True
    return status == STATUS_DELIVERED and has_permission("production.deliver")


def _render_detail(order_id: int, *, error: str | None = None):
    with db.get_conn() as conn:
        order, plates = db.order_detail(conn, order_id)
        status = db.get_production_status(conn, order_id)
        if order is None or status is None:
            abort(404)
        events = db.list_production_events(conn, order_id)
        declarations: list[dict[str, Any]] = [
            {
                "d": declaration,
                "declarant": declaration.submitted_data.get("declarant") or {},
                "cards": db.list_declaration_cards(conn, declaration.id),
            }
            for declaration in db.list_declarations_by_production_order(conn, order_id)
        ]
    page = render_template(
        "production_detail.html", order=order, status=status, events=events,
        plates=[{**p, "material": inventory_category(p["plate_type"])} for p in plates],
        order_voided=any(p["is_voided"] for p in plates), declarations=declarations,
        advance_label=ADVANCE_LABELS.get(status),
        can_advance=status in ADVANCE_LABELS and _may_advance(status),
        can_undo=status != STATUS_PENDING and _may_undo(status),
        loss_reason=REASON_LOSS, error=error,
    )
    return (page, 400) if error else page


@bp.get("/produccion")
@permission_required("production.view")
def production():
    status_values = {value: status for value, _, status in STATUS_FILTERS}
    status_filter = request.args.get("status") or STATUS_PENDING
    if status_filter not in status_values:
        status_filter = STATUS_PENDING
    query = (request.args.get("q") or "").strip()
    with db.get_conn() as conn:
        orders = db.list_production_orders(
            conn, status=status_values[status_filter], query=query or None,
        )
        counts = db.count_by_production_status(conn)
    return render_template(
        "production.html", orders=orders, query=query, status_filter=status_filter,
        status_filters=STATUS_FILTERS, counts=counts, counted_statuses=COUNTED_STATUSES,
    )


@bp.get("/produccion/<int:order_id>")
@permission_required("production.view")
def order_detail(order_id: int):
    return _render_detail(order_id)


def _linked_declaration(conn, order_id: int, declaration_id: int) -> db.DeclarationRecord:
    """The declaration, only when it is linked to this order (404 otherwise)."""
    declaration = db.get_declaration(conn, declaration_id)
    if declaration is None or declaration.production_order_id != order_id:
        abort(404)
    return declaration


@bp.get("/produccion/<int:order_id>/declarations/<int:declaration_id>/file/<kind>")
@permission_required("production.view")
def declaration_file(order_id: int, declaration_id: int, kind: str):
    """A cédula photo of a declaration linked to the order."""
    if kind not in ID_PHOTO_KINDS:
        abort(404)
    attribute, mimetype = FILE_KINDS[kind]
    with db.get_conn() as conn:
        declaration = _linked_declaration(conn, order_id, declaration_id)
    return send_stored_file(getattr(declaration, attribute), mimetype)


@bp.get("/produccion/<int:order_id>/declarations/<int:declaration_id>/cards/<int:card_id>/photo/<side>")
@permission_required("production.view")
def declaration_card_photo(order_id: int, declaration_id: int, card_id: int, side: str):
    """A card photo (front, back or used plates) of a declaration linked to
    the order."""
    attribute = CARD_PHOTO_SIDES.get(side)
    if attribute is None:
        abort(404)
    with db.get_conn() as conn:
        _linked_declaration(conn, order_id, declaration_id)
        cards = db.list_declaration_cards(conn, declaration_id)
    card = next((c for c in cards if c.id == card_id), None)
    if card is None:
        abort(404)
    return send_stored_file(getattr(card, attribute), None)


def _move(order_id: int, *, forward: bool):
    """Shared advance/undo flow: unknown order -> 404; a step the user may not
    make (judged on the posted expected_status) -> 403; ValueError -> detail
    with the message (400, nothing committed); success -> commit + redirect."""
    expected_status = request.form.get("expected_status") or ""
    with db.get_conn() as conn:
        if db.get_production_status(conn, order_id) is None:
            abort(404)
    allowed = _may_advance(expected_status) if forward else _may_undo(expected_status)
    if not allowed:
        abort(403)
    move = advance_order if forward else revert_order
    try:
        with db.get_conn() as conn:
            new_status = move(conn, order_id, session["user_id"], expected_status=expected_status)
            conn.commit()
    except ValueError as exc:
        return _render_detail(order_id, error=str(exc))
    current_app.logger.info(
        "production status: user=%s order_id=%s %s -> %s",
        session.get("user"), order_id, expected_status, new_status,
    )
    return redirect(url_for("production.order_detail", order_id=order_id))


@bp.post("/produccion/<int:order_id>/advance")
@permission_required("production.manage", "production.deliver")
def advance(order_id: int):
    return _move(order_id, forward=True)


@bp.post("/produccion/<int:order_id>/undo")
@permission_required("production.manage", "production.deliver")
def undo(order_id: int):
    return _move(order_id, forward=False)
