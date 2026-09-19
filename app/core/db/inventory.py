"""blank_preparations / inventory_categories reads/writes.

`_consumed_by_material` and its SELECT constant query `plates`, not
`inventory_categories`/`blank_preparations` -- they stay here anyway (a
deliberate exception to "group by table") because they're an implementation
detail of THIS page/feature (per-material inventory), not something any
plates-related caller elsewhere needs.

See app/core/db/__init__.py for the module-wide connection/transaction
contract that applies to every function here.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

import psycopg
from psycopg import sql
from psycopg.rows import DictRow

from app.core.normalize import INVENTORY_CATEGORIES, inventory_category


def insert_blank_preparation(conn: psycopg.Connection[DictRow], *, prep_date: date | str | None, category: str,
                             quantity: int, prepared_by: str | None) -> None:
    """Record one ALISTAMIENTO event (blanks prepared / material intake).

    prep_date is a real `date` when called from process_rows (via the pydantic-
    coerced ExtractedRow), but the standalone /inventory/alistado form route
    passes the raw form string (or None) directly; the column is nullable and
    psycopg/Postgres accept an ISO date string for a DATE column, so both are
    valid as-is.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO blank_preparations (prep_date, category, quantity, prepared_by)
            VALUES (%s, %s, %s, %s)
            """,
            (prep_date, category, quantity, prepared_by),
        )


_CONSUMED_BY_MATERIAL_SELECT = sql.SQL(
    "SELECT plate_type, COALESCE(SUM(quantity), 0) AS qty FROM plates WHERE {where} GROUP BY plate_type"
)


def _consumed_by_material(cur: psycopg.Cursor[DictRow], *, where: sql.Composable) -> dict[str, int]:
    """Sum plate quantities per inventory MATERIAL, mapping types in Python.

    Ornamental (and any other no-material type) maps to None and is skipped.

    `where` is a psycopg.sql.Composable (e.g. sql.SQL("is_voided = false")), not
    an f-string -- this is the one query in the module that used to build its
    WHERE clause via raw string interpolation; composing it through sql.SQL
    keeps that pattern from being copied by a future caller that DOES need to
    interpolate untrusted input (e.g. a /cuadre date-range filter).
    """
    cur.execute(_CONSUMED_BY_MATERIAL_SELECT.format(where=where))
    totals: dict[str, int] = defaultdict(int)
    for row in cur.fetchall():
        material = inventory_category(row["plate_type"])
        if material is not None:
            totals[material] += row["qty"]
    return totals


def recalc_inventory(conn: psycopg.Connection[DictRow]) -> None:
    """Recompute prepared and consumed per inventory MATERIAL.

      prepared = SUM(blank_preparations.quantity) per category.
      consumed = SUM(plates.quantity) per material (types mapped via
                 inventory_category), excluding order-annulled (is_voided=true).
                 Ornamental consumes no material (skipped). available = prepared - consumed.
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
        # Iterate over the fixed set of materials (not just this query's keys) so a
        # category that just lost its last live plate still gets consumed reset to
        # 0, instead of being silently skipped (GROUP BY drops empty categories).
        consumed = _consumed_by_material(cur, where=sql.SQL("is_voided = false"))
        for material in INVENTORY_CATEGORIES:
            cur.execute(
                """
                INSERT INTO inventory_categories (category, consumed) VALUES (%s, %s)
                ON CONFLICT (category) DO UPDATE SET consumed = EXCLUDED.consumed
                """,
                (material, consumed.get(material, 0)),
            )


def inventory_view(conn: psycopg.Connection[DictRow]) -> list[dict]:
    """Per-material inventory with available = prepared - consumed, plus wasted."""
    with conn.cursor() as cur:
        wasted = _consumed_by_material(cur, where=sql.SQL("is_plate_annulled = true"))
        cur.execute(
            "SELECT category, prepared, consumed FROM inventory_categories ORDER BY category"
        )
        rows = []
        for row in cur.fetchall():
            category, prepared, consumed = row["category"], row["prepared"], row["consumed"]
            rows.append({
                "category": category,
                "prepared": prepared,
                "consumed": consumed,
                "available": prepared - consumed,
                "wasted": wasted.get(category, 0),
            })
        return rows


def ornamental_count(conn: psycopg.Connection[DictRow]) -> int:
    """Total ornamental plates made (no material; just a counter)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(SUM(quantity), 0) AS total FROM plates "
            "WHERE plate_type = 'ORNAMENTAL' AND is_voided = false"
        )
        row = cur.fetchone()
        assert row is not None, "a bare SUM(...) aggregate always yields exactly one row"
        return row["total"]
