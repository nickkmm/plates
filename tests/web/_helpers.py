"""Shared setup helpers for tests/web/test_*.py route-integration tests --
moved out of the old tests/web/test_app.py (Fase 1d step 10 test-file
split) so test_login.py, test_review.py, test_inventory.py,
test_records.py, test_ingest.py and test_roles.py can all import them
without duplicating. Plain functions, not fixtures -- import them
explicitly, e.g. `from tests.web._helpers import make_row, _insert_order`."""

from __future__ import annotations

from datetime import date

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
    exist" setup calls -- see the identical helper (and its full rationale)
    in tests/core/db/_helpers.py."""
    return db.insert_order(
        conn,
        row=ExtractedRow(order_number=order_number, order_date=order_date, requested_by=requested_by,
                          plate_type="MOTO PARTICULAR"),
        customer_id=None,
        created_by=None,
    )
