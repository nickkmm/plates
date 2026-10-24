"""/ingest and /ingest/pdf -- the "Registrar" form (order header + plate
lines, built by hand or pre-filled from an uploaded pedido PDF)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from flask import Blueprint, abort, current_app, render_template, request, session

from app.core import db
from app.core.db.declarations import STATUS_APPROVED, STATUS_PENDING_REVIEW, requires_notarization
from app.core.declarations import ACTING_AS_GESTOR
from app.core.ingestion import find_duplicate_plate_numbers, process_rows, replace_order
from app.core.models import ExtractedRow
from app.core.normalize import (
    _CITY_SPECIFIC_TYPES,
    CUSTOMER_TYPES,
    DOCUMENT_RELATIONS,
    DOCUMENT_TYPES,
    PAYMENT_METHODS,
    REASONS,
    RECEIPT_TYPES,
    clean_plate,
    normalize_choice,
    normalize_plate_type,
    normalize_requested_by,
    validate_plate,
)
from app.core.pdf_extract import ParsedPedido, PdfExtractError
from app.core.pedido_upload import parse_uploaded_pedido
from app.web.auth import pos_allowed
from app.web.helpers import (
    FORM_PLATE_TYPES,
    _options,
    _padded,
    _validate_order_header,
    customer_fields_from_form,
    today_iso,
)

bp = Blueprint("ingest", __name__)

# Order-level fields the "sticky" header dict carries (Fase 3): the original
# order_date/order_number/requested_by plus every POS field added on top.
# Single source of truth for this key set, used by _sticky_header_from_form
# (rebuilds it from a bounced POST) AND _form_from_pedido (builds it from a
# parsed PDF) -- these two used to drift independently (three separate
# hand-written dicts before this refactor: two inline in ingest_post, one in
# _form_from_pedido), which is exactly how a field silently stops
# round-tripping as more of them get added.
_STICKY_HEADER_FIELDS: tuple[str, ...] = (
    "order_date", "order_number", "requested_by",
    "receipt_type", "receipt_number",
    "customer_type", "customer_document_type", "customer_document_number",
    "customer_document_relation", "customer_phone", "customer_email",
    "customer_address", "customer_neighborhood", "customer_municipality",
    "payment_method", "tracking_number", "client_order_number",
    "delivery_date", "payment_date", "dispatched_by", "received_by", "notes",
)


def _sticky_header_from_form(form) -> dict:
    """Re-derive the order-header fields (order + cliente fieldsets) exactly
    as typed, to redisplay the form without losing work when a duplicate-
    order/duplicate-plate notice interrupts the save. The one place this
    dict is built -- see _STICKY_HEADER_FIELDS' docstring."""
    return {field: form.get(field, "") for field in _STICKY_HEADER_FIELDS}


