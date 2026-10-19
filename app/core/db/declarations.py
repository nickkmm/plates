"""declarations / declaration_cards reads/writes.

A declaration is one sworn declaration ("declaración juramentada") submitted
through the public form: ONE declarant (the person who signs, once) and one or
more ownership cards ("tarjetas de propiedad"), each a `declaration_cards` row
with its own reason, plate quantity, owner and review flags. This module is
only the data layer; the decision rules (flags, auto-approval, review) live in
app/core/declarations.py.

While a declaration is `pending_review`, `declarations.gestor_id` and every
card's customer_id / ownership_card_id are NULL and `submitted_data` (JSONB)
is the ONLY complete copy of what was declared -- nothing was created in
customers / ownership_cards. `declarations.review_flags` is the union of the
cards' own `review_flags`.

See app/core/db/__init__.py for the module-wide connection/transaction
contract that applies to every function here.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import DictRow, class_row
from psycopg.types.json import Jsonb

# declarations.status values.
STATUS_PENDING_REVIEW = "pending_review"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUSES = (STATUS_PENDING_REVIEW, STATUS_APPROVED, STATUS_REJECTED)

# review_flags values (computed by app/core/declarations.py).
FLAG_PLATE_MADE_RECENTLY = "plate_made_recently"
FLAG_CARD_OTHER_OWNER = "card_other_owner"
FLAG_CARD_DATA_MISMATCH = "card_data_mismatch"
FLAG_CUSTOMER_TYPE_MISMATCH = "customer_type_mismatch"
FLAG_CUSTOMER_DOCUMENT_MISMATCH = "customer_document_mismatch"


@dataclass
class DeclarationRecord:
    """One `declarations` row (the per-card data is in DeclarationCardRecord)."""

    id: int
    created_at: datetime
    acting_as: str
    gestor_id: int | None
    status: str
    review_flags: list[str]
    reviewed_by: int | None
    reviewed_at: datetime | None
    review_note: str | None
    submitted_data: dict[str, Any]
    notarized_pdf_path: str | None
    submitter_ip: str | None
    user_agent: str | None
    accepted_terms_at: datetime | None
    declarant_signature_path: str | None
    production_order_id: int | None    # the order that attended it; NULL = not attended yet
    notarized_received_at: datetime | None   # staff got the notarized copy back; NULL = not yet
    notarized_received_by: int | None


@dataclass
class DeclarationCardRecord:
    """One `declaration_cards` row: an ownership card as declared."""

    id: int
    declaration_id: int
    position: int
    reason: str
    plate_quantity: int
    plate_number: str
    transit_license: str
    engine_number: str | None
    chassis_number: str | None
    vehicle_type: str | None
    registered_in: str | None
    vehicle_brand: str | None
    owner_relation: str | None
    owner_data: dict[str, Any]
    customer_id: int | None
    ownership_card_id: int | None
    review_flags: list[str]


_SELECT_COLUMNS = (
    "id, created_at, acting_as, gestor_id, status, review_flags, reviewed_by, "
    "reviewed_at, review_note, submitted_data, notarized_pdf_path, submitter_ip, "
    "user_agent, accepted_terms_at, declarant_signature_path, production_order_id, "
    "notarized_received_at, notarized_received_by"
)

_CARD_SELECT_COLUMNS = (
    "id, declaration_id, position, reason, plate_quantity, plate_number, "
    "transit_license, engine_number, chassis_number, vehicle_type, registered_in, "
    "vehicle_brand, owner_relation, owner_data, customer_id, ownership_card_id, "
    "review_flags"
)


def insert_declaration(
    conn: psycopg.Connection[DictRow], *,
    acting_as: str, status: str, submitted_data: dict[str, Any],
    review_flags: list[str] | None = None, gestor_id: int | None = None,
    declarant_signature_path: str | None = None, notarized_pdf_path: str | None = None,
    submitter_ip: str | None = None, user_agent: str | None = None,
    accepted_terms_at: datetime | None = None,
) -> int:
    """Insert a declaration header; return its id. Its cards go in with
    insert_declaration_card. No pre-checks (FK violations surface as psycopg
    errors, same convention as the other modules)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO declarations (
                acting_as, gestor_id, status, review_flags, submitted_data,
                declarant_signature_path, notarized_pdf_path,
                submitter_ip, user_agent, accepted_terms_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (acting_as, gestor_id, status, list(review_flags or []), Jsonb(submitted_data),
             declarant_signature_path, notarized_pdf_path,
             submitter_ip, user_agent, accepted_terms_at),
        )
        row = cur.fetchone()
        assert row is not None, "INSERT ... RETURNING id always yields a row"
        return row["id"]


