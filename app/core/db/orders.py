"""production_orders reads/writes, and the shared order+plates summary shape.

See app/core/db/__init__.py for the module-wide connection/transaction
contract that applies to every function here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import psycopg
from psycopg import sql
from psycopg.rows import DictRow, class_row


@dataclass
class OrderSummary:
    """One "order + aggregated plates" row, the shared shape returned by
    recent_orders/find_orders_by_number/search_orders_by_number and rendered
    by _orders_table.html (twice, via records.html) and ingest.html.

    `cities` is never NULL (COALESCE'd to '{}' in SQL) -- an order with no
    plates, or none with a city, gets an empty list, not None. `plates` CAN be
    None: a LEFT JOIN against an order with zero plate rows at all produces
    one all-NULL joined row, which string_agg has nothing to aggregate from
    (see recent_orders' docstring/tests for the 'sin placa' vs NULL
    distinction this is guarding).
    """

    id: int
    order_number: str
    order_date: date
    requested_by: str
    cities: list[str]
    n_plates: int
    total_qty: int
    plates: str | None


def insert_order(conn: psycopg.Connection[DictRow], *, order_number: str, order_date: date,
                 requested_by: str) -> int:
    """Insert one production_orders row and return its id.

    No city here on purpose: city belongs to the PLATE (one order can ship plates
    to several cities). The Registros list derives an order's cities from them.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO production_orders (order_number, order_date, requested_by)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (order_number, order_date, requested_by),
        )
        row = cur.fetchone()
        assert row is not None, "RETURNING id on a successful INSERT always yields a row"
        return row["id"]


def update_order(conn: psycopg.Connection[DictRow], order_id: int, *, order_number: str, order_date: date | str,
                 requested_by: str) -> None:
    """Edit an order's header fields (date/number/solicitado por)."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE production_orders SET order_number = %s, order_date = %s, requested_by = %s "
            "WHERE id = %s",
            (order_number, order_date, requested_by, order_id),
        )


def delete_order(conn: psycopg.Connection[DictRow], order_id: int) -> None:
    """Delete an order and all its plates (plates first, for the FK)."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM plates WHERE order_id = %s", (order_id,))
        cur.execute("DELETE FROM production_orders WHERE id = %s", (order_id,))


