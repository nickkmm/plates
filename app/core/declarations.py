"""Sworn-declaration business logic (public form + staff review). No Flask.

The data layer is app/core/db/declarations.py; this module decides what a
submitted declaration does. Takes an open `conn` and never commits/rolls back
(same contract as app/core/db/__init__.py).

Decision rules for `submit_declaration` (all decided by the owner of the
project, see the declarations plan):

* Any review flag  -> stored `pending_review`; NOTHING else is created (no
  customer, no gestor, no ownership card) and the three FKs stay NULL.
  `submitted_data` is then the only copy of what was declared.
* No flag -> stored `approved`; the CLIENTE (and the GESTOR, if any) are
  created only if absent -- existing ones are linked and NEVER modified --
  and the ownership card is created (`new`) or linked untouched (`same_card`).

Flags: PLATE_MADE_RECENTLY (made < 6 calendar months ago), CARD_OTHER_OWNER,
CARD_DATA_MISMATCH (licence/plate that do not match each other) and
CUSTOMER_TYPE_MISMATCH (an existing customer found by name is not of the
expected type; NULL counts as a mismatch) and CUSTOMER_DOCUMENT_MISMATCH (an
existing customer found by name already has a document number and it differs
from the declared one -- a homonym, not the same person).

**Privacy**: `SubmitResult.flags` is for staff/logging ONLY. The web layer
must never show the flags (or anything about the previous owner / fabrication
date) to the anonymous submitter -- only "Recibimos tu declaración".
"""

from __future__ import annotations

import re
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
    normalize_choice,
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
MAX_VEHICLES = 30   # per declaration; bounds what the anonymous public form can submit
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
class VehicleInput:
    """One vehicle of a declaration (one ownership card, one reason, one
    plate quantity). A declaration carries 1..MAX_VEHICLES of these.

    `owner` / `owner_relation` are only read when the declarant acts as GESTOR
    (every vehicle then has its own owner). When the declarant is the owner or
    possessor, every vehicle belongs to the declarant and both are ignored."""

    plate_number: str = ""
    transit_license: str = ""
    engine_number: str = ""
    chassis_number: str = ""
    vehicle_type: str = ""
    registered_in: str = ""
    vehicle_brand: str = ""
    reason: str = ""                    # normalize.DECLARATION_REASONS, per vehicle
    plate_quantity: int = 1             # 1..normalize.max_plates_per_vehicle(vehicle_type)
    owner: PersonInput | None = None
    owner_relation: str = ""            # DUEÑO / POSEEDOR (GESTOR only)


@dataclass
class DeclarationInput:
    """What the public form submits. ONE declarant (the person who signs):
    the owner/possessor themself, or a gestor acting for several owners. Only
    the declarant signs, once -- the signature covers every vehicle listed."""

    acting_as: str = ""                 # PROPIETARIO / POSEEDOR / GESTOR
    declarant: PersonInput = field(default_factory=PersonInput)
    vehicles: list[VehicleInput] = field(default_factory=list)
    signature_file: str | None = None   # stored file name of the declarant's signature PNG
    notarized_pdf_file: str | None = None   # required when ANY vehicle's reason is PÉRDIDA
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
    if _blank(person.document_type):
        errors.append(f"Falta el tipo de documento {label}.")
    elif normalize_choice(person.document_type, DOCUMENT_TYPES) is None:
        errors.append(f"El tipo de documento {label} no es válido.")
    return errors


def validate_declaration(inp: DeclarationInput) -> list[str]:
    """Spanish error messages (empty list = valid)."""
    errors: list[str] = []
    if normalize_choice(inp.reason, DECLARATION_REASONS) is None:
        errors.append("El motivo debe ser DETERIORO o PÉRDIDA.")
    if not isinstance(inp.plate_quantity, int) or inp.plate_quantity < 1:
        errors.append("La cantidad de placas debe ser al menos 1.")
    acting_as = normalize_choice(inp.acting_as, ACTING_AS)
    if acting_as is None:
        errors.append("Debe indicar si actúa como propietario, poseedor o gestor.")
    elif acting_as == ACTING_AS_GESTOR and normalize_choice(
        inp.owner_relation, DOCUMENT_RELATIONS,
    ) not in ("DUEÑO", "POSEEDOR"):
        errors.append("Indique si el cliente es dueño o poseedor del vehículo.")

    errors += _validate_person(inp.owner, "del propietario/poseedor")
    if acting_as == ACTING_AS_GESTOR:
        if inp.gestor is None:
            errors.append("Faltan los datos del gestor.")
        else:
            errors += _validate_person(inp.gestor, "del gestor")
        if _blank(inp.gestor_signature_file):
            errors.append("Falta la firma del gestor.")
    if _blank(inp.owner_signature_file):
        errors.append("Falta la firma del propietario/poseedor.")

    vehicle = inp.vehicle
    if _blank(vehicle.transit_license):
        errors.append("Falta la licencia de tránsito.")
    vehicle_type = normalize_choice(vehicle.vehicle_type, PLATE_TYPES)
    if _blank(vehicle.vehicle_type):
        errors.append("Falta el tipo de vehículo.")
    elif vehicle_type is None:
        errors.append("El tipo de vehículo no es válido.")
    if _blank(vehicle.plate_number):
        errors.append("Falta la placa.")
    elif vehicle_type is not None and vehicle_type in PLATE_PATTERNS:
        try:
            validate_plate(vehicle.plate_number, vehicle_type)
        except ValueError as exc:
            errors.append(str(exc))

    if normalize_choice(inp.reason, DECLARATION_REASONS) == LOSS_REASON and _blank(inp.notarized_pdf_file):
        errors.append("Para pérdida debe adjuntar el PDF de la declaración notariada.")
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