def _rows_from_form(form) -> list[ExtractedRow]:
    """Build ExtractedRow(s) from the order header + repeated plate lines.

    line_type drives the flags so the user never types special words:
      placa          -> single plate
      rango          -> consecutive plates: requires 'desde' (plate_number) and 'hasta' (plate_to)
      reposicion     -> big non-consecutive lot (no plate, no range)
      anulada_orden  -> voided order (NOT counted in inventory)
      muestra        -> sample (plate_number = MUESTRA)

    Ciudad is per LINE, not per order: one order can ship plates to several
    cities, so each line carries its own (no header field, no inheritance).

    receipt_type/receipt_number/payment_method/tracking_number/
    client_order_number/delivery_date/payment_date/dispatched_by/
    received_by/notes (Fase 2 POS) are genuinely order-level -- read ONCE
    here and stamped identically onto every row of this order, same as
    order_number/order_date/requested_by already are. reason/vehicle_brand/
    unit_price are genuinely per-line (a vehicle brand belongs to one plate,
    not the whole order), so those are read per-row like plate_type/city.
    """
    order_number = form["order_number"].strip()
    order_date = form["order_date"]
    requested_by = form["requested_by"].strip()
    _validate_order_header(requested_by=requested_by, order_date=order_date)

    # payment_method is the one POS field that's genuinely required (per the
    # plan: it's the key /cuadre groups by, and blank silently defeats that
    # report) -- enforced here, not just via the <select required> in HTML,
    # since a raw POST could still omit it.
    payment_method = normalize_choice(form.get("payment_method"), PAYMENT_METHODS)
    if not payment_method:
        raise ValueError("Elegí una forma de pago antes de guardar.")

    receipt_type = normalize_choice(form.get("receipt_type"), RECEIPT_TYPES)
    receipt_number = (form.get("receipt_number") or "").strip() or None
    tracking_number = (form.get("tracking_number") or "").strip() or None
    client_order_number = (form.get("client_order_number") or "").strip() or None
    delivery_date = (form.get("delivery_date") or "").strip() or None
    payment_date = (form.get("payment_date") or "").strip() or None
    dispatched_by = (form.get("dispatched_by") or "").strip() or None
    received_by = (form.get("received_by") or "").strip() or None
    notes = (form.get("notes") or "").strip() or None

    plate_types = form.getlist("plate_type")
    n = len(plate_types)
    rows: list[ExtractedRow] = []
    for lt, pt, pn, pto, qty, ct, rs, vb, up in zip(
        form.getlist("line_type"), plate_types,
        form.getlist("plate_number"), form.getlist("plate_to"), form.getlist("quantity"),
        _padded(form.getlist("city"), n),
        _padded(form.getlist("reason"), n),
        _padded(form.getlist("vehicle_brand"), n),
        _padded(form.getlist("unit_price"), n),
        strict=False,
    ):
        if not (pt or "").strip():
            continue  # skip empty line
        line_number = len(rows) + 1
        # The city is mandatory on every line except a voided order (nothing
        # shipped). Types that are not tied to one city use COLOMBIA.
        if lt != "anulada_orden" and not (ct or "").strip():
            raise ValueError(
                f"Línea {line_number}: elegí la ciudad "
                "(para las placas que no dependen de una ciudad usá COLOMBIA)."
            )
        plate_number = (pn or "").strip() or None
        plate_to = (pto or "").strip() or None
        is_batch = is_voided = is_plate_annulled = False
        ptype = pt.strip()
        if lt == "reposicion":
            is_batch, plate_number, plate_to = True, None, None
        elif lt == "anulada_orden":
            is_voided, plate_number, plate_to = True, None, None
        elif lt == "muestra":
            plate_number, plate_to = plate_number or "MUESTRA", None
        elif lt == "rango":
            if not (plate_number and plate_to):
                raise ValueError("En 'Rango' tenés que indicar desde y hasta.")
            plate_number = validate_plate(plate_number, ptype)  # desde
            plate_to = validate_plate(plate_to, ptype)          # hasta
        elif lt == "placa_anulada":
            # blank was lost -> counts as consumed, but flagged for waste tracking
            plate_to, is_plate_annulled = None, True
            if plate_number:
                plate_number = validate_plate(plate_number, ptype)
        else:  # placa (single)
            plate_to = None
            if plate_number:
                plate_number = validate_plate(plate_number, ptype)
        rows.append(ExtractedRow(
            order_number=order_number, order_date=order_date, requested_by=requested_by,
            city=(ct or "").strip() or None,
            plate_type=pt.strip(), plate_number=plate_number, plate_to=plate_to,
            quantity=int(qty or 1), is_batch_replacement=is_batch, is_voided=is_voided,
            is_plate_annulled=is_plate_annulled,
            receipt_type=receipt_type, receipt_number=receipt_number,
            payment_method=payment_method, tracking_number=tracking_number,
            client_order_number=client_order_number, delivery_date=delivery_date,
            payment_date=payment_date, dispatched_by=dispatched_by, received_by=received_by,
            notes=notes,
            reason=normalize_choice(rs, REASONS), vehicle_brand=(vb or "").strip() or None,
            unit_price=(up or "").strip() or None,
        ))
    return rows


def _sticky_lines_from_form(form) -> list[dict]:
    """Re-derive the plate-line table as-typed, to redisplay the form without
    losing work when a duplicate warning interrupts the save. reason/
    vehicle_brand/unit_price (Fase 2) are _padded(...)-wrapped exactly like
    city already is -- they're optional per-line inputs, so a shorter list
    would otherwise truncate the zip and silently drop plate lines."""
    plate_types = form.getlist("plate_type")
    n = len(plate_types)
    return [
        {"line_type": lt, "plate_type": pt, "plate_number": pn, "plate_to": pto,
         "quantity": qty, "city": ct, "reason": rs, "vehicle_brand": vb, "unit_price": up}
        for lt, pt, pn, pto, qty, ct, rs, vb, up in zip(
            form.getlist("line_type"), plate_types,
            form.getlist("plate_number"), form.getlist("plate_to"), form.getlist("quantity"),
            _padded(form.getlist("city"), n),
            _padded(form.getlist("reason"), n),
            _padded(form.getlist("vehicle_brand"), n),
            _padded(form.getlist("unit_price"), n),
            strict=False,
        )
        if (pt or "").strip()
    ]


