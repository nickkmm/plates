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

from app.core.db.plates import relink_plates
from app.core.models import ExtractedRow


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


def insert_order(
    conn: psycopg.Connection[DictRow], *, row: ExtractedRow, customer_id: int | None, created_by: int | None
) -> int:
    """Insert one production_orders row (order header + every Fase 2 POS
    field) and return its id.

    Takes the full ExtractedRow, like insert_plate already does, instead of
    listing each order-level field as its own kwarg -- with 10 POS fields on
    top of order_number/order_date/requested_by that would be 13 loose kwargs,
    unreadable and easy to get out of sync as more get added. row.plate_type/
    plate_number/quantity/etc. are simply ignored (irrelevant to
    production_orders) -- same as insert_plate ignoring order-level fields.

    customer_id/created_by are passed separately, NOT read off `row`:
    customer_id is RESOLVED from row.requested_by by the caller (see
    db.resolve_or_create_customer) rather than typed, and created_by comes
    from session["user_id"], which ExtractedRow has no business knowing
    about. Both are required (no default) so every caller makes an explicit
    choice, same convention as insert_plate's is_possible_duplicate.

    requested_by keeps being written verbatim -- it's a denormalized copy of
    customers.name kept in sync by every writer that sets customer_id (see
    docs/DATABASE.md's "rareza"), and this function is exactly one of those
    writers.

    No city here on purpose: city belongs to the PLATE (one order can ship plates
    to several cities). The Registros list derives an order's cities from them.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO production_orders (
                order_number, order_date, requested_by, customer_id,
                receipt_type, receipt_number, payment_method, tracking_number,
                client_order_number, delivery_date, payment_date,
                dispatched_by, received_by, notes, created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                row.order_number, row.order_date, row.requested_by, customer_id,
                row.receipt_type, row.receipt_number, row.payment_method, row.tracking_number,
                row.client_order_number, row.delivery_date, row.payment_date,
                row.dispatched_by, row.received_by, row.notes, created_by,
            ),
        )
        result = cur.fetchone()
        assert result is not None, "RETURNING id on a successful INSERT always yields a row"
        return result["id"]


class _Unset:
    """Sentinel type for update_order's `customer_id` default -- see its
    docstring for why this isn't simply `None`."""

    def __repr__(self) -> str:
        return "<UNSET>"


_UNSET = _Unset()


def update_order(
    conn: psycopg.Connection[DictRow], order_id: int, *, order_number: str, order_date: date | str,
    requested_by: str, customer_id: int | None | _Unset = _UNSET,
    receipt_type: str | None = None, receipt_number: str | None = None,
    payment_method: str | None = None, tracking_number: str | None = None,
    client_order_number: str | None = None, delivery_date: date | str | None = None,
    payment_date: date | str | None = None, dispatched_by: str | None = None,
    received_by: str | None = None, notes: str | None = None,
) -> None:
    """Edit an order's header fields (date/number/solicitado por + Fase 3's
    POS fields), and optionally re-point its customer_id.

    customer_id defaults to a private "not provided" sentinel, deliberately
    NOT None: before Fase 3, record_save didn't resolve/pass a customer_id at
    all, and if the default silently meant "set customer_id to NULL", every
    existing Registro-detail save would have wiped the order's customer link
    the moment this shipped. Pass customer_id explicitly -- an int, or None
    to genuinely clear it (e.g. an order whose requested_by no longer
    resolves to anything) -- to change it; omit it to leave the column
    exactly as it was.

    The 10 POS fields below have no such sentinel: record_save (their only
    real caller) always submits the record_detail.html form in full, so
    "not provided" and "genuinely blank" are the same thing here -- default
    None simply means "clear it", matching every column's own nullability.
    """
    with conn.cursor() as cur:
        if isinstance(customer_id, _Unset):
            cur.execute(
                """
                UPDATE production_orders
                SET order_number = %s, order_date = %s, requested_by = %s,
                    receipt_type = %s, receipt_number = %s, payment_method = %s,
                    tracking_number = %s, client_order_number = %s, delivery_date = %s,
                    payment_date = %s, dispatched_by = %s, received_by = %s, notes = %s
                WHERE id = %s
                """,
                (order_number, order_date, requested_by,
                 receipt_type, receipt_number, payment_method, tracking_number,
                 client_order_number, delivery_date, payment_date, dispatched_by,
                 received_by, notes, order_id),
            )
        else:
            cur.execute(
                """
                UPDATE production_orders
                SET order_number = %s, order_date = %s, requested_by = %s, customer_id = %s,
                    receipt_type = %s, receipt_number = %s, payment_method = %s,
                    tracking_number = %s, client_order_number = %s, delivery_date = %s,
                    payment_date = %s, dispatched_by = %s, received_by = %s, notes = %s
                WHERE id = %s
                """,
                (order_number, order_date, requested_by, customer_id,
                 receipt_type, receipt_number, payment_method, tracking_number,
                 client_order_number, delivery_date, payment_date, dispatched_by,
                 received_by, notes, order_id),
            )
    if not isinstance(customer_id, _Unset):
        # The order may now belong to a customer of another type: recompute
        # which of its plates may carry an ownership_card_id.
        relink_plates(conn, order_id=order_id)


def delete_order(conn: psycopg.Connection[DictRow], order_id: int) -> None:
    """Delete an order and all its plates (plates first, for the FK)."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM plates WHERE order_id = %s", (order_id,))
        cur.execute("DELETE FROM production_orders WHERE id = %s", (order_id,))


def order_detail(conn: psycopg.Connection[DictRow], order_id: int) -> tuple[dict | None, list[dict]]:
    """Return (order dict, list of its plate dicts) for the detail view.

    LEFT JOINs customers on customer_id (Fase 2) so the caller gets the
    resolved client's attributes alongside the order's own POS fields in one
    round trip -- LEFT, not JOIN, because customer_id is nullable (the
    backfill left one historical order unresolved). Customer columns are
    aliased `customer_*` to avoid colliding with anything else in the dict
    (there's no other `type`/`phone`/`email` on production_orders).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.id, o.order_number, o.order_date, o.requested_by,
                   o.receipt_type, o.receipt_number, o.customer_id,
                   o.payment_method, o.tracking_number, o.client_order_number,
                   o.delivery_date, o.payment_date, o.dispatched_by,
                   o.received_by, o.notes, o.created_by,
                   c.name AS customer_name, c.type AS customer_type,
                   c.document_type AS customer_document_type,
                   c.document_number AS customer_document_number,
                   c.document_relation AS customer_document_relation,
                   c.phone AS customer_phone, c.email AS customer_email,
                   c.address AS customer_address,
                   c.neighborhood AS customer_neighborhood,
                   c.municipality AS customer_municipality
            FROM production_orders o
            LEFT JOIN customers c ON c.id = o.customer_id
            WHERE o.id = %s
            """,
            (order_id,),
        )
        order = cur.fetchone()
        if not order:
            return None, []
        cur.execute(
            """
            SELECT p.id, p.plate_type, p.plate_number, p.plate_to, p.quantity,
                   p.is_batch_replacement, p.is_voided, p.is_plate_annulled, p.city,
                   p.unit_price, p.reason, p.vehicle_brand,
                   p.ownership_card_id, oc.transit_license AS card_transit_license
            FROM plates p
            LEFT JOIN ownership_cards oc ON oc.id = p.ownership_card_id
            WHERE p.order_id = %s ORDER BY p.id
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
