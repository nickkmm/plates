"""Database access for plate-production ingestion (psycopg 3).

Connection settings come from environment variables (same ones the Postgres
container uses, plus DB_HOST/DB_PORT for where to reach it):
    DB_HOST (default "db"), DB_PORT (default "5432"),
    POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
"""

from __future__ import annotations

import os
from collections.abc import Iterable

import psycopg

from app.core.models import ExtractedRow


def get_conn() -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ.get("DB_HOST", "db"),
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def existing_plate_numbers(conn: psycopg.Connection, candidates: Iterable[str]) -> set[str]:
    """Return the subset of `candidates` already present as live (non-voided) plates."""
    values = [c for c in {c for c in candidates if c}]
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
        return {r[0] for r in cur.fetchall()}


def insert_order(conn: psycopg.Connection, *, order_number: int, order_date, requested_by: str,
                 city: str | None) -> int:
    """Insert one production_orders row and return its id."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO production_orders (order_number, order_date, requested_by, city)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (order_number, order_date, requested_by, city),
        )
        return cur.fetchone()[0]


def insert_plate(conn: psycopg.Connection, *, order_id: int, row: ExtractedRow,
                 is_possible_duplicate: bool) -> None:
    """Insert one plates row linked to order_id."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO plates (
                order_id, plate_type, plate_number, plate_to, is_batch_replacement,
                is_voided, is_plate_annulled, quantity, material, prepared_by, is_possible_duplicate
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            ),
        )


def insert_blank_preparation(conn: psycopg.Connection, *, prep_date, category: str,
                             quantity: int, prepared_by: str | None) -> None:
    """Record one ALISTAMIENTO event (blanks prepared / material intake)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO blank_preparations (prep_date, category, quantity, prepared_by)
            VALUES (%s, %s, %s, %s)
            """,
            (prep_date, category, quantity, prepared_by),
        )


def recalc_inventory(conn: psycopg.Connection) -> None:
    """Recompute prepared and consumed per category.

      prepared = SUM(blank_preparations.quantity)  (includes the Feb baseline,
                 which is stored as the first ALISTAMIENTO events)
      consumed = SUM(plates.quantity) excluding order-annulled (is_voided = true).
                 REPOSICION and plate-annulled count as consumed.
      available (in the view) = prepared - consumed.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO inventory_categories (category, prepared)
            SELECT category, COALESCE(SUM(quantity), 0)
            FROM blank_preparations
            GROUP BY category
            ON CONFLICT (category) DO UPDATE SET prepared = EXCLUDED.prepared
            """
        )
        cur.execute(
            """
            INSERT INTO inventory_categories (category, consumed)
            SELECT plate_type, COALESCE(SUM(quantity), 0)
            FROM plates
            WHERE is_voided = false
            GROUP BY plate_type
            ON CONFLICT (category) DO UPDATE SET consumed = EXCLUDED.consumed
            """
        )


def inventory_view(conn: psycopg.Connection) -> list[dict]:
    """Per-category inventory with computed available = prepared - consumed."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ic.category, ic.prepared, ic.consumed,
                   (ic.prepared - ic.consumed) AS available,
                   COALESCE(w.wasted, 0) AS wasted
            FROM inventory_categories ic
            LEFT JOIN (
                SELECT plate_type, SUM(quantity) AS wasted
                FROM plates WHERE is_plate_annulled = true
                GROUP BY plate_type
            ) w ON w.plate_type = ic.category
            ORDER BY ic.category
            """
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def distinct_requested_by(conn: psycopg.Connection) -> list[str]:
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
        return [r[0] for r in cur.fetchall() if any(ch.isalnum() for ch in r[0])]


def distinct_cities(conn: psycopg.Connection) -> list[str]:
    """Existing cities, most frequent first; drops obvious junk/annulment markers."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT city FROM production_orders
            WHERE city IS NOT NULL AND city <> ''
            GROUP BY city ORDER BY count(*) DESC, city
            """
        )
        return [c for c in (r[0] for r in cur.fetchall())
                if any(ch.isalnum() for ch in c) and "ANULAD" not in c.upper()]


def recent_orders(conn: psycopg.Connection, limit: int = 50) -> list[dict]:
    """Most recently registered orders with a summary of their plates."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.id, o.order_number, o.order_date, o.requested_by, o.city,
                   count(p.id) AS n_plates,
                   COALESCE(SUM(p.quantity), 0) AS total_qty,
                   string_agg(
                       CASE
                           WHEN p.plate_number IS NOT NULL THEN p.plate_number
                           WHEN p.is_batch_replacement THEN 'reposición'
                           WHEN p.is_voided THEN 'anulada'
                           ELSE 'sin placa'
                       END, ', ' ORDER BY p.id) AS plates
            FROM production_orders o
            LEFT JOIN plates p ON p.order_id = o.id
            GROUP BY o.id
            ORDER BY o.created_at DESC, o.id DESC
            LIMIT %s
            """,
            (limit,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def order_detail(conn: psycopg.Connection, order_id: int):
    """Return (order dict, list of its plate dicts) for the detail view."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, order_number, order_date, requested_by, city "
            "FROM production_orders WHERE id = %s",
            (order_id,),
        )
        row = cur.fetchone()
        if not row:
            return None, []
        order = dict(zip([d[0] for d in cur.description], row))
        cur.execute(
            """
            SELECT plate_type, plate_number, plate_to, quantity,
                   is_batch_replacement, is_voided, is_plate_annulled
            FROM plates WHERE order_id = %s ORDER BY id
            """,
            (order_id,),
        )
        pcols = [d[0] for d in cur.description]
        plates = [dict(zip(pcols, r)) for r in cur.fetchall()]
    return order, plates


def delete_order(conn: psycopg.Connection, order_id: int) -> None:
    """Delete an order and all its plates (plates first, for the FK)."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM plates WHERE order_id = %s", (order_id,))
        cur.execute("DELETE FROM production_orders WHERE id = %s", (order_id,))


def review_rows(conn: psycopg.Connection) -> list[dict]:
    """Rows flagged for review: possible duplicates only (real repeated plates)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.id, o.order_number, o.order_date, o.requested_by,
                   p.plate_type, p.plate_number, p.quantity, p.is_voided
            FROM plates p
            JOIN production_orders o ON o.id = p.order_id
            WHERE p.is_possible_duplicate = true
            ORDER BY p.plate_number, o.order_date
            """
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def update_plate(conn: psycopg.Connection, plate_id: int, *, plate_type: str,
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


def resolve_plate(conn: psycopg.Connection, plate_id: int) -> None:
    """Mark a flagged plate as reviewed (not a duplicate) without other changes."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE plates SET is_possible_duplicate = false WHERE id = %s",
            (plate_id,),
        )


def delete_plate(conn: psycopg.Connection, plate_id: int) -> None:
    """Delete a plate row (e.g. a clear misread)."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM plates WHERE id = %s", (plate_id,))
