"""Sworn-declaration business logic (public form + staff review). No Flask.

The data layer is app/core/db/declarations.py; this module decides what a
submitted declaration does. Takes an open `conn` and never commits/rolls back
(same contract as app/core/db/__init__.py).

**Model**: a declaration has ONE declarant (the person who signs, once, for
everything declared) and 1..MAX_CARDS ownership cards ("tarjetas de
propiedad"), each stored as a `declaration_cards` row with its own reason
(DETERIORO / PÉRDIDA), plate quantity, owner and review flags. If the declarant
acts as PROPIETARIO / POSEEDOR every card belongs to the declarant; if it acts
as GESTOR the declarant is the GESTOR customer (`declarations.gestor_id`) and
EACH card has its own owner (a CLIENTE). Nothing is uploaded with a
declaration other than the signature: for a PÉRDIDA the notarization happens
offline (staff hand the client the declaration as a PDF, the client gets it
notarized and brings it back), so no card reason requires a file.

Decision rules for `submit_declaration` (all decided by the owner of the
project, see the declarations plan), applied PER CARD:

* EVERY submitted declaration is stored `pending_review`, with or without
  flags; submitting creates NOTHING (no customer, no gestor, no ownership
  card) and every link stays NULL. `submitted_data` is then the only complete
  copy of what was declared. `declarations.review_flags` is the union of the
  cards' flags (empty for a clean declaration); each card keeps its own flags.
  The flags only tell the reviewer what to look at.
* `approve_declaration` (point-of-sale review) is what creates the CLIENTE
  owners (and the GESTOR, if any) only if absent -- existing ones are linked
  and NEVER modified; the same owner repeated across cards is ONE customer
  (customers are identified by their canonical name) -- and each ownership
  card is created (`new`) or linked untouched (`same_card`), all in the same
  transaction. It re-checks every conflict at approval time, so one that
  appeared after submission raises ValueError and changes nothing.

Flags: PLATE_MADE_RECENTLY (made < 6 calendar months ago), CARD_OTHER_OWNER,
CARD_DATA_MISMATCH (licence/plate that do not match each other) and
CUSTOMER_TYPE_MISMATCH (an existing customer found by name is not of the
expected type; NULL counts as a mismatch; also raised when the same name is
declared both as gestor and as a card owner in the same submission) and
CUSTOMER_DOCUMENT_MISMATCH (an existing customer found by name already has a
document number and it differs from the declared one -- a homonym, not the
same person; also raised when the same name is declared with two different
document numbers in the same submission). Flags that concern the declarant
(the gestor customer) are recorded on every card.

**Canonical `submitted_data`** (what `_build_submitted_data` writes and the
0009 migration backfills):
`{"acting_as", "declarant": {PersonInput fields}, "cards": [{OwnershipCardInput
fields as typed, with "owner" = the effective owner's PersonInput fields and
"owner_relation" = DUEÑO / POSEEDOR}], "raw": <the form's own copy>}`.

**Privacy**: `SubmitResult.flags` is for staff/logging ONLY. The web layer
must never show the flags (or anything about the previous owner / fabrication
date) to the anonymous submitter -- only "Recibimos tu declaración".
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from dateutil.relativedelta import relativedelta
from psycopg.rows import DictRow

from app.core import db
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
from app.core.normalize import (
    ACTING_AS,
    DECLARATION_REASONS,
    DOCUMENT_RELATIONS,
    DOCUMENT_TYPES,
    PLATE_PATTERNS,
    PLATE_TYPES,
    clean_plate,
    max_plates_per_card,
    normalize_choice,
    normalize_requested_by,
    validate_plate,
)

# Re-exported so callers of the logic layer need only this module.
PLATE_MADE_RECENTLY = FLAG_PLATE_MADE_RECENTLY
CARD_OTHER_OWNER = FLAG_CARD_OTHER_OWNER
CARD_DATA_MISMATCH = FLAG_CARD_DATA_MISMATCH
CUSTOMER_TYPE_MISMATCH = FLAG_CUSTOMER_TYPE_MISMATCH
CUSTOMER_DOCUMENT_MISMATCH = FLAG_CUSTOMER_DOCUMENT_MISMATCH

OWNER_TYPE = "CLIENTE"
GESTOR_TYPE = "GESTOR"
LOSS_REASON = "PÉRDIDA"
ACTING_AS_GESTOR = "GESTOR"
_RELATION_FOR_ACTING_AS = {"PROPIETARIO": "DUEÑO", "POSEEDOR": "POSEEDOR"}
MIN_MONTHS_BETWEEN_PLATES = 6
MAX_CARDS = 30   # ownership cards per declaration; bounds what the anonymous public form can submit
BOGOTA_TZ = ZoneInfo("America/Bogota")


@dataclass
class PersonInput:
    name: str = ""
    document_type: str = ""
    document_number: str = ""
    address: str = ""
    neighborhood: str = ""
    municipality: str = ""
    phone: str = ""
    email: str = ""


@dataclass
class OwnershipCardInput:
    """One ownership card ("tarjeta de propiedad") of a declaration, with its
    own reason and plate quantity. A declaration carries 1..MAX_CARDS of these.

    `owner` / `owner_relation` are only read when the declarant acts as GESTOR
    (every card then has its own owner). When the declarant is the owner or
    possessor, every card belongs to the declarant and both are ignored."""

    plate_number: str = ""
    transit_license: str = ""
    engine_number: str = ""
    chassis_number: str = ""
    vehicle_type: str = ""
    registered_in: str = ""
    vehicle_brand: str = ""
    reason: str = ""                    # normalize.DECLARATION_REASONS, per card
    plate_quantity: int = 1             # 1..normalize.max_plates_per_card(vehicle_type)
    owner: PersonInput | None = None
    owner_relation: str = ""            # DUEÑO / POSEEDOR (GESTOR only)


@dataclass
class DeclarationInput:
    """What the public form submits. ONE declarant (the person who signs):
    the owner/possessor themself, or a gestor acting for several owners. Only
    the declarant signs, once -- the signature covers every ownership card listed."""

    acting_as: str = ""                 # PROPIETARIO / POSEEDOR / GESTOR
    declarant: PersonInput = field(default_factory=PersonInput)
    cards: list[OwnershipCardInput] = field(default_factory=list)
    signature_file: str | None = None   # stored file name of the declarant's signature PNG
    submitter_ip: str | None = None
    user_agent: str | None = None
    accepted_terms: bool = False
    submitted_data: dict[str, Any] = field(default_factory=dict)   # raw form copy


@dataclass
class SubmitResult:
    """`flags` is for staff/logging only -- never render it to the public."""

    declaration_id: int
    status: str
    flags: list[str]


# --- validation -------------------------------------------------------------

def _blank(value: str | None) -> bool:
    return not (value and value.strip())


def _validate_person(person: PersonInput, label: str) -> list[str]:
    errors: list[str] = []
    required = (
        (person.name, "el nombre"), (person.document_number, "el número de documento"),
        (person.address, "la dirección"), (person.neighborhood, "el barrio"),
        (person.municipality, "el municipio"), (person.phone, "el teléfono"),
    )
    for value, what in required:
        if _blank(value):
            errors.append(f"Falta {what} {label}.")
    # Same rule as /ingest's "Solicitado por": a name with digits looks like a
    # plate and staff could not create the order for this customer afterwards.
    if any(ch.isdigit() for ch in person.name):
        errors.append(f"El nombre {label} no puede contener números.")
    if _blank(person.document_type):
        errors.append(f"Falta el tipo de documento {label}.")
    elif normalize_choice(person.document_type, DOCUMENT_TYPES) is None:
        errors.append(f"El tipo de documento {label} no es válido.")
    return errors


def _license_key(value: str) -> str:
    """Same normalization ownership_cards applies to a transit licence."""
    return value.strip().upper()


def _validate_card(card: OwnershipCardInput, *, gestor: bool) -> list[str]:
    """Errors of one card, WITHOUT the "Tarjeta N: " prefix."""
    errors: list[str] = []
    if normalize_choice(card.reason, DECLARATION_REASONS) is None:
        errors.append("El motivo debe ser DETERIORO o PÉRDIDA.")
    if _blank(card.transit_license):
        errors.append("Falta la licencia de tránsito.")
    vehicle_type = normalize_choice(card.vehicle_type, PLATE_TYPES)
    if _blank(card.vehicle_type):
        errors.append("Falta el tipo de vehículo.")
    elif vehicle_type is None:
        errors.append("El tipo de vehículo no es válido.")
    if _blank(card.plate_number):
        errors.append("Falta la placa.")
    elif vehicle_type is not None and vehicle_type in PLATE_PATTERNS:
        try:
            validate_plate(card.plate_number, vehicle_type)
        except ValueError as exc:
            errors.append(str(exc))

    quantity = card.plate_quantity
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
        errors.append("La cantidad de placas debe ser al menos 1.")
    elif vehicle_type is not None and quantity > max_plates_per_card(vehicle_type):
        if max_plates_per_card(vehicle_type) == 2:
            errors.append("La cantidad de placas debe ser 1 o 2.")
        else:
            errors.append("La cantidad de placas para este tipo de vehículo debe ser 1.")

    if gestor:
        if card.owner is None:
            errors.append("Faltan los datos del propietario/poseedor.")
        else:
            errors += _validate_person(card.owner, "del propietario/poseedor")
        if normalize_choice(card.owner_relation, DOCUMENT_RELATIONS) not in ("DUEÑO", "POSEEDOR"):
            errors.append("Indique si el cliente es dueño o poseedor de la tarjeta de propiedad.")
    return errors


def validate_declaration(inp: DeclarationInput) -> list[str]:
    """Spanish error messages (empty list = valid). Messages about a specific
    ownership card are prefixed with its 1-based number ("Tarjeta 2: ...")."""
    errors: list[str] = []
    acting_as = normalize_choice(inp.acting_as, ACTING_AS)
    if acting_as is None:
        errors.append("Debe indicar si actúa como propietario, poseedor o gestor.")
    errors += _validate_person(inp.declarant, "del declarante")

    if not inp.cards:
        errors.append("Debe declarar al menos una tarjeta de propiedad.")
    elif len(inp.cards) > MAX_CARDS:
        errors.append(f"Solo se pueden declarar hasta {MAX_CARDS} tarjetas de propiedad por declaración.")
    else:
        seen_plates: dict[str, int] = {}
        seen_licenses: dict[str, int] = {}
        for number, card in enumerate(inp.cards, start=1):
            card_errors = _validate_card(card, gestor=acting_as == ACTING_AS_GESTOR)
            if not _blank(card.plate_number):
                key = clean_plate(card.plate_number)
                if key in seen_plates:
                    card_errors.append(
                        f"La placa {key} está repetida (ya aparece en la tarjeta {seen_plates[key]}).")
                else:
                    seen_plates[key] = number
            if not _blank(card.transit_license):
                key = _license_key(card.transit_license)
                if key in seen_licenses:
                    card_errors.append(
                        f"La licencia de tránsito {key} está repetida "
                        f"(ya aparece en la tarjeta {seen_licenses[key]}).")
                else:
                    seen_licenses[key] = number
            errors += [f"Tarjeta {number}: {message}" for message in card_errors]

    if _blank(inp.signature_file):
        errors.append("Falta la firma del declarante.")
    if not inp.accepted_terms:
        errors.append("Debe aceptar los términos de la declaración.")
    return errors


# --- helpers ----------------------------------------------------------------

def bogota_today() -> date:
    return datetime.now(BOGOTA_TZ).date()


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _person_dict(person: PersonInput) -> dict[str, Any]:
    return asdict(person)


def _person_from_data(data: dict[str, Any] | None) -> PersonInput:
    return PersonInput(**{k: str((data or {}).get(k) or "") for k in asdict(PersonInput())})


def _owner_and_relation(
    acting_as: str | None, declarant: PersonInput, card: OwnershipCardInput,
) -> tuple[PersonInput, str | None]:
    """The effective owner of a card and its DUEÑO/POSEEDOR relation: the
    declarant for PROPIETARIO/POSEEDOR, the card's own owner for GESTOR."""
    if acting_as == ACTING_AS_GESTOR:
        return card.owner or PersonInput(), normalize_choice(card.owner_relation, DOCUMENT_RELATIONS)
    return declarant, _RELATION_FOR_ACTING_AS.get(acting_as or "")