def _form_from_pedido(pedido: ParsedPedido) -> tuple[dict, list[dict], list[str]]:
    """Turn a parsed PDF into (header, lines, notices) for the /ingest form.

    Deliberately produces the exact same `header`/`lines` shapes the duplicate-
    warning path builds (see `_sticky_header_from_form`/`_sticky_lines_from_form`),
    so the review step is the real form, pre-filled — not a second UI that could
    drift from it. header uses the SAME key set as _sticky_header_from_form
    (_STICKY_HEADER_FIELDS). Fase 4 fills in every footer/extra-header field
    `pdf_extract` now parses (customer_document_number/tracking_number/
    client_order_number/delivery_date/payment_date/dispatched_by/
    customer_phone/received_by/payment_method/notes); the remaining keys
    (receipt_type/receipt_number/customer_type/customer_document_type/
    customer_document_relation/customer_email/customer_address/
    customer_neighborhood/customer_municipality) have
    no printed field anywhere on the document and stay blank, left for the
    person to fill by hand, same as an unrecognized plate_type already is.

    Anything the PDF left ambiguous is surfaced as a notice and left BLANK
    rather than guessed, so a wrong value can't ride along unnoticed.
    """
    notices: list[str] = []
    if not pedido.order_number:
        notices.append("No se pudo leer el N° de orden de producción — completalo antes de guardar.")
    if pedido.order_date is None:
        notices.append("No se pudo leer la fecha del pedido — completala antes de guardar.")
    elif pedido.order_date.isoformat() > today_iso():
        notices.append(
            f"La fecha del PDF ({pedido.order_date.isoformat()}) es futura — corregila antes de guardar."
        )
    if not pedido.requested_by:
        notices.append("No se pudo leer el cliente — completalo antes de guardar.")

    # normalize_choice never invents a value: blank OR an unrecognized value
    # both come back None here, and both cases get the same notice -- the
    # person always picks it from the dropdown themselves rather than trust
    # a guess.
    payment_method = normalize_choice(pedido.payment_method, PAYMENT_METHODS)
    if not payment_method:
        notices.append("El PDF no indica la forma de pago — elegila antes de guardar.")

    no_type = [str(i) for i, line in enumerate(pedido.lines, start=1) if not line.plate_type]
    if no_type:
        notices.append(
            f"El PDF no marca el tipo de placa en la(s) línea(s) {', '.join(no_type)} — "
            "elegilo antes de guardar."
        )

    # City is stored per plate, so a pedido listing several cities needs no
    # disambiguation: each line just carries its own, exactly as the PDF has it.
    header = dict.fromkeys(_STICKY_HEADER_FIELDS, "")
    header["order_date"] = pedido.order_date.isoformat() if pedido.order_date else ""
    header["order_number"] = pedido.order_number
    header["requested_by"] = pedido.requested_by
    header["customer_document_number"] = pedido.customer_document_number
    header["tracking_number"] = pedido.tracking_number
    header["client_order_number"] = pedido.client_order_number
    header["delivery_date"] = pedido.delivery_date.isoformat() if pedido.delivery_date else ""
    header["payment_date"] = pedido.payment_date.isoformat() if pedido.payment_date else ""
    header["dispatched_by"] = pedido.dispatched_by
    header["customer_phone"] = pedido.customer_phone
    header["received_by"] = pedido.received_by
    header["payment_method"] = payment_method or ""
    header["notes"] = pedido.notes
    lines = [
        {
            "line_type": line.line_type,
            "plate_type": line.plate_type,
            "plate_number": line.plate_number,
            "plate_to": line.plate_to,
            "quantity": line.quantity,
            "city": line.city or "",
            "reason": "",
            "vehicle_brand": "",
            "unit_price": "",
        }
        for line in pedido.lines
    ]
    return header, lines, notices


def _pricing_context(conn) -> dict[str, str]:
    """{plate_type: unit_price} as JSON-safe strings (Decimal isn't directly
    serializable) -- embedded in ingest.html so its JS can pre-fill a line's
    "Valor" input with the current suggested tariff the moment a plate_type
    is chosen, without a round trip. A type with no configured tariff yet is
    simply absent (never a fabricated 0), same contract as
    db.current_prices itself."""
    return {plate_type: str(price) for plate_type, price in db.current_prices(conn).items()}


