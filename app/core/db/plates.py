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
from datetime import date
from decimal import Decimal

import psycopg
from psycopg.rows import DictRow

from app.core.db.ownership_cards import resolve_card_id_for_plate
from app.core.db.pricing import current_prices
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


def last_fabrication_date(conn: psycopg.Connection[DictRow], plate_number: str) -> date | None:
    """Read-only: the latest `order_date` of an order that fabricated a plate
    with exactly this `plate_number` (normalized with clean_plate), or None.

    Plates that are `is_voided` or `is_plate_annulled` are IGNORED: an
    annulled plate was never really delivered, so it must not trigger the
    declarations "made less than 6 months ago" warning.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT MAX(o.order_date) AS last_date
            FROM plates p
            JOIN production_orders o ON o.id = p.order_id
            WHERE p.plate_number = %s
              AND p.is_voided IS NOT TRUE AND p.is_plate_annulled IS NOT TRUE
            """,
            (clean_plate(plate_number),),
        )
        row = cur.fetchone()
        return row["last_date"] if row else None


def insert_plate(conn: psycopg.Connection[DictRow], *, order_id: int, row: ExtractedRow,
                 is_possible_duplicate: bool) -> None:
    """Insert one plates row linked to order_id.

    row.unit_price/reason/vehicle_brand (Fase 2) are written as given --
    this function does NOT apply the tariff-snapshot fallback itself (that's
    app.core.ingestion's job, once per batch, before calling this per row);
    it just persists whatever the row already carries, same as every other
    field here.

    ownership_card_id is NOT taken from `row`: it is resolved here from the
    order's customer type and the plate (see _card_id_for and
    ownership_cards.resolve_card_id_for_plate), so every insert path
    (ingestion, replace_order, record_save) applies the same rule.
    """
    with conn.cursor() as cur:
        card_id = _card_id_for(
            cur, order_id=order_id, plate_number=row.plate_number, plate_to=row.plate_to,
            is_voided=row.is_voided, is_plate_annulled=row.is_plate_annulled,
            is_batch_replacement=row.is_batch_replacement,
        )
        cur.execute(
            """
            INSERT INTO plates (
                order_id, plate_type, plate_number, plate_to, is_batch_replacement,
                is_voided, is_plate_annulled, quantity, material, prepared_by,
                is_possible_duplicate, city, unit_price, reason, vehicle_brand,
                ownership_card_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                row.unit_price,
                row.reason,
                row.vehicle_brand,
                card_id,
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


def _card_id_for(
    cur: psycopg.Cursor[DictRow], *, order_id: int | None, plate_number: str | None,
    plate_to: str | None, is_voided: bool | None, is_plate_annulled: bool | None,
    is_batch_replacement: bool | None,
) -> int | None:
    """Resolve plates.ownership_card_id for a plate of `order_id` on the
    caller's cursor: looks up the order's customer type (NULL when the order
    or its customer is missing/unclassified) and delegates the rule to
    ownership_cards.resolve_card_id_for_plate."""
    customer_type = None
    if order_id is not None:
        cur.execute(
            """
            SELECT c.type FROM production_orders o
            LEFT JOIN customers c ON c.id = o.customer_id
            WHERE o.id = %s
            """,
            (order_id,),
        )
        found = cur.fetchone()
        customer_type = found["type"] if found else None
    return resolve_card_id_for_plate(
        cur.connection, plate_number, customer_type, plate_to=plate_to,
        is_voided=bool(is_voided), is_plate_annulled=bool(is_plate_annulled),
        is_batch_replacement=bool(is_batch_replacement),
    )


def _resnapshot_unit_price(
    cur: psycopg.Cursor[DictRow], plate_id: int, new_plate_type: str
) -> Decimal | None:
    """The unit_price re-snapshot rule (see PLAN_ARQUITECTURA_Y_POS.md /
    docs/DATABASE.md), shared by update_plate and update_plate_full -- both
    let plate_type change, so both need it:

      - plate_type UNCHANGED -> unit_price is left exactly as it was,
        regardless of any other field changing (e.g. quantity) -- the total
        is always unit_price * quantity, computed on read, never stored.
      - plate_type CHANGED -> re-take the CURRENT suggested tariff for the
        NEW type (via pricing.current_prices), same as a brand-new plate.
        Stays None if that type has no configured tariff yet -- never a
        silent 0 (a NULL unit_price raises an explicit /cuadre notice).

    Reads the plate's existing plate_type/unit_price on the SAME cursor
    (before the caller's own UPDATE runs), so this must be called first.
    """
    cur.execute("SELECT plate_type, unit_price FROM plates WHERE id = %s", (plate_id,))
    current = cur.fetchone()
    old_plate_type = current["plate_type"] if current else None
    old_unit_price = current["unit_price"] if current else None
    if old_plate_type == new_plate_type:
        return old_unit_price
    return current_prices(cur.connection).get(new_plate_type)


def update_plate(conn: psycopg.Connection[DictRow], plate_id: int, *, plate_type: str,
                 plate_number: str | None, quantity: int, is_voided: bool) -> None:
    """Edit a plate row and clear its review flag (treated as reviewed).

    Used by the /review duplicate-review queue, which also allows changing
    plate_type -- so this applies the unit_price re-snapshot rule too (see
    _resnapshot_unit_price).
    """
    with conn.cursor() as cur:
        new_unit_price = _resnapshot_unit_price(cur, plate_id, plate_type)
        cur.execute(
            "SELECT order_id, plate_to, is_plate_annulled, is_batch_replacement "
            "FROM plates WHERE id = %s",
            (plate_id,),
        )
        current = cur.fetchone()
        card_id = _card_id_for(
            cur, order_id=current["order_id"] if current else None,
            plate_number=plate_number, plate_to=current["plate_to"] if current else None,
            is_voided=is_voided,
            is_plate_annulled=current["is_plate_annulled"] if current else False,
            is_batch_replacement=current["is_batch_replacement"] if current else False,
        )
        cur.execute(
            """
            UPDATE plates
            SET plate_type = %s, plate_number = %s, quantity = %s,
                is_voided = %s, is_possible_duplicate = false, unit_price = %s,
                ownership_card_id = %s
            WHERE id = %s
            """,
            (plate_type, plate_number, quantity, is_voided, new_unit_price, card_id, plate_id),
        )


def update_plate_full(conn: psycopg.Connection[DictRow], plate_id: int, *, plate_type: str,
                      plate_number: str | None, plate_to: str | None, city: str | None,
                      quantity: int, is_voided: bool, is_plate_annulled: bool,
                      is_batch_replacement: bool, reason: str | None = None,
                      vehicle_brand: str | None = None) -> None:
    """Edit every plate-level field from the Registro detail page.

    Wider than update_plate (which only covers the flagged-duplicate review
    form's fields): this also lets a range end, city, and the "placa anulada"
    waste flag be corrected after the order was already saved. Clears
    is_possible_duplicate too, same "editing counts as reviewed" convention.
    Also applies the unit_price re-snapshot rule (see _resnapshot_unit_price)
    since this route can change plate_type too.

    is_batch_replacement is a required param (not left untouched) so that
    editing a reposición row's plate_number in place to a real plate number
    -- which normalize_row() correctly recomputes as is_batch_replacement=False
    -- actually lands that correction in the DB, instead of leaving the row
    mislabeled "reposición" forever.

    reason/vehicle_brand (Fase 3) are plain per-line attributes with no
    re-snapshot rule of their own (that rule is unit_price-specific) --
    written verbatim, same as plate_number/city/etc above. unit_price itself
    is deliberately NOT a parameter here: the re-snapshot rule (unchanged
    plate_type keeps the old value; changed plate_type re-takes the current
    tariff) is the ONLY way this function ever sets it, so there is no path
    for a caller to overwrite an existing plate's price directly.

    ownership_card_id is likewise never a parameter: it is RECOMPUTED on
    every call from the (new) plate_number/range/void/annulled/replacement
    flags and the order's customer type, so changing the number re-links,
    and a plate that becomes ineligible is cleared back to NULL.
    """
    with conn.cursor() as cur:
        new_unit_price = _resnapshot_unit_price(cur, plate_id, plate_type)
        cur.execute("SELECT order_id FROM plates WHERE id = %s", (plate_id,))
        current = cur.fetchone()
        card_id = _card_id_for(
            cur, order_id=current["order_id"] if current else None,
            plate_number=plate_number, plate_to=plate_to, is_voided=is_voided,
            is_plate_annulled=is_plate_annulled, is_batch_replacement=is_batch_replacement,
        )
        cur.execute(
            """
            UPDATE plates
            SET plate_type = %s, plate_number = %s, plate_to = %s, city = %s,
                quantity = %s, is_voided = %s, is_plate_annulled = %s,
                is_batch_replacement = %s, is_possible_duplicate = false,
                unit_price = %s, reason = %s, vehicle_brand = %s,
                ownership_card_id = %s
            WHERE id = %s
            """,
            (plate_type, plate_number, plate_to, city, quantity, is_voided,
             is_plate_annulled, is_batch_replacement, new_unit_price, reason, vehicle_brand,
             card_id, plate_id),
        )


def relink_plates(conn: psycopg.Connection[DictRow], *, order_id: int | None = None,
                  customer_id: int | None = None) -> None:
    """Recompute plates.ownership_card_id for every plate of one order
    (`order_id`) or of every order of one customer (`customer_id`).

    Needed when something OTHER than the plate changes its eligibility: an
    order is re-pointed to another customer (update_order) or a customer's
    type is edited (update_customer). Exactly one of the two must be given.
    """
    if (order_id is None) == (customer_id is None):
        raise ValueError("pass exactly one of order_id / customer_id")
    with conn.cursor() as cur:
        if order_id is not None:
            cur.execute("SELECT id FROM production_orders WHERE id = %s", (order_id,))
        else:
            cur.execute("SELECT id FROM production_orders WHERE customer_id = %s", (customer_id,))
        order_ids = [r["id"] for r in cur.fetchall()]
        for oid in order_ids:
            cur.execute(
                """
                SELECT id, plate_number, plate_to, is_voided, is_plate_annulled,
                       is_batch_replacement, ownership_card_id
                FROM plates WHERE order_id = %s
                """,
                (oid,),
            )
            for p in cur.fetchall():
                card_id = _card_id_for(
                    cur, order_id=oid, plate_number=p["plate_number"], plate_to=p["plate_to"],
                    is_voided=p["is_voided"], is_plate_annulled=p["is_plate_annulled"],
                    is_batch_replacement=p["is_batch_replacement"],
                )
                if card_id != p["ownership_card_id"]:
                    cur.execute(
                        "UPDATE plates SET ownership_card_id = %s WHERE id = %s", (card_id, p["id"])
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
