"""production_orders.production_status / production_order_events reads/writes.

Every order moves pending -> in_progress -> ready -> delivered (English
identifiers; the Spanish labels belong to the web layer), and every change
is recorded as one `production_order_events` row. This module is only the
data layer; the transition rules live in app/core/production.py.

See app/shared/db.py for the module-wide connection/transaction
contract that applies to every function here.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import psycopg
from psycopg.rows import DictRow, class_row

from app.shared.normalize import clean_plate

# production_orders.production_status values, in the order an order moves
# through them.
STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_READY = "ready"
STATUS_DELIVERED = "delivered"
PRODUCTION_STATUSES = (STATUS_PENDING, STATUS_IN_PROGRESS, STATUS_READY, STATUS_DELIVERED)
# Still on the production floor (sorted oldest first on the board).
ACTIVE_STATUSES = (STATUS_PENDING, STATUS_IN_PROGRESS, STATUS_READY)


@dataclass
class ProductionOrderSummary:
    """One order as the production board lists it.

    `plate_count` is the sum of `quantity` over the non-voided plates (0 when
    there are none). `plates` is built like OrderSummary.plates (same
    placeholders, NULL for an order with no plates at all).
    `status_changed_at` is the latest event's time, or the order's
    created_at while it has no events.
    """

    id: int
    order_number: str
    order_date: date
    requested_by: str
    delivery_date: date | None
    production_status: str
    plate_count: int
    plates: str | None
    declaration_ids: list[int]
    status_changed_at: datetime | None


@dataclass
class ProductionEvent:
    """One `production_order_events` row, with the username of who made the
    change (None when user_id is NULL)."""

    from_status: str
    to_status: str
    username: str | None
    created_at: datetime


# An order whose plates are ALL voided (an annulled order) is left off the
# board; an order with no plates at all is kept.
_NOT_FULLY_VOIDED = """(
    NOT EXISTS (SELECT 1 FROM plates vp WHERE vp.order_id = o.id)
    OR EXISTS (SELECT 1 FROM plates vp WHERE vp.order_id = o.id AND NOT COALESCE(vp.is_voided, false))
)"""


def list_production_orders(
    conn: psycopg.Connection[DictRow], *, status: str | None, query: str | None = None, limit: int = 200,
) -> list[ProductionOrderSummary]:
    """Orders for the production board, fully voided orders excluded.

    `status` keeps one production_status (None = every status). A non-blank
    `query` keeps the orders whose order_number contains it (stripped,
    case-insensitive) or with any plate whose number contains it cleaned
    like normalize.clean_plate ("abc 123" finds ABC123). Active statuses come
    oldest first (the queue to work through); delivered and "all" newest
    first.
    """
    where = [_NOT_FULLY_VOIDED]
    params: list[Any] = []
    if status is not None:
        where.append("o.production_status = %s")
        params.append(status)
    if query and query.strip():
        where.append(
            "(o.order_number ILIKE %s OR EXISTS ("
            "SELECT 1 FROM plates qp WHERE qp.order_id = o.id AND qp.plate_number ILIKE %s))"
        )
        params.extend([f"%{query.strip()}%", f"%{clean_plate(query)}%"])
    direction = "ASC" if status in ACTIVE_STATUSES else "DESC"
    with conn.cursor(row_factory=class_row(ProductionOrderSummary)) as cur:
        cur.execute(
            f"""
            SELECT o.id, o.order_number, o.order_date, o.requested_by, o.delivery_date,
                   o.production_status,
                   COALESCE(SUM(p.quantity) FILTER (WHERE NOT COALESCE(p.is_voided, false)), 0)
                       AS plate_count,
                   string_agg(
                       CASE
                           WHEN p.id IS NULL THEN NULL  -- LEFT JOIN phantom row: no plate at all
                           WHEN p.plate_number IS NOT NULL THEN p.plate_number
                           WHEN p.is_batch_replacement THEN 'reposición'
                           WHEN p.is_voided THEN 'anulada'
                           ELSE 'sin placa'
                       END, ', ' ORDER BY p.id) AS plates,
                   COALESCE(
                       (SELECT array_agg(d.id ORDER BY d.id) FROM declarations d
                        WHERE d.production_order_id = o.id),
                       '{{}}'
                   ) AS declaration_ids,
                   COALESCE(
                       (SELECT max(e.created_at) FROM production_order_events e WHERE e.order_id = o.id),
                       o.created_at
                   ) AS status_changed_at
            FROM production_orders o
            LEFT JOIN plates p ON p.order_id = o.id
            WHERE {' AND '.join(where)}
            GROUP BY o.id
            ORDER BY o.order_date {direction}, o.id {direction}
            LIMIT %s
            """,
            (*params, limit),
        )
        return cur.fetchall()


def count_by_production_status(conn: psycopg.Connection[DictRow]) -> dict[str, int]:
    """How many orders are in each status (every PRODUCTION_STATUSES key, 0
    when none), with the same fully-voided exclusion as
    list_production_orders."""
    counts = dict.fromkeys(PRODUCTION_STATUSES, 0)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT o.production_status, count(*) AS n
            FROM production_orders o
            WHERE {_NOT_FULLY_VOIDED}
            GROUP BY o.production_status
            """
        )
        for row in cur.fetchall():
            counts[row["production_status"]] = int(row["n"])
    return counts


def get_production_status(conn: psycopg.Connection[DictRow], order_id: int) -> str | None:
    """The order's production_status, or None when the order does not exist."""
    with conn.cursor() as cur:
        cur.execute("SELECT production_status FROM production_orders WHERE id = %s", (order_id,))
        row = cur.fetchone()
        return None if row is None else str(row["production_status"])


def set_production_status(
    conn: psycopg.Connection[DictRow], order_id: int, *, from_status: str, to_status: str,
    user_id: int | None,
) -> bool:
    """Move the order from `from_status` to `to_status` and record the event.

    The UPDATE only matches while the order is still in `from_status`, so a
    change someone else made in the meantime is never overwritten: then
    nothing is written and this returns False.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE production_orders SET production_status = %s WHERE id = %s AND production_status = %s",
            (to_status, order_id, from_status),
        )
        if cur.rowcount != 1:
            return False
        cur.execute(
            "INSERT INTO production_order_events (order_id, from_status, to_status, user_id) "
            "VALUES (%s, %s, %s, %s)",
            (order_id, from_status, to_status, user_id),
        )
    return True


def list_production_events(conn: psycopg.Connection[DictRow], order_id: int) -> list[ProductionEvent]:
    """The order's status history, oldest first."""
    with conn.cursor(row_factory=class_row(ProductionEvent)) as cur:
        cur.execute(
            """
            SELECT e.from_status, e.to_status, u.username, e.created_at
            FROM production_order_events e
            LEFT JOIN users u ON u.id = e.user_id
            WHERE e.order_id = %s
            ORDER BY e.created_at, e.id
            """,
            (order_id,),
        )
        return cur.fetchall()


def production_statuses_for_orders(
    conn: psycopg.Connection[DictRow], order_ids: Collection[int],
) -> dict[int, str]:
    """{order_id: production_status} for several orders in one query (missing
    ids are simply absent); {} without querying for an empty input."""
    if not order_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, production_status FROM production_orders WHERE id = ANY(%s)",
            (list(order_ids),),
        )
        return {row["id"]: row["production_status"] for row in cur.fetchall()}