def _customers_context(conn) -> dict[str, dict]:
    """{customer.name: {attributes...}} embedded in ingest.html so its JS can
    auto-fill the 'cliente' fieldset the instant the typed/selected name
    matches an existing customer -- same "embed the whole small lookup list"
    pattern already used for distinct_requested_by/distinct_cities (this app
    has no JSON/AJAX endpoints anywhere else, so a lookup endpoint would be
    the odd one out). Matching against this dict is a client-side
    convenience only -- resolve_or_create_customer (server-side, via
    normalize_requested_by) is what actually decides which customer an order
    resolves to, so a miss here is harmless."""
    return {
        c.name: {
            "type": c.type or "", "document_type": c.document_type or "",
            "document_number": c.document_number or "", "document_relation": c.document_relation or "",
            "phone": c.phone or "", "email": c.email or "",
            "address": c.address or "", "neighborhood": c.neighborhood or "",
            "municipality": c.municipality or "",
        }
        for c in db.list_customers(conn)
    }


def _form_context(conn) -> dict:
    """The template variables every render of ingest.html needs (dropdown and
    autofill data) -- the same block used to be repeated at each render site."""
    requesters, cities = _options(conn)
    return {
        "requesters": requesters, "cities": cities,
        "categories": FORM_PLATE_TYPES, "today": today_iso(),
        "receipt_types": RECEIPT_TYPES, "customer_types": CUSTOMER_TYPES,
        "document_types": DOCUMENT_TYPES, "document_relations": DOCUMENT_RELATIONS,
        "payment_methods": PAYMENT_METHODS, "reasons": REASONS,
        "prices": _pricing_context(conn), "customers": _customers_context(conn),
    }


# Highest value a Postgres INTEGER id can hold; a larger declaration_id in the
# query string / form can never exist and must not reach the database.
_MAX_ID = 2_147_483_647


def _parse_declaration_id(raw: str | None) -> int | None:
    """The declaration id from the query string / hidden input, or None when
    it is not a positive integer that can be a real id."""
    try:
        value = int((raw or "").strip())
    except ValueError:
        return None
    return value if 0 < value <= _MAX_ID else None


@dataclass
class _DeclarationCheck:
    """Whether a declaration can be turned into an order right now.

    `declaration` is None only when the id does not exist. `error` (Spanish,
    shown to the person) is set whenever it cannot be used -- unknown,
    pending, rejected, already attended or (PÉRDIDA cards) still waiting for
    the notarized copy; for the attended case
    `attended_order` is the order that attended it so the page can link it.
    """

    declaration: db.DeclarationRecord | None
    cards: list[db.DeclarationCardRecord] = field(default_factory=list)
    error: str | None = None
    attended_order: dict | None = None


def _check_declaration(conn, declaration_id: int | None) -> _DeclarationCheck:
    """Re-read the declaration and decide if it is usable: it must exist, be
    `approved`, not yet attended (production_order_id NULL) and, when any card
    is a PÉRDIDA, have its notarized copy received. Used by the
    GET prefill AND, again, by POST /ingest -- the server never trusts the
    hidden input."""
    declaration = db.get_declaration(conn, declaration_id) if declaration_id else None
    if declaration is None:
        return _DeclarationCheck(None, error="La declaración indicada no existe.")
    label = f"La declaración #{declaration.id}"
    if declaration.status == STATUS_PENDING_REVIEW:
        return _DeclarationCheck(
            declaration,
            error=f"{label} todavía está pendiente de revisión: aprobala antes de crear la orden.",
        )
    if declaration.status != STATUS_APPROVED:
        return _DeclarationCheck(
            declaration, error=f"{label} fue rechazada: no se puede crear una orden desde ella.",
        )
    if declaration.production_order_id is not None:
        order, _ = db.order_detail(conn, declaration.production_order_id)
        order_number = order["order_number"] if order else str(declaration.production_order_id)
        return _DeclarationCheck(
            declaration,
            error=f"{label} ya fue atendida por la orden {order_number}.",
            attended_order={"id": declaration.production_order_id, "order_number": order_number},
        )
    cards = db.list_declaration_cards(conn, declaration.id)
    if requires_notarization(cards) and declaration.notarized_received_at is None:
        # PÉRDIDA: the client must bring the notarized copy back first.
        return _DeclarationCheck(
            declaration,
            error=(
                f"{label} tiene tarjetas por pérdida y falta la autenticación notarial: "
                "marcá la autenticación como recibida en la declaración antes de crear la orden."
            ),
        )
    return _DeclarationCheck(declaration, cards=cards)


