"""ownership_cards reads/writes.

An ownership card is the Colombian "tarjeta de propiedad", identified by its
"licencia de tránsito" (`transit_license`). One customer may own several
cards; each card belongs to exactly one customer. The table has three
INDEPENDENT constraints (FK to customers, UNIQUE transit_license, UNIQUE
plate_number) -- see docs/DATABASE.md.

Inputs are normalized here, inside the layer: transit_license is stripped +
uppercased, plate_number goes through app.core.normalize.clean_plate, and
free-text fields are stripped with blank -> NULL.

Constraint violations are NOT pre-checked: they surface as psycopg errors
(UniqueViolation / ForeignKeyViolation) -- "let the DB constraint be the
source of truth", same as create_user/update_customer. find_card_conflicts is
the read-only way to ask beforehand what a declared (customer, licence, plate)
triple would collide with.

See app/core/db/__init__.py for the module-wide connection/transaction
contract that applies to every function here.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
from psycopg.rows import DictRow, class_row

from app.core.normalize import clean_plate, is_real_plate

# Customer types whose plates NEVER get an ownership_card_id: EMPRESA and
# TRÁNSITO have no "tarjeta de propiedad" (their plates hang off the company
# only). CLIENTE, GESTOR (a gestor sends plates of its clients' cards) and
# NULL (185 historical customers not classified yet -- the lookup only matches
# a card with that exact plate, so trying is harmless) are eligible.
CARD_LINK_EXCLUDED_CUSTOMER_TYPES = ("EMPRESA", "TRÁNSITO")


@dataclass
class OwnershipCardRecord:
    """One `ownership_cards` row -- returned by lookups."""

    id: int
    customer_id: int
    transit_license: str
    plate_number: str
    engine_number: str | None
    chassis_number: str | None
    vehicle_type: str | None
    registered_in: str | None
    vehicle_brand: str | None


_SELECT_COLUMNS = (
    "id, customer_id, transit_license, plate_number, engine_number, "
    "chassis_number, vehicle_type, registered_in, vehicle_brand"
)

# find_card_conflicts statuses (see CardLookup).
CARD_STATUS_NEW = "new"
CARD_STATUS_SAME_CARD = "same_card"
CARD_STATUS_OTHER_OWNER = "other_owner"
CARD_STATUS_DATA_MISMATCH = "data_mismatch"


@dataclass
class CardLookup:
    """Result of find_card_conflicts: the existing card found by licence (or
    None), the one found by plate (or None) and the classified `status`
    (one of the CARD_STATUS_* constants)."""

    by_transit_license: OwnershipCardRecord | None
    by_plate_number: OwnershipCardRecord | None
    status: str


def _normalize_transit_license(value: str) -> str:
    return value.strip().upper()


def _clean_text(value: str | None) -> str | None:
    """Strip; blank -> None."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def create_ownership_card(
    conn: psycopg.Connection[DictRow], customer_id: int, *,
    transit_license: str, plate_number: str,
    engine_number: str | None = None, chassis_number: str | None = None,
    vehicle_type: str | None = None, registered_in: str | None = None,
    vehicle_brand: str | None = None,
) -> int:
    """Insert an ownership card owned by `customer_id`; return its id.

    No pre-checks: a duplicate transit_license or plate_number raises
    psycopg.errors.UniqueViolation and a nonexistent customer raises
    psycopg.errors.ForeignKeyViolation. `vehicle_type` is expected to be a
    normalize.PLATE_TYPES value, validated by the caller (no DB CHECK).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ownership_cards (
                customer_id, transit_license, plate_number, engine_number,
                chassis_number, vehicle_type, registered_in, vehicle_brand
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (customer_id, _normalize_transit_license(transit_license), clean_plate(plate_number),
             _clean_text(engine_number), _clean_text(chassis_number), _clean_text(vehicle_type),
             _clean_text(registered_in), _clean_text(vehicle_brand)),
        )
        row = cur.fetchone()
        assert row is not None, "INSERT ... RETURNING id always yields a row"
        return row["id"]


def get_ownership_card(conn: psycopg.Connection[DictRow], card_id: int) -> OwnershipCardRecord | None:
    """Look up a card by id."""
    with conn.cursor(row_factory=class_row(OwnershipCardRecord)) as cur:
        cur.execute(f"SELECT {_SELECT_COLUMNS} FROM ownership_cards WHERE id = %s", (card_id,))
        return cur.fetchone()


def get_ownership_card_by_transit_license(
    conn: psycopg.Connection[DictRow], transit_license: str,
) -> OwnershipCardRecord | None:
    """Look up a card by its transit licence (normalized: strip + uppercase)."""
    with conn.cursor(row_factory=class_row(OwnershipCardRecord)) as cur:
        cur.execute(
            f"SELECT {_SELECT_COLUMNS} FROM ownership_cards WHERE transit_license = %s",
            (_normalize_transit_license(transit_license),),
        )
        return cur.fetchone()


def get_ownership_card_by_plate_number(
    conn: psycopg.Connection[DictRow], plate_number: str,
) -> OwnershipCardRecord | None:
    """Look up a card by its plate (normalized with clean_plate)."""
    with conn.cursor(row_factory=class_row(OwnershipCardRecord)) as cur:
        cur.execute(
            f"SELECT {_SELECT_COLUMNS} FROM ownership_cards WHERE plate_number = %s",
            (clean_plate(plate_number),),
        )
        return cur.fetchone()


def list_ownership_cards_for_customer(
    conn: psycopg.Connection[DictRow], customer_id: int,
) -> list[OwnershipCardRecord]:
    """Every card owned by `customer_id`, ordered by id."""
    with conn.cursor(row_factory=class_row(OwnershipCardRecord)) as cur:
        cur.execute(
            f"SELECT {_SELECT_COLUMNS} FROM ownership_cards WHERE customer_id = %s ORDER BY id",
            (customer_id,),
        )
        return cur.fetchall()


def reassign_ownership_card(
    conn: psycopg.Connection[DictRow], card_id: int, new_customer_id: int,
) -> None:
    """Move a card to another customer -- the deliberate admin action used by
    the declaration review flow. A plain UPDATE: a nonexistent customer raises
    psycopg.errors.ForeignKeyViolation; an unknown card_id updates nothing."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ownership_cards SET customer_id = %s WHERE id = %s",
            (new_customer_id, card_id),
        )