def _build_submitted_data(inp: DeclarationInput) -> dict[str, Any]:
    """Canonical JSON copy of what was declared (the legal evidence). The raw
    form dict, if the caller supplied one, is kept under `raw`."""
    data: dict[str, Any] = {
        "reason": inp.reason,
        "plate_quantity": inp.plate_quantity,
        "acting_as": inp.acting_as,
        "owner": _person_dict(inp.owner),
        "owner_relation": inp.owner_relation,
        "gestor": _person_dict(inp.gestor) if inp.gestor is not None else None,
        "vehicle": asdict(inp.vehicle),
    }
    if inp.submitted_data:
        data["raw"] = inp.submitted_data
    return data


def _person_from_data(data: dict[str, Any] | None) -> PersonInput | None:
    if not data:
        return None
    return PersonInput(**{k: str(data.get(k) or "") for k in asdict(PersonInput())})


def _vehicle_from_data(data: dict[str, Any]) -> VehicleInput:
    return VehicleInput(**{k: str(data.get(k) or "") for k in asdict(VehicleInput())})


def _owner_document_relation(acting_as: str, owner_relation: str) -> str | None:
    canonical = normalize_choice(acting_as, ACTING_AS)
    if canonical == ACTING_AS_GESTOR:
        return normalize_choice(owner_relation, DOCUMENT_RELATIONS)
    return _RELATION_FOR_ACTING_AS.get(canonical or "")


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


def _compute_flags(
    conn: psycopg.Connection[DictRow], *, owner: PersonInput, gestor: PersonInput | None,
    vehicle: VehicleInput, today: date,
) -> tuple[list[str], db.CustomerRecord | None, db.CustomerRecord | None, db.CardLookup]:
    flags: list[str] = []
    existing_owner = db.get_customer_by_name(conn, owner.name)
    existing_gestor = db.get_customer_by_name(conn, gestor.name) if gestor is not None else None

    last_made = db.last_fabrication_date(conn, vehicle.plate_number)
    if last_made is not None and last_made + relativedelta(months=MIN_MONTHS_BETWEEN_PLATES) > today:
        flags.append(PLATE_MADE_RECENTLY)

    lookup = db.find_card_conflicts(
        conn, customer_id=existing_owner.id if existing_owner else None,
        transit_license=vehicle.transit_license, plate_number=vehicle.plate_number,
    )
    if lookup.status == db.CARD_STATUS_OTHER_OWNER:
        flags.append(CARD_OTHER_OWNER)
    elif lookup.status == db.CARD_STATUS_DATA_MISMATCH:
        flags.append(CARD_DATA_MISMATCH)

    if _type_mismatch(existing_owner, OWNER_TYPE) or _type_mismatch(existing_gestor, GESTOR_TYPE):
        flags.append(CUSTOMER_TYPE_MISMATCH)
    if _document_mismatch(existing_owner, owner) or _document_mismatch(existing_gestor, gestor):
        flags.append(CUSTOMER_DOCUMENT_MISMATCH)
    return flags, existing_owner, existing_gestor, lookup