def order_detail(conn: psycopg.Connection[DictRow], order_id: int) -> tuple[dict | None, list[dict]]:
    """Return (order dict, list of its plate dicts) for the detail view."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, order_number, order_date, requested_by "
            "FROM production_orders WHERE id = %s",
            (order_id,),
        )
        order = cur.fetchone()
        if not order:
            return None, []
        cur.execute(
            """
            SELECT id, plate_type, plate_number, plate_to, quantity,
                   is_batch_replacement, is_voided, is_plate_annulled, city
            FROM plates WHERE order_id = %s ORDER BY id
            """,
            (order_id,),
        )
        plates = cur.fetchall()
    return order, plates


def distinct_requested_by(conn: psycopg.Connection[DictRow]) -> list[str]:
    """Existing 'solicitado por' values, most frequent first (for the dropdown)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT requested_by FROM production_orders
            WHERE requested_by IS NOT NULL AND requested_by <> ''
            GROUP BY requested_by ORDER BY count(*) DESC, requested_by
            """
        )
        # Drop punctuation-only junk like '//'.
        return [r["requested_by"] for r in cur.fetchall() if any(ch.isalnum() for ch in r["requested_by"])]


# Shared by recent_orders/find_orders_by_number/search_orders_by_number: the
# three used to each copy-paste this exact SELECT (see git history) --
# composed once here via psycopg.sql.SQL and concatenated with a per-case
# WHERE/LIMIT instead. Deliberately plain `+` concatenation, not
# `.format()`: the COALESCE default below is the literal Postgres empty-array
# `'{}'`, which `.format()` would otherwise try to parse as a placeholder.
_ORDER_SUMMARY_SELECT = sql.SQL(
    """
    SELECT o.id, o.order_number, o.order_date, o.requested_by,
           COALESCE(
               array_agg(DISTINCT p.city) FILTER (WHERE p.city IS NOT NULL),
               '{}'
           ) AS cities,
           count(p.id) AS n_plates,
           COALESCE(SUM(p.quantity), 0) AS total_qty,
           string_agg(
               CASE
                   WHEN p.id IS NULL THEN NULL  -- LEFT JOIN phantom row: no plate at all
                   WHEN p.plate_number IS NOT NULL THEN p.plate_number
                   WHEN p.is_batch_replacement THEN 'reposición'
                   WHEN p.is_voided THEN 'anulada'
                   ELSE 'sin placa'
               END, ', ' ORDER BY p.id) AS plates
    FROM production_orders o
    LEFT JOIN plates p ON p.order_id = o.id
    """
)

_ORDER_SUMMARY_GROUP_ORDER = sql.SQL(
    """
    GROUP BY o.id
    ORDER BY o.order_date DESC, o.created_at DESC, o.id DESC
    """
)


def recent_orders(conn: psycopg.Connection[DictRow], limit: int = 50) -> list[OrderSummary]:
    """Orders sorted by their real business date, most recent first.

    Sorted by order_date, NOT created_at/insertion time -- a correction
    entered today for an order dated months ago (e.g. fixing a duplicate)
    must not jump to the top of "recent" and bury today's real activity.
    created_at/id are only tiebreakers for same-day orders.

    `cities` is computed from the order's own plates (there is no order-level
    city column): a list, so a mixed carro+moto order legitimately reads
    e.g. ['CALI', 'COLOMBIA']. The template decides how to render it.
    """
    with conn.cursor(row_factory=class_row(OrderSummary)) as cur:
        cur.execute(
            _ORDER_SUMMARY_SELECT + _ORDER_SUMMARY_GROUP_ORDER + sql.SQL(" LIMIT %s"),
            (limit,),
        )
        return cur.fetchall()


def find_orders_by_number(conn: psycopg.Connection[DictRow], order_number: str) -> list[OrderSummary]:
    """Existing production_orders rows with this EXACT order_number.

    Same row shape as recent_orders() (id, order_number, order_date,
    requested_by, cities, n_plates, total_qty, plates summary string), but an
    exact match (not ILIKE) -- a stray partial match must never wrongly
    trigger the /ingest "esta orden ya existe" warning.

    order_number has no unique constraint in the schema, and historically
    nothing ever checked for a collision here (only plate_number dedup did),
    so 0, 1, or even 2+ rows are all possible results -- no LIMIT needed. See
    app.core.ingestion.replace_order, which fuses every match this returns into
    exactly one order.
    """
    with conn.cursor(row_factory=class_row(OrderSummary)) as cur:
        cur.execute(
            _ORDER_SUMMARY_SELECT + sql.SQL(" WHERE o.order_number = %s ") + _ORDER_SUMMARY_GROUP_ORDER,
            (order_number,),
        )
        return cur.fetchall()


def search_orders_by_number(
    conn: psycopg.Connection[DictRow], query: str, limit: int = 200
) -> list[OrderSummary]:
    """Find orders anywhere in the DB by (partial) order_number, not just the
    recent-orders page -- lets the person look up a specific old order without
    scrolling past everything newer than it.

    Same row shape as recent_orders() (id, order_number, order_date,
    requested_by, cities, n_plates, total_qty, plates summary) so the results
    can be rendered with the same table. order_number is plain text/digits,
    not a plate number, so unlike search_plates_by_number this does NOT run
    the query through clean_plate() -- just strips whitespace. Partial match
    (ILIKE %query%) since the person may not remember it in full. Most recent
    order first, same tiebreaking as recent_orders().
    """
    needle = f"%{query.strip()}%"
    with conn.cursor(row_factory=class_row(OrderSummary)) as cur:
        cur.execute(
            _ORDER_SUMMARY_SELECT
            + sql.SQL(" WHERE o.order_number ILIKE %s ")
            + _ORDER_SUMMARY_GROUP_ORDER
            + sql.SQL(" LIMIT %s"),
            (needle, limit),
        )
        return cur.fetchall()