def classify_card_lookup(
    customer_id: int | None,
    by_transit_license: OwnershipCardRecord | None,
    by_plate_number: OwnershipCardRecord | None,
) -> str:
    """Pure decision over the two lookups (see find_card_conflicts).

    Precedence:
      1. neither found                                   -> "new"
      2. "data_mismatch": the licence and the plate resolve to DIFFERENT
         cards, or only ONE of them exists (the declared licence exists with
         a different plate, or the declared plate with a different licence).
         Wins over "other_owner": a contradictory declaration is flagged as
         such even when the owner also differs.
      3. "other_owner": both lookups hit the SAME card, but its customer is
         not `customer_id` (or `customer_id` is None).
      4. "same_card": same card, owned by `customer_id`.
    """
    if by_transit_license is None and by_plate_number is None:
        return CARD_STATUS_NEW
    if by_transit_license is None or by_plate_number is None:
        return CARD_STATUS_DATA_MISMATCH
    if by_transit_license.id != by_plate_number.id:
        return CARD_STATUS_DATA_MISMATCH
    if customer_id is None or by_transit_license.customer_id != customer_id:
        return CARD_STATUS_OTHER_OWNER
    return CARD_STATUS_SAME_CARD


def find_card_conflicts(
    conn: psycopg.Connection[DictRow], *, customer_id: int | None,
    transit_license: str, plate_number: str,
) -> CardLookup:
    """Read-only: classify what already exists for a declared (customer,
    licence, plate) triple. Never writes. See classify_card_lookup for the
    status precedence. `customer_id` is None when the declaring customer
    doesn't exist yet (any existing card is then necessarily someone else's).
    """
    by_license = get_ownership_card_by_transit_license(conn, transit_license)
    by_plate = get_ownership_card_by_plate_number(conn, plate_number)
    return CardLookup(
        by_transit_license=by_license,
        by_plate_number=by_plate,
        status=classify_card_lookup(customer_id, by_license, by_plate),
    )


def resolve_card_id_for_plate(
    conn: psycopg.Connection[DictRow], plate_number: str | None, customer_type: str | None,
    *, plate_to: str | None = None, is_voided: bool = False,
    is_plate_annulled: bool = False, is_batch_replacement: bool = False,
) -> int | None:
    """The `ownership_cards.id` a fabricated plate should be linked to via
    `plates.ownership_card_id`, or None. Read-only.

    None (never linked) when ANY of: the order's customer is EMPRESA or
    TRÁNSITO (see CARD_LINK_EXCLUDED_CUSTOMER_TYPES -- no lookup is even
    attempted); the plate is a range (`plate_to`), a batch replacement,
    voided or annulled; `plate_number` is not a real plate number
    (normalize.is_real_plate); or no card has that plate. Otherwise the card
    whose `plate_number` equals clean_plate(plate_number) (UNIQUE, so no
    ambiguity), regardless of whose customer the card belongs to.
    """
    if customer_type in CARD_LINK_EXCLUDED_CUSTOMER_TYPES:
        return None
    if plate_to or is_voided or is_plate_annulled or is_batch_replacement:
        return None
    if not is_real_plate(plate_number):
        return None
    card = get_ownership_card_by_plate_number(conn, plate_number)
    return card.id if card else None
