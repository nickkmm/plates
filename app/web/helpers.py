"""Small helpers shared by 2+ blueprints -- everything here is used by both
an /ingest-side route and a /records-side route (or by nearly all of them),
so it doesn't belong to either blueprint specifically.

Route-local helpers used by only ONE future blueprint (e.g.
_rows_from_form, _sticky_lines_from_form, _form_from_pedido) stay with
their routes instead of moving here -- see the Fase 1d plan.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.core import db
from app.core.normalize import INVENTORY_CATEGORIES, PLATE_TYPES

# Plate TYPES are what the user picks per plate (granular, 5). Inventory
# CATEGORIES are the 3 materials those types discount from (público merged).
FORM_PLATE_TYPES = list(PLATE_TYPES)
INVENTORY_CATS = list(INVENTORY_CATEGORIES)

# The container runs in UTC; compute "today" in Colombia so the date default
# and the future-date validation use local time, not UTC.
BOGOTA = ZoneInfo("America/Bogota")


def today_iso() -> str:
    return datetime.now(BOGOTA).date().isoformat()


def _options(conn):
    return db.distinct_requested_by(conn), db.distinct_cities(conn)


def _padded(values: list[str], size: int) -> list[str]:
    """Pad a per-line field list to `size` with blanks.

    The line zips are strict=False, so a request that omits an optional per-line
    field (city) would otherwise truncate the zip and silently drop plate lines.
    """
    return values + [""] * (size - len(values))


def _validate_order_header(*, requested_by: str, order_date: str) -> None:
    """Order-level validations shared by every door that saves a header
    (brand-new /ingest, and editing an existing order's header)."""
    if any(ch.isdigit() for ch in requested_by):
        raise ValueError("'Solicitado por' no debería contener números (parece una placa). Revisá ese campo.")
    if order_date and order_date > today_iso():
        raise ValueError(f"La fecha {order_date} es futura. Corregí la fecha.")


def format_cop(value: Decimal | None) -> str:
    """Colombian peso display formatting: thousands separated by '.', no
    decimals (e.g. Decimal('1954000') -> '$1.954.000'). None -> '—', never a
    silent '$0' -- callers (unit_price is genuinely nullable: an unpriced
    plate_type) must be able to tell "no price" from "free" at a glance.
    Display only: never used for the underlying Decimal math (unit_price *
    quantity), which stays exact/unrounded wherever it's actually computed.
    """
    if value is None:
        return "—"
    return f"${value:,.0f}".replace(",", ".")