def _build_submitted_data(inp: DeclarationInput) -> dict[str, Any]:
    """Canonical JSON copy of what was declared (the legal evidence); the shape
    is documented in the module docstring. The raw form dict, if the caller
    supplied one, is kept under `raw`."""
    acting_as = normalize_choice(inp.acting_as, ACTING_AS)
    cards: list[dict[str, Any]] = []
    for card in inp.cards:
        owner, relation = _owner_and_relation(acting_as, inp.declarant, card)
        data = asdict(card)
        data["owner"] = _person_dict(owner)
        data["owner_relation"] = relation or ""
        cards.append(data)
    result: dict[str, Any] = {
        "acting_as": acting_as or inp.acting_as,
        "declarant": _person_dict(inp.declarant),
        "cards": cards,
    }
    if inp.submitted_data:
        result["raw"] = inp.submitted_data
    return result


def _create_customer(
    conn: psycopg.Connection[DictRow], person: PersonInput, customer_type: str,
    document_relation: str | None = None,
) -> int:
    return db.resolve_or_create_customer(
        conn, person.name, type=customer_type,
        document_type=normalize_choice(person.document_type, DOCUMENT_TYPES),
        document_number=_clean(person.document_number), document_relation=document_relation,
        phone=_clean(person.phone), email=_clean(person.email), address=_clean(person.address),
        neighborhood=_clean(person.neighborhood), municipality=_clean(person.municipality),
    )


