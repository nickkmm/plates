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
                order_id, plate_type, plate_number, is_batch_replacement,
                is_voided, quantity, is_possible_duplicate
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                order_id,
                row.plate_type,
                row.plate_number,
                row.is_batch_replacement,
                row.is_voided,
                row.quantity,
                is_possible_duplicate,
            ),
        )


def recalc_inventory(conn: psycopg.Connection) -> None:
    """Recompute inventory_categories from the plates table.

    DEFAULT semantics (to be confirmed with the user):
      category = plate_type
      prepared = SUM(quantity) of non-voided plates for that type
    initial_stock and consumed are preserved (only `prepared` is updated;
    new categories get prepared with the others left at their column defaults).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO inventory_categories (category, prepared)
            SELECT plate_type, COALESCE(SUM(quantity), 0)
            FROM plates
            WHERE is_voided = false
            GROUP BY plate_type
            ON CONFLICT (category)
            DO UPDATE SET prepared = EXCLUDED.prepared
            """
        )


def review_rows(conn: psycopg.Connection, quantity_threshold: int) -> list[dict]:
    """Rows flagged for review: possible duplicates OR unusually large quantities.

    The quantity flag is computed dynamically (no schema change): any non-voided
    plate whose quantity exceeds `quantity_threshold`.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.id, o.order_number, o.order_date, o.requested_by,
                   p.plate_type, p.plate_number, p.quantity,
                   p.is_possible_duplicate,
                   (p.quantity > %s AND p.is_voided = false) AS unusual_quantity
            FROM plates p
            JOIN production_orders o ON o.id = p.order_id
            WHERE p.is_possible_duplicate = true
               OR (p.quantity > %s AND p.is_voided = false)
            ORDER BY o.order_date DESC, o.order_number
            """,
            (quantity_threshold, quantity_threshold),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
