"""plate_type_prices reads/writes -- suggested tariffs per plate TYPE.

**Namespace warning** (see docs/DATABASE.md): plate_type_prices.plate_type is
one of the 7 GRANULAR normalize.PLATE_TYPES values (FK -> plate_types), never
one of the 3 inventory_categories.category MATERIALS -- confusing the two
would leave ORNAMENTAL unable to have a price and give REMOLQUE the price of
CARRO PÚBLICO. Every function here operates on the plate_type namespace only.

plate_type_prices starts EMPTY on purpose (not seeded in init.sql) -- a type
with no row yet is "sin tarifa", never a silent 0. See app/core/db/__init__.py
for the module-wide connection/transaction contract that applies here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import psycopg
from psycopg.rows import DictRow

from app.core.normalize import PLATE_TYPES


@dataclass
class PriceRow:
    """One row for the /precios table (Fase 5a): one entry per PLATE_TYPES
    value, even when plate_type_prices has no row for it yet -- unit_price/
    updated_at/updated_by are then all None ("sin tarifa"), never a silent
    0."""

    plate_type: str
    unit_price: Decimal | None
    updated_at: datetime | None
    updated_by: str | None


def current_prices(conn: psycopg.Connection[DictRow]) -> dict[str, Decimal]:
    """{plate_type: unit_price} for every plate_type that currently HAS a row
    in plate_type_prices -- a type with no configured tariff yet is simply
    absent from the dict (never a KeyError-prone default, never a 0). Callers
    (ingestion.py's per-batch unit_price snapshot, db/plates.py's re-snapshot
    rule, /precios) use .get(plate_type) and treat a miss as "no suggested
    price yet", same as an explicit NULL.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT plate_type, unit_price FROM plate_type_prices")
        return {r["plate_type"]: r["unit_price"] for r in cur.fetchall()}


def price_list(conn: psycopg.Connection[DictRow]) -> list[PriceRow]:
    """One PriceRow per PLATE_TYPES entry, in PLATE_TYPES' own canonical
    order -- looked up in Python (not a LEFT JOIN) so the result order never
    depends on plate_type_prices' insertion order, and a type with zero rows
    still appears (marked "sin tarifa" by the caller/template)."""
    with conn.cursor() as cur:
        cur.execute("SELECT plate_type, unit_price, updated_at, updated_by FROM plate_type_prices")
        by_type = {r["plate_type"]: r for r in cur.fetchall()}
    return [
        PriceRow(
            plate_type=pt,
            unit_price=by_type[pt]["unit_price"] if pt in by_type else None,
            updated_at=by_type[pt]["updated_at"] if pt in by_type else None,
            updated_by=by_type[pt]["updated_by"] if pt in by_type else None,
        )
        for pt in PLATE_TYPES
    ]


def set_price(
    conn: psycopg.Connection[DictRow], plate_type: str, unit_price: Decimal, updated_by: str | None
) -> None:
    """Create or update the suggested tariff for one plate_type (admin-only,
    /precios -- Fase 5a).

    Validates `plate_type` against normalize.PLATE_TYPES in Python before it
    reaches the DB -- the real FK (plate_type_prices.plate_type ->
    plate_types.plate_type, Fase 1b) is the safety net, but a Spanish
    ValueError here is a far better error message than a bare FK-violation
    traceback, same "double safety net" pattern already used for
    plates.plate_type. ON CONFLICT so the same call works whether or not a
    row already exists (the table starts empty on purpose). An edit always
    moves updated_at forward (`now()`), not just on first creation.
    """
    if plate_type not in PLATE_TYPES:
        raise ValueError(f"'{plate_type}' no es un tipo de placa válido.")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO plate_type_prices (plate_type, unit_price, updated_by)
            VALUES (%s, %s, %s)
            ON CONFLICT (plate_type) DO UPDATE
            SET unit_price = EXCLUDED.unit_price, updated_by = EXCLUDED.updated_by, updated_at = now()
            """,
            (plate_type, unit_price, updated_by),
        )
