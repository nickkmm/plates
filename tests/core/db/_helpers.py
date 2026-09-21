"""Shared setup helpers for tests/core/db/test_*.py -- moved out of the old
tests/core/test_db.py (Fase 1d step 10 test-file split). Plain functions,
not fixtures -- import them explicitly. These call db.py's own
insert_order/insert_plate for setup -- deliberately bypassing
app.core.ingestion and app.core.normalize -- so a failure in a test that
uses them points at db.py itself, not at normalization or the ingestion
pipeline (covered elsewhere)."""

from __future__ import annotations

from datetime import date

from psycopg.rows import tuple_row

from app.core import db
from app.core.models import ExtractedRow


def make_row(**overrides: object) -> ExtractedRow:
    defaults: dict[str, object] = {
        "order_number": "1",
        "order_date": date(2026, 1, 1),
        "requested_by": "PEDRO",
        "plate_type": "CARRO PARTICULAR",
    }
    defaults.update(overrides)
    return ExtractedRow(**defaults)


def _insert_order(conn, *, order_number: str, order_date, requested_by: str) -> int:
    """Back-compat shim for this file's many "just need an order header to
    exist" setup calls, written before Fase 2 grew db.insert_order's real
    signature to (row: ExtractedRow, customer_id, created_by) -- see
    app/core/db/orders.py. Builds the minimal ExtractedRow insert_order
    actually needs (plate_type is required by the model but irrelevant to
    production_orders -- insert_order never reads it) so none of this file's
    order-header-only test setup has to change. Tests of insert_order's OWN
    behavior (TestInsertOrder below) call db.insert_order directly instead.
    """
    return db.insert_order(
        conn,
        row=ExtractedRow(order_number=order_number, order_date=order_date, requested_by=requested_by,
                          plate_type="MOTO PARTICULAR"),
        customer_id=None,
        created_by=None,
    )


def _insert_plate_get_id(conn, order_id: int, **row_kwargs: object) -> int:
    """Insert a plate via db.insert_plate and return its new id (insert_plate
    itself returns None -- there's no RETURNING id in its INSERT)."""
    is_dup = bool(row_kwargs.pop("is_possible_duplicate", False))
    row = make_row(**row_kwargs)
    db.insert_plate(conn, order_id=order_id, row=row, is_possible_duplicate=is_dup)
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT id FROM plates WHERE order_id = %s ORDER BY id DESC LIMIT 1", (order_id,))
        return cur.fetchone()[0]