def _type_mismatch(customer: db.CustomerRecord | None, expected: str) -> bool:
    return customer is not None and customer.type != expected


def _document_key(value: str | None) -> str:
    """Comparison key for document numbers: only letters/digits, uppercase
    ('1.234.567-8' and '12345678' compare equal)."""
    return re.sub(r"[^0-9A-Z]", "", (value or "").upper())


def _document_mismatch(customer: db.CustomerRecord | None, declared: PersonInput | None) -> bool:
    """True when an existing customer already has a document number and the
    declared one is a DIFFERENT number. Names are the customer identity in this
    app, so two different people with the same name must not be merged
    silently. A blank on either side is not a mismatch."""
    if customer is None or declared is None:
        return False
    existing_key = _document_key(customer.document_number)
    declared_key = _document_key(declared.document_number)
    return bool(existing_key and declared_key and existing_key != declared_key)


def _same_name_documents_differ(first: PersonInput, other: PersonInput) -> bool:
    first_key, other_key = _document_key(first.document_number), _document_key(other.document_number)
    return bool(first_key and other_key and first_key != other_key)


def _create_card(
    conn: psycopg.Connection[DictRow], owner_id: int,
    card: OwnershipCardInput | db.DeclarationCardRecord,
) -> int:
    return db.create_ownership_card(
        conn, owner_id, transit_license=card.transit_license, plate_number=card.plate_number,
        engine_number=card.engine_number, chassis_number=card.chassis_number,
        vehicle_type=normalize_choice(card.vehicle_type, PLATE_TYPES),
        registered_in=card.registered_in, vehicle_brand=card.vehicle_brand,
    )


