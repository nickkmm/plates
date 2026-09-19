"""plates reads/writes.

`distinct_cities` lives here (not in inventory.py) even though it's a
"distinct values for a dropdown" query in the same spirit as
orders.distinct_requested_by -- it queries `plates`, not
`inventory_categories`/`blank_preparations`, so it belongs with the rest of
this table's queries. Included here on purpose, by symmetry with
orders.distinct_requested_by.

See app/core/db/__init__.py for the module-wide connection/transaction
contract that applies to every function here.
"""

from __future__ import annotations

from collections.abc import Iterable

import psycopg
from psycopg.rows import DictRow

from app.core.models import ExtractedRow
from app.core.normalize import clean_plate


def existing_plate_numbers(conn: psycopg.Connection[DictRow], candidates: Iterable[str]) -> set[str]:
    """Return the subset of `candidates` already present as live (non-voided) plates."""
    values = list({c for c in candidates if c})
    if not values:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT plate_number FROM plates
            WHERE plate_number = ANY(%s)
              AND plate_number IS NOT NULL
              AND is_voided = false
            """,
            (values,),
        )
        return {r["plate_number"] for r in cur.fetchall()}


def find_orders_for_plate(conn: psycopg.Connection[DictRow], plate_number: str) -> list[dict]:
    """Existing live (non-voided) orders that already used this plate_number.

    Used by the /ingest form to show the data-entry person exactly where a
    plate they're about to save already appears, before it's saved again.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.id AS order_id, o.order_number, o.order_date, o.requested_by,
                   p.plate_type, p.quantity, p.city
            FROM plates p
            JOIN production_orders o ON o.id = p.order_id
            WHERE p.plate_number = %s AND p.is_voided = false
            ORDER BY o.order_date
            """,
            (plate_number,),
        )
        return cur.fetchall()


def insert_plate(conn: psycopg.Connection[DictRow], *, order_id: int, row: ExtractedRow,
                 is_possible_duplicate: bool) -> None:
    """Insert one plates row linked to order_id."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO plates (
                order_id, plate_type, plate_number, plate_to, is_batch_replacement,
                is_voided, is_plate_annulled, quantity, material, prepared_by,
                is_possible_duplicate, city
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                order_id,
                row.plate_type,
                row.plate_number,
                row.plate_to,
                row.is_batch_replacement,
                row.is_voided,
                row.is_plate_annulled,
                row.quantity,
                row.material,
                row.prepared_by,
                is_possible_duplicate,
                row.city,
            ),
        )


def search_plates_by_number(conn: psycopg.Connection[DictRow], query: str, limit: int = 200) -> list[dict]:
    """Find plates anywhere in the DB by (partial) plate_number, not just the
    recent-orders page -- lets the person check whether a plate was ever
    made before without knowing which order to look under.

    Matches against both plate_number and plate_to (a range's end), cleaned
    the same way stored plate numbers are so 'hmf 922' finds 'HMF922'.
    Partial match (ILIKE %query%) since the person may not remember it in
    full. Most recent order first.
    """
    needle = f"%{clean_plate(query)}%"
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.id, p.plate_number, p.plate_to, p.plate_type, p.city, p.quantity,
                   p.is_voided, p.is_batch_replacement, p.is_plate_annulled,
                   o.id AS order_id, o.order_number, o.order_date, o.requested_by
            FROM plates p
            JOIN production_orders o ON o.id = p.order_id
            WHERE p.plate_number ILIKE %s OR p.plate_to ILIKE %s
            ORDER BY o.order_date DESC, p.id DESC
            LIMIT %s
            """,
            (needle, needle, limit),
        )
        return cur.fetchall()


def distinct_cities(conn: psycopg.Connection[DictRow]) -> list[str]:
    """Existing cities, most frequent first; drops obvious junk/annulment markers."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT city FROM plates
            WHERE city IS NOT NULL AND city <> ''
            GROUP BY city ORDER BY count(*) DESC, city
            """
        )
        return [c for c in (r["city"] for r in cur.fetchall())
                if any(ch.isalnum() for ch in c) and "ANULAD" not in c.upper()]


def review_rows(conn: psycopg.Connection[DictRow]) -> list[dict]:
    """Rows flagged for review: possible duplicates only (real repeated plates)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.id, o.order_number, o.order_date, o.requested_by,
                   p.plate_type, p.plate_number, p.quantity, p.is_voided, p.city
            FROM plates p
            JOIN production_orders o ON o.id = p.order_id
            WHERE p.is_possible_duplicate = true
            ORDER BY p.plate_number, o.order_date
            """
        )
        return cur.fetchall()


def update_plate(conn: psycopg.Connection[DictRow], plate_id: int, *, plate_type: str,
                 plate_number: str | None, quantity: int, is_voided: bool) -> None:
    """Edit a plate row and clear its review flag (treated as reviewed)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE plates
            SET plate_type = %s, plate_number = %s, quantity = %s,
                is_voided = %s, is_possible_duplicate = false
            WHERE id = %s
            """,
            (plate_type, plate_number, quantity, is_voided, plate_id),
        )


def update_plate_full(conn: psycopg.Connection[DictRow], plate_id: int, *, plate_type: str,
                      plate_number: str | None, plate_to: str | None, city: str | None,
                      quantity: int, is_voided: bool, is_plate_annulled: bool,
                      is_batch_replacement: bool) -> None:
    """Edit every plate-level field from the Registro detail page.

    Wider than update_plate (which only covers the flagged-duplicate review
    form's fields): this also lets a range end, city, and the "placa anulada"
    waste flag be corrected after the order was already saved. Clears
    is_possible_duplicate too, same "editing counts as reviewed" convention.

    is_batch_replacement is a required param (not left untouched) so that
    editing a reposición row's plate_number in place to a real plate number
    -- which normalize_row() correctly recomputes as is_batch_replacement=False
    -- actually lands that correction in the DB, instead of leaving the row
    mislabeled "reposición" forever.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE plates
            SET plate_type = %s, plate_number = %s, plate_to = %s, city = %s,
                quantity = %s, is_voided = %s, is_plate_annulled = %s,
                is_batch_replacement = %s, is_possible_duplicate = false
            WHERE id = %s
            """,
            (plate_type, plate_number, plate_to, city, quantity, is_voided,
             is_plate_annulled, is_batch_replacement, plate_id),
        )


def resolve_plate(conn: psycopg.Connection[DictRow], plate_id: int) -> None:
    """Mark a flagged plate as reviewed (not a duplicate) without other changes."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE plates SET is_possible_duplicate = false WHERE id = %s",
            (plate_id,),
        )


def delete_plate(conn: psycopg.Connection[DictRow], plate_id: int) -> None:
    """Delete a plate row (e.g. a clear misread).

    If this leaves exactly one other flagged row with the same plate_number,
    clear its flag too — the duplicate that prompted it is gone, so there's
    nothing left to compare against. If 2+ still share the number, leave them
    flagged: there may still be a real ambiguity to review.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT plate_number FROM plates WHERE id = %s", (plate_id,))
        row = cur.fetchone()
        plate_number = row["plate_number"] if row else None
        cur.execute("DELETE FROM plates WHERE id = %s", (plate_id,))
        if plate_number:
            cur.execute(
                "SELECT id FROM plates WHERE plate_number = %s AND is_possible_duplicate = true",
                (plate_number,),
            )
            remaining = cur.fetchall()
            if len(remaining) == 1:
                cur.execute(
                    "UPDATE plates SET is_possible_duplicate = false WHERE id = %s",
                    (remaining[0]["id"],),
                )
