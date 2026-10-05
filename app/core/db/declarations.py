"""declarations reads/writes.

A declaration is one sworn declaration ("declaración juramentada") submitted
through the public form. This module is only the data layer; the decision
rules (flags, auto-approval, review) live in app/core/declarations.py.

While a declaration is `pending_review`, customer_id / ownership_card_id /
gestor_id are NULL and `submitted_data` (JSONB) is the ONLY place the declared
data lives -- nothing was created in customers / ownership_cards.

See app/core/db/__init__.py for the module-wide connection/transaction
contract that applies to every function here.
"""

from __future__ import annotations

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

# declarations.review_flags values (computed by app/core/declarations.py).
FLAG_PLATE_MADE_RECENTLY = "plate_made_recently"
FLAG_CARD_OTHER_OWNER = "card_other_owner"
FLAG_CARD_DATA_MISMATCH = "card_data_mismatch"
FLAG_CUSTOMER_TYPE_MISMATCH = "customer_type_mismatch"


@dataclass
class DeclarationRecord:
    """One `declarations` row."""

    id: int
    created_at: datetime
    reason: str
    plate_quantity: int
    acting_as: str
    customer_id: int | None
    ownership_card_id: int | None
    gestor_id: int | None
    status: str
    review_flags: list[str]
    reviewed_by: int | None
    reviewed_at: datetime | None
    review_note: str | None
    submitted_data: dict[str, Any]
    owner_signature_path: str | None
    gestor_signature_path: str | None
    notarized_pdf_path: str | None
    submitter_ip: str | None
    user_agent: str | None
    accepted_terms_at: datetime | None


_SELECT_COLUMNS = (
    "id, created_at, reason, plate_quantity, acting_as, customer_id, "
    "ownership_card_id, gestor_id, status, review_flags, reviewed_by, "
    "reviewed_at, review_note, submitted_data, owner_signature_path, "
    "gestor_signature_path, notarized_pdf_path, submitter_ip, user_agent, "
    "accepted_terms_at"
)


def insert_declaration(
    conn: psycopg.Connection[DictRow], *,
    reason: str, plate_quantity: int, acting_as: str, status: str,
    submitted_data: dict[str, Any], review_flags: list[str] | None = None,
    customer_id: int | None = None, ownership_card_id: int | None = None,
    gestor_id: int | None = None,
    owner_signature_path: str | None = None, gestor_signature_path: str | None = None,
    notarized_pdf_path: str | None = None,
    submitter_ip: str | None = None, user_agent: str | None = None,
    accepted_terms_at: datetime | None = None,
) -> int:
    """Insert a declaration; return its id. No pre-checks (FK violations
    surface as psycopg errors, same convention as the other modules)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO declarations (
                reason, plate_quantity, acting_as, customer_id, ownership_card_id,
                gestor_id, status, review_flags, submitted_data,
                owner_signature_path, gestor_signature_path, notarized_pdf_path,
                submitter_ip, user_agent, accepted_terms_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (reason, plate_quantity, acting_as, customer_id, ownership_card_id,
             gestor_id, status, list(review_flags or []), Jsonb(submitted_data),
             owner_signature_path, gestor_signature_path, notarized_pdf_path,
             submitter_ip, user_agent, accepted_terms_at),
        )
        row = cur.fetchone()
        assert row is not None, "INSERT ... RETURNING id always yields a row"
        return row["id"]


def get_declaration(conn: psycopg.Connection[DictRow], declaration_id: int) -> DeclarationRecord | None:
    """Look up a declaration by id."""
    with conn.cursor(row_factory=class_row(DeclarationRecord)) as cur:
        cur.execute(f"SELECT {_SELECT_COLUMNS} FROM declarations WHERE id = %s", (declaration_id,))
        return cur.fetchone()


def list_declarations(
    conn: psycopg.Connection[DictRow], *, status: str | None = None, query: str | None = None,
) -> list[DeclarationRecord]:
    """Declarations, `pending_review` first and then newest first.

    `status` filters by exact status. `query` is a case-insensitive substring
    match against the owner / gestor name, document number, plate and transit
    licence declared inside `submitted_data`, plus the linked customer, gestor
    and ownership card rows (so a declaration is still found by the canonical
    customer name after approval).
    """
    where: list[str] = []
    params: list[Any] = []
    if status is not None:
        where.append("d.status = %s")
        params.append(status)
    if query and query.strip():
        pattern = f"%{query.strip()}%"
        where.append(
            """(
                d.submitted_data #>> '{owner,name}' ILIKE %s
                OR d.submitted_data #>> '{owner,document_number}' ILIKE %s
                OR d.submitted_data #>> '{gestor,name}' ILIKE %s
                OR d.submitted_data #>> '{gestor,document_number}' ILIKE %s
                OR d.submitted_data #>> '{vehicle,plate_number}' ILIKE %s
                OR d.submitted_data #>> '{vehicle,transit_license}' ILIKE %s
                OR c.name ILIKE %s OR c.document_number ILIKE %s
                OR g.name ILIKE %s OR g.document_number ILIKE %s
                OR oc.plate_number ILIKE %s OR oc.transit_license ILIKE %s
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
            LEFT JOIN customers c ON c.id = d.customer_id
            LEFT JOIN customers g ON g.id = d.gestor_id
            LEFT JOIN ownership_cards oc ON oc.id = d.ownership_card_id
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
    reviewed_by: int | None, note: str | None = None,
    customer_id: int | None = None, ownership_card_id: int | None = None,
    gestor_id: int | None = None,
) -> None:
    """Record a review decision: sets status, reviewed_by, reviewed_at=now()
    and review_note, and the three FKs (an approval fills them in; a
    rejection leaves them NULL). Does NOT check the previous status -- that
    rule lives in app/core/declarations.py."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE declarations SET
                status = %s, reviewed_by = %s, reviewed_at = now(), review_note = %s,
                customer_id = %s, ownership_card_id = %s, gestor_id = %s
            WHERE id = %s
            """,
            (status, reviewed_by, note, customer_id, ownership_card_id, gestor_id, declaration_id),
        )
