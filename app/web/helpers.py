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
from app.core.normalize import (
    CUSTOMER_TYPES,
    DOCUMENT_RELATIONS,
    DOCUMENT_TYPES,
    INVENTORY_CATEGORIES,
    PLATE_TYPES,
    normalize_choice,
)

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


def customer_fields_from_form(form) -> dict[str, str | None]:
    """Customer attributes typed in a 'cliente' fieldset, keyed to match
    db.resolve_or_create_customer's/db.update_customer's kwargs (name is
    handled separately by each caller). Shared by /ingest (Fase 3's
    "cliente" fieldset) and /customers (the admin edit screen, Fase 6) --
    moved here rather than living in either blueprint once a second one
    needed it.

    Per-type applicability: TRÁNSITO and EMPRESA keep only name, document
    type/number, phone and email. document_relation (Propietario/Poseedor)
    is kept only for CLIENTE; address/neighborhood/municipality only for
    GESTOR or CLIENTE. Everything else is forced back to None here -- the
    HTML already disables those inputs client-side so a browser never sends
    them, but a raw POST (curl, a modified request) could still carry a
    stale value, so this is enforced again here rather than trusted from
    the client alone.
    """
    customer_type = normalize_choice(form.get("customer_type"), CUSTOMER_TYPES)
    document_type = normalize_choice(form.get("customer_document_type"), DOCUMENT_TYPES)
    document_relation = normalize_choice(form.get("customer_document_relation"), DOCUMENT_RELATIONS)
    if customer_type != "CLIENTE":
        document_relation = None
    has_person_fields = customer_type in ("GESTOR", "CLIENTE")

    def _person_field(key: str) -> str | None:
        if not has_person_fields:
            return None
        return (form.get(key) or "").strip() or None

    return {
        "type": customer_type,
        "document_type": document_type,
        "document_number": (form.get("customer_document_number") or "").strip() or None,
        "document_relation": document_relation,
        "phone": (form.get("customer_phone") or "").strip() or None,
        "email": (form.get("customer_email") or "").strip() or None,
        "address": _person_field("customer_address"),
        "neighborhood": _person_field("customer_neighborhood"),
        "municipality": _person_field("customer_municipality"),
    }


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