def _declaration_customer(conn, declaration: db.DeclarationRecord,
                          cards: list[db.DeclarationCardRecord]) -> db.CustomerRecord | None:
    """The declarant as a customer record: the gestor when a gestor filled the
    form in, otherwise the customer the cards were linked to on approval (the
    owner / possessor who declared)."""
    if declaration.acting_as == ACTING_AS_GESTOR:
        return db.get_customer(conn, declaration.gestor_id) if declaration.gestor_id else None
    for card in cards:
        if card.customer_id:
            return db.get_customer(conn, card.customer_id)
    return None


def _form_from_declaration(
    conn, declaration: db.DeclarationRecord, cards: list[db.DeclarationCardRecord],
    prices: dict[str, str],
) -> tuple[dict, list[dict], list[str]]:
    """Turn an approved declaration into (header, lines, notices) for the
    /ingest form -- the same shapes as _form_from_pedido, so the person reviews
    the real form pre-filled and nothing is saved by the GET.

    order_number and payment_method stay blank (staff decide them); city stays
    blank (staff fill it in); unit_price is the current tariff for the card's
    type when there is one.
    """
    notices = ["Completá el N° de orden y elegí la forma de pago."]
    header = dict.fromkeys(_STICKY_HEADER_FIELDS, "")
    header["order_date"] = today_iso()

    customer = _declaration_customer(conn, declaration, cards)
    if customer is not None:
        header["requested_by"] = customer.name
        header["customer_type"] = customer.type or ""
        header["customer_document_type"] = customer.document_type or ""
        header["customer_document_number"] = customer.document_number or ""
        header["customer_document_relation"] = customer.document_relation or ""
        header["customer_phone"] = customer.phone or ""
        header["customer_email"] = customer.email or ""
        header["customer_address"] = customer.address or ""
        header["customer_neighborhood"] = customer.neighborhood or ""
        header["customer_municipality"] = customer.municipality or ""
    else:
        declarant = declaration.submitted_data.get("declarant") or {}
        header["requested_by"] = normalize_requested_by(str(declarant.get("name") or ""))
        notices.append(
            "No se encontró el cliente de esta declaración entre los clientes guardados: "
            "revisá el nombre y completá sus datos."
        )

    lines: list[dict] = []
    unknown_type: list[str] = []
    city_specific: list[str] = []
    for number, card in enumerate(cards, start=1):
        plate_type = normalize_plate_type(card.vehicle_type) or ""
        if plate_type not in FORM_PLATE_TYPES:
            plate_type = ""
            unknown_type.append(str(number))
        elif plate_type in _CITY_SPECIFIC_TYPES:
            city_specific.append(str(number))
        lines.append({
            "line_type": "placa", "plate_type": plate_type,
            "plate_number": card.plate_number, "plate_to": "",
            "quantity": card.plate_quantity, "city": "",
            "reason": card.reason, "vehicle_brand": card.vehicle_brand or "",
            "unit_price": prices.get(plate_type, ""),
        })
    if unknown_type:
        notices.append(
            f"La tarjeta de propiedad de la(s) línea(s) {', '.join(unknown_type)} no tiene un tipo "
            "de placa reconocido — elegilo antes de guardar."
        )
    if city_specific:
        notices.append(
            f"Completá la ciudad de la(s) línea(s) {', '.join(city_specific)}: las placas de "
            "CARRO PARTICULAR y CARRO PÚBLICO son específicas de una ciudad."
        )
    return header, lines, notices


def _blocking_duplicates(
    duplicates: set[str], rows: list[ExtractedRow], exempt_plates: frozenset[str],
) -> set[str]:
    """The duplicate plate numbers that still block a save once the plates of
    the attended declaration are exempt (reprinting a lost / deteriorated plate
    is the whole point of that order). An exempt plate typed twice in this same
    submission is still a likely typo, so it keeps blocking."""
    typed = Counter(r.plate_number for r in rows if r.plate_number)
    return {pn for pn in duplicates if pn not in exempt_plates or typed[pn] > 1}