def _create_card(conn: psycopg.Connection[DictRow], owner_id: int, vehicle: VehicleInput) -> int:
    return db.create_ownership_card(
        conn, owner_id, transit_license=vehicle.transit_license, plate_number=vehicle.plate_number,
        engine_number=vehicle.engine_number, chassis_number=vehicle.chassis_number,
        vehicle_type=normalize_choice(vehicle.vehicle_type, PLATE_TYPES),
        registered_in=vehicle.registered_in, vehicle_brand=vehicle.vehicle_brand,
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
    owner = inp.owner
    gestor = inp.gestor if normalize_choice(inp.acting_as, ACTING_AS) == ACTING_AS_GESTOR else None
    flags, existing_owner, existing_gestor, lookup = _compute_flags(
        conn, owner=owner, gestor=gestor, vehicle=inp.vehicle, today=today,
    )

    common: dict[str, Any] = {
        "reason": normalize_choice(inp.reason, DECLARATION_REASONS) or inp.reason,
        "plate_quantity": inp.plate_quantity,
        "acting_as": normalize_choice(inp.acting_as, ACTING_AS) or inp.acting_as,
        "submitted_data": _build_submitted_data(inp),
        "owner_signature_path": inp.owner_signature_file,
        "gestor_signature_path": inp.gestor_signature_file,
        "notarized_pdf_path": inp.notarized_pdf_file,
        "submitter_ip": inp.submitter_ip,
        "user_agent": inp.user_agent,
        "accepted_terms_at": datetime.now() if inp.accepted_terms else None,
    }

    if flags:
        declaration_id = db.insert_declaration(
            conn, status=STATUS_PENDING_REVIEW, review_flags=flags, **common,
        )
        return SubmitResult(declaration_id, STATUS_PENDING_REVIEW, flags)

    relation = _owner_document_relation(inp.acting_as, inp.owner_relation)
    owner_id = existing_owner.id if existing_owner else _create_customer(conn, owner, OWNER_TYPE, relation)
    gestor_id: int | None = None
    if gestor is not None:
        gestor_id = existing_gestor.id if existing_gestor else _create_customer(conn, gestor, GESTOR_TYPE)
    if lookup.status == db.CARD_STATUS_SAME_CARD:
        assert lookup.by_transit_license is not None
        card_id = lookup.by_transit_license.id          # linked, never modified
    else:
        card_id = _create_card(conn, owner_id, inp.vehicle)
    declaration_id = db.insert_declaration(
        conn, status=STATUS_APPROVED, review_flags=[], customer_id=owner_id,
        ownership_card_id=card_id, gestor_id=gestor_id, **common,
    )
    return SubmitResult(declaration_id, STATUS_APPROVED, [])


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


def approve_declaration(
    conn: psycopg.Connection[DictRow], declaration_id: int, reviewer_user_id: int, *,
    reassign_card: bool = False, note: str | None = None,
) -> None:
    """Approve a `pending_review` declaration: create/link the customers and
    the card from `submitted_data` (same creation rules as auto-approval,
    plus the explicit-admin cases below) and mark it approved.

    Raises ValueError (clear Spanish message) when:
      * the declaration is not `pending_review`;
      * an existing customer has a different non-NULL type (fix it first);
      * the card exists on another owner and `reassign_card` is False;
      * the licence and the plate point at different cards / only one exists.
    Checks happen before any write; the caller owns the transaction anyway.
    """
    declaration = _load_pending(conn, declaration_id)
    data = declaration.submitted_data
    owner = _person_from_data(data.get("owner"))
    assert owner is not None, "submitted_data always carries the owner"
    gestor = _person_from_data(data.get("gestor"))
    vehicle = _vehicle_from_data(data.get("vehicle") or {})
    relation = _owner_document_relation(declaration.acting_as, str(data.get("owner_relation") or ""))

    existing_owner = db.get_customer_by_name(conn, owner.name)
    existing_gestor = db.get_customer_by_name(conn, gestor.name) if gestor else None
    for existing, expected in ((existing_owner, OWNER_TYPE), (existing_gestor, GESTOR_TYPE)):
        if existing is not None and existing.type not in (None, expected):
            raise ValueError(
                f"El cliente '{existing.name}' ya existe con tipo {existing.type}; "
                f"corrija su tipo a {expected} antes de aprobar."
            )
    lookup = db.find_card_conflicts(
        conn, customer_id=existing_owner.id if existing_owner else None,
        transit_license=vehicle.transit_license, plate_number=vehicle.plate_number,
    )
    if lookup.status == db.CARD_STATUS_DATA_MISMATCH:
        raise ValueError(
            "La licencia de tránsito y la placa no coinciden con la misma tarjeta existente."
        )
    if lookup.status == db.CARD_STATUS_OTHER_OWNER and not reassign_card:
        raise ValueError(
            "La tarjeta de propiedad ya existe a nombre de otro cliente; "
            "apruebe con reasignación para moverla."
        )

    owner_id = _resolve_for_approval(conn, owner, OWNER_TYPE, relation)
    gestor_id = _resolve_for_approval(conn, gestor, GESTOR_TYPE, None) if gestor else None
    if lookup.status == db.CARD_STATUS_NEW:
        card_id = _create_card(conn, owner_id, vehicle)
    else:
        assert lookup.by_transit_license is not None
        card_id = lookup.by_transit_license.id
        if lookup.status == db.CARD_STATUS_OTHER_OWNER:
            db.reassign_ownership_card(conn, card_id, owner_id)
    db.set_declaration_review(
        conn, declaration_id, status=STATUS_APPROVED, reviewed_by=reviewer_user_id, note=note,
        customer_id=owner_id, ownership_card_id=card_id, gestor_id=gestor_id,
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