def insert_declaration_card(
    conn: psycopg.Connection[DictRow], declaration_id: int, *,
    position: int, reason: str, plate_quantity: int, plate_number: str, transit_license: str,
    owner_data: dict[str, Any], engine_number: str | None = None,
    chassis_number: str | None = None, vehicle_type: str | None = None,
    registered_in: str | None = None, vehicle_brand: str | None = None,
    owner_relation: str | None = None, review_flags: list[str] | None = None,
    customer_id: int | None = None, ownership_card_id: int | None = None,
) -> int:
    """Insert one card of a declaration; return its id. `position` is the
    0-based order inside the declaration (UNIQUE per declaration). While the
    declaration is pending, customer_id / ownership_card_id stay NULL."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO declaration_cards (
                declaration_id, position, reason, plate_quantity, plate_number,
                transit_license, engine_number, chassis_number, vehicle_type,
                registered_in, vehicle_brand, owner_relation, owner_data,
                customer_id, ownership_card_id, review_flags
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (declaration_id, position, reason, plate_quantity, plate_number,
             transit_license, engine_number, chassis_number, vehicle_type,
             registered_in, vehicle_brand, owner_relation, Jsonb(owner_data),
             customer_id, ownership_card_id, list(review_flags or [])),
        )
        row = cur.fetchone()
        assert row is not None, "INSERT ... RETURNING id always yields a row"
        return row["id"]


def get_declaration(conn: psycopg.Connection[DictRow], declaration_id: int) -> DeclarationRecord | None:
    """Look up a declaration by id."""
    with conn.cursor(row_factory=class_row(DeclarationRecord)) as cur:
        cur.execute(f"SELECT {_SELECT_COLUMNS} FROM declarations WHERE id = %s", (declaration_id,))
        return cur.fetchone()


def list_declaration_cards(
    conn: psycopg.Connection[DictRow], declaration_id: int,
) -> list[DeclarationCardRecord]:
    """The cards of one declaration, in their declared order (`position`)."""
    with conn.cursor(row_factory=class_row(DeclarationCardRecord)) as cur:
        cur.execute(
            f"SELECT {_CARD_SELECT_COLUMNS} FROM declaration_cards "
            "WHERE declaration_id = %s ORDER BY position",
            (declaration_id,),
        )
        return cur.fetchall()


def list_cards_for_declarations(
    conn: psycopg.Connection[DictRow], declaration_ids: Collection[int],
) -> dict[int, list[DeclarationCardRecord]]:
    """The cards of many declarations in ONE query (for list screens), keyed by
    declaration id, each list in `position` order. Every requested id is a
    key, with [] when it has no cards."""
    grouped: dict[int, list[DeclarationCardRecord]] = {i: [] for i in declaration_ids}
    if not grouped:
        return grouped
    with conn.cursor(row_factory=class_row(DeclarationCardRecord)) as cur:
        cur.execute(
            f"SELECT {_CARD_SELECT_COLUMNS} FROM declaration_cards "
            "WHERE declaration_id = ANY(%s) ORDER BY declaration_id, position",
            (list(grouped),),
        )
        for card in cur.fetchall():
            grouped[card.declaration_id].append(card)
    return grouped


def list_declarations(
    conn: psycopg.Connection[DictRow], *, status: str | None = None, query: str | None = None,
    attended: bool | None = None,
) -> list[DeclarationRecord]:
    """Declarations, `pending_review` first and then newest first.

    `status` filters by exact status. `attended` filters by whether an order
    already attended the declaration (`production_order_id`): None = no filter,
    True = IS NOT NULL, False = IS NULL (it does not look at the status, so
    False also includes pending/rejected ones; combine it with `status`). `query` is a case-insensitive substring
    match against the declarant's name / document number (declared inside
    `submitted_data`, plus the linked gestor customer) and, on ANY of the
    declaration's cards, the plate, transit licence, declared owner name /
    document number and the linked owner customer / ownership card rows (so a
    declaration is still found by the canonical customer name after
    approval). The user's own `%`, `_` and `\\` are escaped, so they match
    literally instead of acting as LIKE wildcards.
    """
    where: list[str] = []
    params: list[Any] = []
    if status is not None:
        where.append("d.status = %s")
        params.append(status)
    if attended is not None:
        where.append("d.production_order_id IS NOT NULL" if attended else "d.production_order_id IS NULL")
    if query and query.strip():
        escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        where.append(
            """(
                d.submitted_data #>> '{declarant,name}' ILIKE %s
                OR d.submitted_data #>> '{declarant,document_number}' ILIKE %s
                OR g.name ILIKE %s OR g.document_number ILIKE %s
                OR EXISTS (
                    SELECT 1
                    FROM declaration_cards dc
                    LEFT JOIN customers cc ON cc.id = dc.customer_id
                    LEFT JOIN ownership_cards oc ON oc.id = dc.ownership_card_id
                    WHERE dc.declaration_id = d.id AND (
                        dc.plate_number ILIKE %s OR dc.transit_license ILIKE %s
                        OR dc.owner_data ->> 'name' ILIKE %s
                        OR dc.owner_data ->> 'document_number' ILIKE %s
                        OR cc.name ILIKE %s OR cc.document_number ILIKE %s
                        OR oc.plate_number ILIKE %s OR oc.transit_license ILIKE %s
                    )
                )
            )"""
        )
        params.extend([pattern] * 12)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    columns = ", ".join(f"d.{c.strip()}" for c in _SELECT_COLUMNS.split(","))
    with conn.cursor(row_factory=class_row(DeclarationRecord)) as cur:
        cur.execute(
            f"""
            SELECT {columns}
            FROM declarations d
            LEFT JOIN customers g ON g.id = d.gestor_id
            {where_sql}
            ORDER BY (d.status = %s) DESC, d.created_at DESC, d.id DESC
            """,
            (*params, STATUS_PENDING_REVIEW),
        )
        return cur.fetchall()


def count_recent_by_ip(conn: psycopg.Connection[DictRow], ip: str, *, since: datetime) -> int:
    """How many declarations came from `ip` at or after `since` -- the data
    behind the public form's per-IP rate limit."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM declarations WHERE submitter_ip = %s AND created_at >= %s",
            (ip, since),
        )
        row = cur.fetchone()
        assert row is not None
        return int(row["n"])