def _union(flag_lists: list[list[str]]) -> list[str]:
    """Flags of all cards, deduplicated, in first-seen order."""
    seen: dict[str, None] = {}
    for flags in flag_lists:
        for flag in flags:
            seen.setdefault(flag)
    return list(seen)


@dataclass
class _CardPlan:
    """What submit_declaration decided about one card (before any write)."""

    position: int
    card: OwnershipCardInput
    owner: PersonInput
    relation: str | None
    flags: list[str]
    existing_owner: db.CustomerRecord | None
    lookup: db.CardLookup


def _plan_cards(
    conn: psycopg.Connection[DictRow], inp: DeclarationInput, today: date,
) -> tuple[list[_CardPlan], db.CustomerRecord | None]:
    """Evaluate every card (read-only) and return the plans plus the existing
    gestor customer, if the declarant is a gestor that already exists."""
    acting_as = normalize_choice(inp.acting_as, ACTING_AS)
    is_gestor = acting_as == ACTING_AS_GESTOR
    existing_gestor = db.get_customer_by_name(conn, inp.declarant.name) if is_gestor else None
    declarant_flags: list[str] = []
    if is_gestor:
        if _type_mismatch(existing_gestor, GESTOR_TYPE):
            declarant_flags.append(CUSTOMER_TYPE_MISMATCH)
        if _document_mismatch(existing_gestor, inp.declarant):
            declarant_flags.append(CUSTOMER_DOCUMENT_MISMATCH)

    # First declaration of each canonical name inside this submission, with the
    # customer type it needs: the same name cannot be a gestor AND an owner, and
    # one name cannot carry two different document numbers.
    first_seen: dict[str, tuple[str, PersonInput]] = {}
    if is_gestor:
        first_seen[normalize_requested_by(inp.declarant.name)] = (GESTOR_TYPE, inp.declarant)

    plans: list[_CardPlan] = []
    for position, card in enumerate(inp.cards):
        owner, relation = _owner_and_relation(acting_as, inp.declarant, card)
        flags: list[str] = []
        existing_owner = db.get_customer_by_name(conn, owner.name)

        last_made = db.last_fabrication_date(conn, card.plate_number)
        if last_made is not None and last_made + relativedelta(months=MIN_MONTHS_BETWEEN_PLATES) > today:
            flags.append(PLATE_MADE_RECENTLY)

        lookup = db.find_card_conflicts(
            conn, customer_id=existing_owner.id if existing_owner else None,
            transit_license=card.transit_license, plate_number=card.plate_number,
        )
        if lookup.status == db.CARD_STATUS_OTHER_OWNER:
            flags.append(CARD_OTHER_OWNER)
        elif lookup.status == db.CARD_STATUS_DATA_MISMATCH:
            flags.append(CARD_DATA_MISMATCH)

        in_submission_type = in_submission_document = False
        if is_gestor:   # (a declarant that owns the cards is one single person)
            key = normalize_requested_by(owner.name)
            if key in first_seen:
                first_type, first_person = first_seen[key]
                in_submission_type = first_type != OWNER_TYPE
                in_submission_document = _same_name_documents_differ(first_person, owner)
            else:
                first_seen[key] = (OWNER_TYPE, owner)
        if _type_mismatch(existing_owner, OWNER_TYPE) or in_submission_type:
            flags.append(CUSTOMER_TYPE_MISMATCH)
        if _document_mismatch(existing_owner, owner) or in_submission_document:
            flags.append(CUSTOMER_DOCUMENT_MISMATCH)
        flags += [f for f in declarant_flags if f not in flags]
        plans.append(_CardPlan(position, card, owner, relation, flags, existing_owner, lookup))
    return plans, existing_gestor