def _mark_declaration_attended(conn, declaration_id: int, order_number: str) -> None:
    """Link the declaration to the order just created under `order_number`
    (the newest one, if the person confirmed saving beside an existing order
    with that number). Runs in the SAME transaction as the save, and refuses
    -- rolling the whole save back -- if someone else attended it meanwhile."""
    declaration = db.get_declaration(conn, declaration_id)
    if declaration is None or declaration.production_order_id is not None:
        raise ValueError(f"La declaración #{declaration_id} ya fue atendida por otra orden.")
    orders = db.find_orders_by_number(conn, order_number)
    if not orders:
        raise ValueError("No se encontró la orden recién creada para marcar la declaración como atendida.")
    db.set_declaration_production_order(conn, declaration_id, max(o.id for o in orders))


@bp.get("/ingest")
@pos_allowed
def ingest():
    """The blank form, or -- with `?declaration_id=N` -- the form pre-filled
    from an approved, not yet attended declaration (nothing is saved here)."""
    raw_declaration_id = request.args.get("declaration_id")
    declaration_id = _parse_declaration_id(raw_declaration_id)
    if raw_declaration_id is not None and declaration_id is None:
        abort(404)
    with db.get_conn() as conn:
        context = _form_context(conn)
        if declaration_id is None:
            return render_template("ingest.html", **context)
        check = _check_declaration(conn, declaration_id)
        if check.declaration is None:
            abort(404)
        if check.error:
            return render_template(
                "ingest.html", **context, error=check.error, attended_order=check.attended_order,
            )
        header, lines, notices = _form_from_declaration(
            conn, check.declaration, check.cards, context["prices"],
        )
    return render_template(
        "ingest.html", **context, header=header, lines=lines, notices=notices,
        notices_title=(
            f"Revisá esto antes de guardar — los datos vienen de la declaración "
            f"#{declaration_id}, todavía no se guardó nada."
        ),
        declaration_id=declaration_id,
    )