def set_declaration_review(
    conn: psycopg.Connection[DictRow], declaration_id: int, *, status: str,
    reviewed_by: int | None, note: str | None = None, gestor_id: int | None = None,
) -> None:
    """Record a review decision: sets status, reviewed_by, reviewed_at=now(),
    review_note and gestor_id (an approval of a GESTOR declaration fills it in;
    a rejection leaves it NULL). The cards' own links are set with
    set_declaration_card_links. Does NOT check the previous status -- that rule
    lives in app/core/declarations.py."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE declarations SET
                status = %s, reviewed_by = %s, reviewed_at = now(), review_note = %s,
                gestor_id = %s
            WHERE id = %s
            """,
            (status, reviewed_by, note, gestor_id, declaration_id),
        )


def set_declaration_card_links(
    conn: psycopg.Connection[DictRow], declaration_card_id: int, *,
    customer_id: int | None, ownership_card_id: int | None,
) -> None:
    """Point one declaration card at the customer (owner) and the ownership
    card that were created or linked for it on approval."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE declaration_cards SET customer_id = %s, ownership_card_id = %s WHERE id = %s",
            (customer_id, ownership_card_id, declaration_card_id),
        )


def set_declaration_production_order(
    conn: psycopg.Connection[DictRow], declaration_id: int, production_order_id: int | None,
) -> None:
    """Mark a declaration as attended by an order (or clear it with None).
    Does not check the declaration's status -- the caller (the /ingest save)
    only passes approved declarations."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE declarations SET production_order_id = %s WHERE id = %s",
            (production_order_id, declaration_id),
        )


def set_declaration_notarized_received(
    conn: psycopg.Connection[DictRow], declaration_id: int, *, received_by: int | None,
    received: bool = True,
) -> None:
    """Record (received=True, stamped now()) or clear (received=False) that the
    notarized copy of a PÉRDIDA declaration came back to the counter. Does not
    check the status or the reasons -- the web layer decides when it applies."""
    with conn.cursor() as cur:
        if received:
            cur.execute(
                "UPDATE declarations SET notarized_received_at = now(), notarized_received_by = %s "
                "WHERE id = %s",
                (received_by, declaration_id),
            )
        else:
            cur.execute(
                "UPDATE declarations SET notarized_received_at = NULL, notarized_received_by = NULL "
                "WHERE id = %s",
                (declaration_id,),
            )