def _insert_card_row(
    conn: psycopg.Connection[DictRow], declaration_id: int, plan: _CardPlan, *,
    customer_id: int | None = None, ownership_card_id: int | None = None,
) -> int:
    card = plan.card
    return db.insert_declaration_card(
        conn, declaration_id, position=plan.position,
        reason=normalize_choice(card.reason, DECLARATION_REASONS) or card.reason,
        plate_quantity=card.plate_quantity,
        plate_number=clean_plate(card.plate_number),
        transit_license=_license_key(card.transit_license),
        engine_number=_clean(card.engine_number), chassis_number=_clean(card.chassis_number),
        vehicle_type=normalize_choice(card.vehicle_type, PLATE_TYPES) or _clean(card.vehicle_type),
        registered_in=_clean(card.registered_in), vehicle_brand=_clean(card.vehicle_brand),
        owner_relation=plan.relation, owner_data=_person_dict(plan.owner),
        review_flags=plan.flags, customer_id=customer_id, ownership_card_id=ownership_card_id,
    )


# --- submit -----------------------------------------------------------------

def submit_declaration(
    conn: psycopg.Connection[DictRow], inp: DeclarationInput, *, today: date | None = None,
) -> SubmitResult:
    """Store a declaration from the public form (see the module docstring for
    the rules). Assumes `validate_declaration(inp)` already passed.

    `today` defaults to the Bogotá calendar date; tests pass a fixed one.
    """
    today = today or bogota_today()
    acting_as = normalize_choice(inp.acting_as, ACTING_AS) or inp.acting_as
    plans, existing_gestor = _plan_cards(conn, inp, today)
    flags = _union([plan.flags for plan in plans])

    common: dict[str, Any] = {
        "acting_as": acting_as,
        "submitted_data": _build_submitted_data(inp),
        "declarant_signature_path": inp.signature_file,
        "submitter_ip": inp.submitter_ip,
        "user_agent": inp.user_agent,
        "accepted_terms_at": datetime.now() if inp.accepted_terms else None,
    }

    # Every declaration waits for the point of sale to review and approve it
    # (approve_declaration creates/links the customers and ownership cards).
    # `flags` only tell the reviewer what to look at; submitting creates nothing.
    declaration_id = db.insert_declaration(
        conn, status=STATUS_PENDING_REVIEW, review_flags=flags, **common,
    )
    for plan in plans:
        _insert_card_row(conn, declaration_id, plan)
    return SubmitResult(declaration_id, STATUS_PENDING_REVIEW, flags)