@bp.post("/ingest")
@pos_allowed
def ingest_post():
    summary = None
    error = None
    duplicates = None
    order_duplicates = None
    header = None
    lines = None
    # Set only when the hidden `declaration_id` re-validated OK (see
    # _check_declaration); None for a normal order or a refused declaration.
    declaration_id: int | None = None
    attended_order = None
    attended_declaration_id: int | None = None
    exempt_plates: frozenset[str] = frozenset()
    try:
        raw_declaration_id = (request.form.get("declaration_id") or "").strip()
        if raw_declaration_id:
            with db.get_conn() as conn:
                check = _check_declaration(conn, _parse_declaration_id(raw_declaration_id))
            if check.error or check.declaration is None:
                # Refuse to save, but give the form back exactly as typed.
                error = check.error
                attended_order = check.attended_order
                header = _sticky_header_from_form(request.form)
                lines = _sticky_lines_from_form(request.form)
            else:
                declaration_id = check.declaration.id
                exempt_plates = frozenset(clean_plate(card.plate_number) for card in check.cards)

        rows = [] if error else _rows_from_form(request.form)
        if error:
            pass  # the declaration was refused above
        elif not rows:
            error = "Agregá al menos una placa."
        elif request.form.get("replace_order"):
            # Explicit, opt-in fuse (see the "Reemplazar" button below): wipe
            # every existing order under this order_number and replace it with
            # exactly one new order built from these rows. The plate-number
            # duplicate check below is moot for this choice -- we're about to
            # delete every plate currently under this order_number outright,
            # so nothing already there can register as "already existing"
            # against the fresh rows being inserted in its place.
            with db.get_conn() as conn:
                db.resolve_or_create_customer(
                    conn, rows[0].requested_by, **customer_fields_from_form(request.form)
                )
                summary = replace_order(conn, rows[0].order_number, rows,
                                        created_by=session.get("user_id"))
                if declaration_id is not None:
                    _mark_declaration_attended(conn, declaration_id, rows[0].order_number)
                    attended_declaration_id = declaration_id
            current_app.logger.info(
                "order replaced: user=%s order_number=%s plates=%d",
                session.get("user"), rows[0].order_number, len(rows),
            )
        else:
            # One connection for the whole route from here down: the
            # order-number collision check, the plate-number dup check, and
            # the eventual insert all need to see each other's work within a
            # single transaction (previously each opened its own connection,
            # so a single POST /ingest ran across several separate
            # transactions -- not atomic).
            with db.get_conn() as conn:
                # Order-number collision check runs BEFORE the plate-number dup
                # check: a reposición placeholder order re-saved via a re-uploaded
                # PDF has plate_number=NULL, so it would sail right through the
                # plate check and silently create a second production_orders row
                # under the same order_number (see replace_order's docstring for
                # the real WCY449/pedido 1030 case this guards against).
                order_duplicates = db.find_orders_by_number(conn, rows[0].order_number)
                if order_duplicates and not request.form.get("confirm_order_duplicate"):
                    header = _sticky_header_from_form(request.form)
                    lines = _sticky_lines_from_form(request.form)
                else:
                    order_duplicates = None  # not a collision the user needs to see anymore
                    dup_plate_numbers = _blocking_duplicates(
                        find_duplicate_plate_numbers(conn, rows), rows, exempt_plates,
                    )
                    if dup_plate_numbers and not request.form.get("confirm_duplicate"):
                        duplicates = {
                            pn: db.find_orders_for_plate(conn, pn) for pn in sorted(dup_plate_numbers)
                        }
                        header = _sticky_header_from_form(request.form)
                        lines = _sticky_lines_from_form(request.form)
                    else:
                        db.resolve_or_create_customer(
                            conn, rows[0].requested_by, **customer_fields_from_form(request.form)
                        )
                        summary = process_rows(conn, rows, created_by=session.get("user_id"))
                        if declaration_id is not None:
                            _mark_declaration_attended(conn, declaration_id, rows[0].order_number)
                            attended_declaration_id = declaration_id
                        current_app.logger.info(
                            "order created: user=%s order_number=%s plates=%d",
                            session.get("user"), rows[0].order_number, len(rows),
                        )
    except Exception as e:  # surface validation/DB errors without crashing
        error = f"Error: {e}"
        summary = None  # the transaction rolled back, nothing was saved
        attended_declaration_id = None
        if declaration_id is not None and header is None:
            # A declaration-backed order is worth not retyping: keep the form.
            header = _sticky_header_from_form(request.form)
            lines = _sticky_lines_from_form(request.form)

    with db.get_conn() as conn:
        context = _form_context(conn)
    return render_template(
        "ingest.html", **context,
        summary=summary.model_dump() if summary else None, error=error,
        duplicates=duplicates, order_duplicates=order_duplicates, header=header, lines=lines,
        # After a successful save the form is blank again, so the hidden field
        # is only carried while the declaration still has to be attended.
        declaration_id=None if attended_declaration_id else declaration_id,
        attended_declaration_id=attended_declaration_id, attended_order=attended_order,
    )


@bp.route("/ingest/pdf", methods=["GET", "POST"])
@pos_allowed
def ingest_pdf():
    """Upload a pedido, PDF or Excel (.xlsx) (GET), and review the parse in the normal form (POST).

    POST intentionally does NOT save: it renders `ingest.html` pre-filled, so
    the person reviews/corrects and then submits to POST /ingest exactly like a
    hand-typed order — including the duplicate-plate warning gate, for free.
    """
    if request.method == "GET":
        return render_template("ingest_pdf.html")

    upload = request.files.get("file") or request.files.get("pdf")  # "pdf": pages opened before the rename
    if upload is None or not upload.filename:
        return render_template("ingest_pdf.html", error="Elegí un archivo (PDF o Excel) para subir."), 400

    try:
        pedido = parse_uploaded_pedido(upload.read())
    except PdfExtractError as e:
        return render_template("ingest_pdf.html", error=str(e)), 400
    except Exception:  # never 500 on a bad upload — always offer manual entry
        current_app.logger.exception("Unexpected failure parsing uploaded pedido")
        return render_template(
            "ingest_pdf.html",
            error="No se pudo procesar el archivo. Ingresá la orden manualmente.",
        ), 400

    header, lines, notices = _form_from_pedido(pedido)
    with db.get_conn() as conn:
        # Read-only: surface "esta orden ya existe" as soon as the file is
        # previewed, not only after a first failed POST /ingest save attempt.
        # This route never writes to the DB either way.
        order_duplicates = (
            db.find_orders_by_number(conn, pedido.order_number) if pedido.order_number else []
        )
        context = _form_context(conn)
    return render_template(
        "ingest.html", **context,
        header=header, lines=lines, notices=notices, order_duplicates=order_duplicates,
    )
