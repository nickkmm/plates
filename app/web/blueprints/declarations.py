"""/declaraciones -- internal review screen for sworn declarations.

The public form (/declaracion) stores a declaration: ONE declarant (who signs
once) and 1..N ownership cards ("tarjetas de propiedad"), each with its own
reason, plate quantity, owner and review flags. EVERY declaration is stored as
`pending_review` (flags or not) and creates nothing until approved. This screen
is where the point of sale (the customer fills the form, staff only reviews)
reads what was declared, sees per card the real context behind each flag
(previous owner, last fabrication date, ...) and approves, rejects or reassigns
the declaration as a whole.

Each declaration shows ONE status badge, its derived stage
(db.declaration_stage): Por revisar / Falta autenticación / Lista para pedido /
Atendida / Rechazada. The list's tabs (`?stage=`) filter by that stage.

Access: EVERY route is @pos_allowed (admin + point_of_sale); `reviewed_by`
records the acting session user. The read-only 'production' role gets 403 on
everything here: it handles identity documents and signatures. The flags
context (previous owner, fabrication dates) is shown ONLY here, never to the
anonymous submitter.

An approved declaration that no order has attended yet (production_order_id
NULL) shows a "Crear pedido" button on the detail page that opens
/ingest?declaration_id=N (the /ingest save marks it attended).

Notarization: when an approved declaration has at least one PÉRDIDA card, the
client must have it notarized first. Staff download the generated PDF
(GET .../pdf), hand it to the client and, when the client comes back with it
authenticated, mark it received (POST .../notarized; .../notarized/undo
reverts). Until then the "Crear pedido" button is replaced by a note and
/ingest refuses the declaration (see ingest._check_declaration). Pure-DETERIORO
declarations are never gated, but the same PDF can still be downloaded as an
optional extra.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from datetime import date, datetime
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
    REASON_DETERIORATION,
    REASON_LOSS,
    STAGE_ATTENDED,
    STAGE_AWAITING_NOTARIZATION,
    STAGE_READY,
    STAGE_REJECTED,
    STAGE_TO_REVIEW,
    STATUS_APPROVED,
    STATUS_PENDING_REVIEW,
    declaration_stage,
    requires_notarization,
)
from app.core.declaration_pdf import build_declaration_pdf
from app.core.normalize import clean_plate, normalize_requested_by
from app.web.auth import pos_allowed
from app.web.uploads import PHOTO_MIMETYPES, UploadError, resolve_upload_path

bp = Blueprint("declarations", __name__)

# The single status badge of a declaration (see db.declaration_stage).
STAGE_LABELS = {
    STAGE_TO_REVIEW: "Por revisar",
    STAGE_AWAITING_NOTARIZATION: "Falta autenticación",
    STAGE_READY: "Lista para pedido",
    STAGE_ATTENDED: "Atendida",
    STAGE_REJECTED: "Rechazada",
}
# Stage tabs: (query value, label, db.list_declarations(stage=...)). "all" = no
# stage filter; the default tab is the first one (the work queue).
STAGE_FILTERS = (
    (STAGE_TO_REVIEW, "Por revisar", STAGE_TO_REVIEW),
    (STAGE_AWAITING_NOTARIZATION, "Falta autenticación", STAGE_AWAITING_NOTARIZATION),
    (STAGE_READY, "Lista para pedido", STAGE_READY),
    (STAGE_ATTENDED, "Atendidas", STAGE_ATTENDED),
    (STAGE_REJECTED, "Rechazadas", STAGE_REJECTED),
    ("all", "Todas", None),
)
# Reason filter: (query value, label, db.list_declarations(reason=...)). Any
# card of the declaration having that reason matches.
REASON_FILTERS = (
    ("all", "Todos", None),
    ("loss", "Pérdida", REASON_LOSS),
    ("deterioration", "Deterioro", REASON_DETERIORATION),
)
FLAG_LABELS = {
    FLAG_PLATE_MADE_RECENTLY: "Placa reciente",
    FLAG_CARD_OTHER_OWNER: "Tarjeta de otro dueño",
    FLAG_CARD_DATA_MISMATCH: "Datos no cuadran",
    FLAG_CUSTOMER_TYPE_MISMATCH: "Tipo de cliente",
    FLAG_CUSTOMER_DOCUMENT_MISMATCH: "Documento distinto",
}
# kind -> (DeclarationRecord attribute holding the stored name, mimetype)
# A None mimetype means "a photo": taken from the stored file's extension.
FILE_KINDS = {
    "signature": ("declarant_signature_path", "image/png"),
    "notarized_pdf": ("notarized_pdf_path", "application/pdf"),
    "id_front": ("declarant_id_front_path", None),
    "id_back": ("declarant_id_back_path", None),
}
# side -> DeclarationCardRecord attribute holding the stored name of the card photo
CARD_PHOTO_SIDES = {"front": "card_front_path", "back": "card_back_path"}
# Approve-form checkbox: reassign_<declaration_cards.id>.
_REASSIGN_FIELD = re.compile(r"reassign_(\d+)")
# Plates shown in the list's compact summary before collapsing into "+N".
SUMMARY_PLATES = 2


def _fmt_date(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def _declarant(declaration: db.DeclarationRecord) -> dict[str, Any]:
    return declaration.submitted_data.get("declarant") or {}


def _ascii_lower(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", value.strip().lower()) if not unicodedata.combining(c)
    )


def _reason_filter(raw: str | None) -> str:
    """The `reason` query value -> one of REASON_FILTERS' keys ("all" when
    missing or unknown). The stored words (PÉRDIDA / DETERIORO) work too."""
    aliases = {"perdida": "loss", "deterioro": "deterioration"}
    key = _ascii_lower(raw or "")
    key = aliases.get(key, key)
    return key if key in {value for value, _, _ in REASON_FILTERS} else "all"


def _reasons_text(cards: list[db.DeclarationCardRecord]) -> str:
    """"2 pérdida · 1 deterioro": cards per reason, PÉRDIDA first (it is the
    one that needs the notary)."""
    counts: dict[str, int] = {}
    for card in cards:
        counts[card.reason] = counts.get(card.reason, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (item[0] != REASON_LOSS, item[0]))
    return " · ".join(f"{count} {reason.lower()}" for reason, count in ordered)


def _summarize(cards: list[db.DeclarationCardRecord]) -> dict[str, Any]:
    """What the list shows for a declaration's cards: counts, total plates, a
    compact "ABC123 · DEF45G +3" and the reasons ("2 pérdida · 1 deterioro")."""
    plates = [card.plate_number for card in cards]
    shown = " · ".join(plates[:SUMMARY_PLATES])
    if len(plates) > SUMMARY_PLATES:
        shown += f" +{len(plates) - SUMMARY_PLATES}"
    return {
        "card_count": len(cards),
        "plate_total": sum(card.plate_quantity for card in cards),
        "plates": shown or "—",
        "reasons": _reasons_text(cards) or "—",
        "has_loss": requires_notarization(cards),
        "flagged_numbers": [card.position + 1 for card in cards if card.review_flags],
    }


def _card_line(conn: psycopg.Connection[DictRow], card: db.OwnershipCardRecord) -> str:
    owner = db.get_customer(conn, card.customer_id)
    owner_name = owner.name if owner else f"cliente #{card.customer_id}"
    return (
        f"licencia {card.transit_license}, placa {card.plate_number}, "
        f"a nombre de {owner_name}."
    )


def _name_key(person: dict[str, Any]) -> str:
    return normalize_requested_by(str(person.get("name") or ""))


def _flag_details(
    conn: psycopg.Connection[DictRow], declaration: db.DeclarationRecord,
    card: db.DeclarationCardRecord, cards: list[db.DeclarationCardRecord],
) -> list[dict[str, Any]]:
    """One explanation per flag of ONE card, with the real context staff need.

    Flags raised by the declarant (a GESTOR's type/document) are copied onto
    every card by the core, so those lines name who they are about
    ("Gestor ...")."""
    owner: dict[str, Any] = card.owner_data or {}
    is_gestor = declaration.acting_as == decl.ACTING_AS_GESTOR
    gestor: dict[str, Any] = _declarant(declaration) if is_gestor else {}
    plate = clean_plate(card.plate_number)
    license_ = card.transit_license
    details: list[dict[str, Any]] = []

    for flag in card.review_flags:
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
            if gestor and owner and _name_key(owner) == _name_key(gestor):
                lines.append(
                    f"'{owner.get('name')}' figura como gestor y como propietario/poseedor "
                    "en esta misma declaración."
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
            for other in cards:
                other_owner = other.owner_data or {}
                if (
                    other.position < card.position and _name_key(other_owner) == _name_key(owner)
                    and (other_owner.get("document_number") or "") != (owner.get("document_number") or "")
                ):
                    lines.append(
                        f"En la tarjeta {other.position + 1} el mismo nombre '{owner.get('name')}' "
                        f"aparece con el documento {other_owner.get('document_number') or '—'}."
                    )
        else:
            title = flag
        details.append({"flag": flag, "title": title, "lines": lines})
    return details


def _render_detail(
    declaration_id: int, *, error: str | None = None, reassign_ids: frozenset[int] = frozenset(),
):
    with db.get_conn() as conn:
        declaration = db.get_declaration(conn, declaration_id)
        if declaration is None:
            return redirect(url_for("declarations.declarations"))
        gestor = db.get_customer(conn, declaration.gestor_id) if declaration.gestor_id else None
        card_records = db.list_declaration_cards(conn, declaration_id)
        pending = declaration.status == STATUS_PENDING_REVIEW
        stage = declaration_stage(declaration, card_records)
        attended_order: dict[str, Any] | None = None
        if declaration.production_order_id is not None:
            attended_order, _plates = db.order_detail(conn, declaration.production_order_id)
        needs_notary = declaration.status == STATUS_APPROVED and requires_notarization(card_records)
        received_user = (
            db.get_user_by_id(conn, declaration.notarized_received_by)
            if declaration.notarized_received_by is not None else None
        )
        cards: list[dict[str, Any]] = []
        for card in card_records:
            cards.append({
                "record": card,
                "number": card.position + 1,
                "flag_details": _flag_details(conn, declaration, card, card_records) if pending else [],
                "customer": db.get_customer(conn, card.customer_id) if card.customer_id else None,
                "ownership_card": (
                    db.get_ownership_card(conn, card.ownership_card_id) if card.ownership_card_id else None
                ),
                "can_reassign": pending and FLAG_CARD_OTHER_OWNER in card.review_flags,
            })
    page = render_template(
        "declaration_detail.html", d=declaration, declarant=_declarant(declaration), gestor=gestor,
        cards=cards, flagged_cards=[c for c in cards if c["record"].review_flags],
        plate_total=sum(c["record"].plate_quantity for c in cards),
        flag_labels=FLAG_LABELS, stage=stage, stage_labels=STAGE_LABELS,
        error=error, pending_status=STATUS_PENDING_REVIEW, approved_status=STATUS_APPROVED, reassign_ids=reassign_ids,
        attended_order=attended_order,
        needs_notary=needs_notary, notary_received=declaration.notarized_received_at is not None,
        notary_received_by=received_user.username if received_user else None,
        loss_reason=REASON_LOSS,
    )
    return (page, 400) if error else page


@bp.get("/declaraciones")
@pos_allowed
def declarations():
    stage_values = {value: stage for value, _, stage in STAGE_FILTERS}
    stage_filter = request.args.get("stage") or STAGE_TO_REVIEW
    if stage_filter not in stage_values:
        stage_filter = STAGE_TO_REVIEW
    reason_values = {value: reason for value, _, reason in REASON_FILTERS}
    reason_filter = _reason_filter(request.args.get("reason"))
    query = (request.args.get("q") or "").strip()
    with db.get_conn() as conn:
        records = db.list_declarations(
            conn, stage=stage_values[stage_filter], query=query or None,
            reason=reason_values[reason_filter],
        )
        pending_count = len(db.list_declarations(conn, stage=STAGE_TO_REVIEW))
        cards_by_declaration = db.list_cards_for_declarations(conn, [d.id for d in records])
    rows = []
    for d in records:
        cards = cards_by_declaration[d.id]
        rows.append({
            "d": d, "declarant": _declarant(d), "summary": _summarize(cards),
            "stage": declaration_stage(d, cards),
        })
    return render_template(
        "declarations.html", rows=rows, query=query, stage_filter=stage_filter,
        stage_filters=STAGE_FILTERS, pending_count=pending_count, flag_labels=FLAG_LABELS,
        stage_labels=STAGE_LABELS,
        reason_filter=reason_filter, reason_filters=REASON_FILTERS,
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
    return _send_stored_file(getattr(declaration, attribute), mimetype)


@bp.get("/declaraciones/<int:declaration_id>/cards/<int:card_id>/photo/<side>")
@pos_allowed
def declaration_card_photo(declaration_id: int, card_id: int, side: str):
    """The front or back photo of one ownership card of the declaration."""
    attribute = CARD_PHOTO_SIDES.get(side)
    if attribute is None:
        abort(404)
    with db.get_conn() as conn:
        cards = db.list_declaration_cards(conn, declaration_id)
    card = next((c for c in cards if c.id == card_id), None)
    if card is None:
        abort(404)
    return _send_stored_file(getattr(card, attribute), None)


def _photo_entries(
    declaration: db.DeclarationRecord, cards: list[db.DeclarationCardRecord],
) -> list[tuple[str, str]]:
    """(file name inside the ZIP, stored name) of every photo of a declaration,
    in reading order: the cédula, then each card (front, back)."""
    entries: list[tuple[str, str]] = []
    for label, stored in (("cedula-frente", declaration.declarant_id_front_path),
                          ("cedula-atras", declaration.declarant_id_back_path)):
        if stored:
            entries.append((label, stored))
    for card in cards:
        plate = clean_plate(card.plate_number) or "sin-placa"
        for label, stored in (("frente", card.card_front_path), ("atras", card.card_back_path)):
            if stored:
                entries.append((f"tarjeta-{card.position + 1}-{plate}-{label}", stored))
    return entries


@bp.get("/declaraciones/<int:declaration_id>/photos.zip")
@pos_allowed
def declaration_photos_zip(declaration_id: int):
    """Every photo of the declaration (cédula and ownership cards) in one ZIP.
    Stored names are resolved with resolve_upload_path; a photo whose file is
    gone is skipped; 404 when there is nothing to download."""
    with db.get_conn() as conn:
        declaration = db.get_declaration(conn, declaration_id)
        cards = db.list_declaration_cards(conn, declaration_id) if declaration else []
    if declaration is None:
        abort(404)
    buffer = io.BytesIO()
    added = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:   # JPEG/PNG: no gain from deflate
        for entry_name, stored in _photo_entries(declaration, cards):
            try:
                path = resolve_upload_path(stored)
            except UploadError:
                continue
            if path.is_file():
                archive.write(path, f"{entry_name}{path.suffix}")
                added += 1
    if not added:
        abort(404)
    buffer.seek(0)
    response = send_file(
        buffer, mimetype="application/zip", as_attachment=True,
        download_name=f"declaracion-{declaration_id}-fotos.zip",
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


def _send_stored_file(stored_name: str | None, mimetype: str | None):
    """Serve an upload by its stored name (never a client-supplied path):
    inline, `nosniff`, `no-store`; 404 when there is none or it is gone.
    `mimetype=None` means a photo, typed by the stored extension."""
    if not stored_name:
        abort(404)
    try:
        path = resolve_upload_path(stored_name)
    except UploadError:
        abort(404)
    if not path.is_file():
        abort(404)
    if mimetype is None:
        mimetype = PHOTO_MIMETYPES.get(path.suffix.lstrip("."))
        if mimetype is None:
            abort(404)
    response = send_file(path, mimetype=mimetype, as_attachment=False)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


def _reassign_ids_from_form() -> frozenset[int]:
    """`declaration_cards.id` values ticked in the approve form
    (`reassign_<id>` checkboxes). The core refuses ids that do not belong to
    the declaration."""
    ids: set[int] = set()
    for field in request.form:
        match = _REASSIGN_FIELD.fullmatch(field)
        if match:
            ids.add(int(match.group(1)))
    return frozenset(ids)


def _decide(declaration_id: int, decision: str):
    """Shared approve/reject flow: unknown id -> list; ValueError -> detail
    with the message (400, nothing changed); success -> commit + redirect."""
    user_id = session["user_id"]
    with db.get_conn() as conn:
        if db.get_declaration(conn, declaration_id) is None:
            return redirect(url_for("declarations.declarations"))
    note = (request.form.get("note") or "").strip()
    reassign_ids = _reassign_ids_from_form() if decision == "approve" else frozenset()
    try:
        with db.get_conn() as conn:
            if decision == "approve":
                decl.approve_declaration(
                    conn, declaration_id, user_id, reassign_card_ids=reassign_ids, note=note or None,
                )
            else:
                decl.reject_declaration(conn, declaration_id, user_id, note)
            conn.commit()
    except ValueError as exc:
        return _render_detail(declaration_id, error=str(exc), reassign_ids=reassign_ids)
    current_app.logger.info(
        "declaration review: user=%s declaration_id=%s decision=%s",
        session.get("user"), declaration_id, decision,
    )
    return redirect(url_for("declarations.declaration_detail", declaration_id=declaration_id))


@bp.post("/declaraciones/<int:declaration_id>/approve")
@pos_allowed
def declaration_approve(declaration_id: int):
    return _decide(declaration_id, "approve")


@bp.post("/declaraciones/<int:declaration_id>/reject")
@pos_allowed
def declaration_reject(declaration_id: int):
    return _decide(declaration_id, "reject")


def _read_signature(declaration: db.DeclarationRecord) -> bytes | None:
    """The declarant's signature PNG, read the same safe way as the file
    download (stored name -> resolve_upload_path); None when there is none or
    the file is missing/unreadable."""
    if not declaration.declarant_signature_path:
        return None
    try:
        path = resolve_upload_path(declaration.declarant_signature_path)
        return path.read_bytes() if path.is_file() else None
    except (UploadError, OSError):
        return None


@bp.get("/declaraciones/<int:declaration_id>/pdf")
@pos_allowed
def declaration_pdf(declaration_id: int):
    """The sworn declaration as a PDF to print and hand to the client. Any
    APPROVED declaration; it is mandatory (notarization) only with a PÉRDIDA
    card, for DETERIORO it is an optional extra."""
    with db.get_conn() as conn:
        declaration = db.get_declaration(conn, declaration_id)
        cards = db.list_declaration_cards(conn, declaration_id) if declaration else []
    if declaration is None:
        abort(404, "La declaración no existe.")
    if declaration.status != STATUS_APPROVED:
        abort(404, "El PDF solo está disponible para declaraciones aprobadas.")
    pdf = build_declaration_pdf(
        declaration=declaration, cards=cards, signature_png=_read_signature(declaration),
        issued_at=datetime.now(),
    )
    response = send_file(
        io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
        download_name=f"declaracion-{declaration_id}.pdf",
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


def _set_notarized(declaration_id: int, *, received: bool):
    """Shared mark/undo flow. Unknown id -> list; an ineligible declaration
    (not approved, no PÉRDIDA card, or -- for the undo -- already attended by
    an order) -> detail with a message (400, nothing changed)."""
    user_id = session["user_id"]
    error = None
    with db.get_conn() as conn:
        declaration = db.get_declaration(conn, declaration_id)
        if declaration is None:
            return redirect(url_for("declarations.declarations"))
        cards = db.list_declaration_cards(conn, declaration_id)
        if declaration.status != STATUS_APPROVED or not requires_notarization(cards):
            error = "Solo las declaraciones aprobadas con tarjetas por pérdida requieren autenticación notarial."
        elif not received and declaration.production_order_id is not None:
            error = "No se puede deshacer: esta declaración ya fue atendida por un pedido."
        elif received != (declaration.notarized_received_at is not None):
            # Marking twice keeps the first stamp (who/when it really arrived).
            db.set_declaration_notarized_received(
                conn, declaration_id, received_by=user_id, received=received,
            )
            conn.commit()
            current_app.logger.info(
                "declaration notarized: user=%s declaration_id=%s received=%s",
                session.get("user"), declaration_id, received,
            )
    if error:
        return _render_detail(declaration_id, error=error)
    return redirect(url_for("declarations.declaration_detail", declaration_id=declaration_id))


@bp.post("/declaraciones/<int:declaration_id>/notarized")
@pos_allowed
def declaration_notarized(declaration_id: int):
    return _set_notarized(declaration_id, received=True)


@bp.post("/declaraciones/<int:declaration_id>/notarized/undo")
@pos_allowed
def declaration_notarized_undo(declaration_id: int):
    return _set_notarized(declaration_id, received=False)