# --- staff review -----------------------------------------------------------

def _load_pending(conn: psycopg.Connection[DictRow], declaration_id: int) -> db.DeclarationRecord:
    declaration = db.get_declaration(conn, declaration_id)
    if declaration is None:
        raise ValueError("La declaración no existe.")
    if declaration.status != STATUS_PENDING_REVIEW:
        raise ValueError("Solo se puede revisar una declaración pendiente de revisión.")
    return declaration


def _resolve_for_approval(
    conn: psycopg.Connection[DictRow], person: PersonInput, expected_type: str,
    document_relation: str | None,
) -> int:
    """Existing customer: NULL type is set to `expected_type` (the admin's
    explicit approval); a DIFFERENT type is an error. Absent: created."""
    existing = db.get_customer_by_name(conn, person.name)
    if existing is None:
        return _create_customer(conn, person, expected_type, document_relation)
    if existing.type is None:
        db.update_customer(
            conn, existing.id, name=existing.name, type=expected_type,
            document_type=existing.document_type, document_number=existing.document_number,
            document_relation=existing.document_relation, phone=existing.phone,
            email=existing.email, address=existing.address,
            neighborhood=existing.neighborhood, municipality=existing.municipality,
        )
    return existing.id


def _card_label(card: db.DeclarationCardRecord) -> str:
    return f"tarjeta {card.position + 1} (placa {card.plate_number})"


def approve_declaration(
    conn: psycopg.Connection[DictRow], declaration_id: int, reviewer_user_id: int, *,
    reassign_card_ids: Collection[int] = (), note: str | None = None,
) -> None:
    """Approve a `pending_review` declaration: create/link the customers and
    the ownership cards of EVERY card from the stored declaration (same
    creation rules for every declaration, plus the explicit-admin cases below) and
    mark it approved.

    `reassign_card_ids` are `declaration_cards.id` values (NOT
    ownership_cards.id) whose existing ownership card belongs to another
    customer and may be moved to the declared owner.

    Raises ValueError (clear Spanish message naming the card by number and
    plate) when:
      * the declaration is not `pending_review`;
      * `reassign_card_ids` holds an id that is not a card of this declaration;
      * an existing customer has a different non-NULL type (fix it first), or
        the same name is declared both as gestor and as a card owner;
      * a card exists on another owner and its id is not in `reassign_card_ids`;
      * a card's licence and plate point at different cards / only one exists.
    ALL checks happen before the first write, so a refused approval changes
    nothing (the caller owns the transaction anyway).
    """
    declaration = _load_pending(conn, declaration_id)
    cards = db.list_declaration_cards(conn, declaration_id)
    if not cards:
        raise ValueError("La declaración no tiene tarjetas de propiedad.")
    unknown = set(reassign_card_ids) - {card.id for card in cards}
    if unknown:
        raise ValueError("Se pidió reasignar una tarjeta que no pertenece a esta declaración.")

    is_gestor = declaration.acting_as == ACTING_AS_GESTOR
    declarant = _person_from_data(declaration.submitted_data.get("declarant"))
    owners = [_person_from_data(card.owner_data) for card in cards]

    # --- checks (no writes) ---
    expected_types: dict[str, str] = {}
    people: list[tuple[PersonInput, str, str]] = []   # (person, expected type, where)
    if is_gestor:
        people.append((declarant, GESTOR_TYPE, "el gestor"))
    people += [(owner, OWNER_TYPE, _card_label(card)) for owner, card in zip(owners, cards, strict=True)]
    for person, expected, where in people:
        key = normalize_requested_by(person.name)
        if expected_types.setdefault(key, expected) != expected:
            raise ValueError(
                f"'{person.name}' aparece como gestor y como propietario/poseedor en la misma "
                f"declaración ({where}); no se puede aprobar."
            )
        existing = db.get_customer_by_name(conn, person.name)
        if existing is not None and existing.type not in (None, expected):
            raise ValueError(
                f"El cliente '{existing.name}' ya existe con tipo {existing.type}; "
                f"corrija su tipo a {expected} antes de aprobar ({where})."
            )
    lookups: list[db.CardLookup] = []
    for owner, card in zip(owners, cards, strict=True):
        existing_owner = db.get_customer_by_name(conn, owner.name)
        lookup = db.find_card_conflicts(
            conn, customer_id=existing_owner.id if existing_owner else None,
            transit_license=card.transit_license, plate_number=card.plate_number,
        )
        if lookup.status == db.CARD_STATUS_DATA_MISMATCH:
            raise ValueError(
                f"En la {_card_label(card)}, la licencia de tránsito y la placa no coinciden "
                "con la misma tarjeta existente."
            )
        if lookup.status == db.CARD_STATUS_OTHER_OWNER and card.id not in reassign_card_ids:
            raise ValueError(
                f"La {_card_label(card)} ya existe a nombre de otro cliente; "
                "apruebe con reasignación para moverla."
            )
        lookups.append(lookup)

    # --- writes ---
    gestor_id = _resolve_for_approval(conn, declarant, GESTOR_TYPE, None) if is_gestor else None
    for owner, card, lookup in zip(owners, cards, lookups, strict=True):
        owner_id = _resolve_for_approval(
            conn, owner, OWNER_TYPE, normalize_choice(card.owner_relation, DOCUMENT_RELATIONS),
        )
        if lookup.status == db.CARD_STATUS_NEW:
            card_id = _create_card(conn, owner_id, card)
        else:
            assert lookup.by_transit_license is not None
            card_id = lookup.by_transit_license.id
            if lookup.status == db.CARD_STATUS_OTHER_OWNER:
                db.reassign_ownership_card(conn, card_id, owner_id)
        db.set_declaration_card_links(conn, card.id, customer_id=owner_id, ownership_card_id=card_id)
    db.set_declaration_review(
        conn, declaration_id, status=STATUS_APPROVED, reviewed_by=reviewer_user_id, note=note,
        gestor_id=gestor_id,
    )


def reject_declaration(
    conn: psycopg.Connection[DictRow], declaration_id: int, reviewer_user_id: int, note: str,
) -> None:
    """Reject a `pending_review` declaration; creates nothing. A note is
    required (ValueError if blank)."""
    if _blank(note):
        raise ValueError("Escriba una nota explicando el rechazo.")
    _load_pending(conn, declaration_id)
    db.set_declaration_review(
        conn, declaration_id, status=STATUS_REJECTED, reviewed_by=reviewer_user_id, note=note.strip(),
    )
