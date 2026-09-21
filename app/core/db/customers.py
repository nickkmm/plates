"""customers reads/writes.

requested_by (the intermediary typed on an order -- PTO.V, MUVA, etc.) and
the sale "customer" are the SAME entity (see docs/DATABASE.md): a customer
row is looked up/created by its canonical name, exactly the same canonical
form `customers.name`'s UNIQUE constraint and the Fase 1 backfill both use
(app.core.normalize.normalize_requested_by).

See app/core/db/__init__.py for the module-wide connection/transaction
contract that applies to every function here.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
from psycopg.rows import DictRow, class_row

from app.core.normalize import normalize_requested_by


@dataclass
class CustomerRecord:
    """One `customers` row -- returned by lookups. Consumed today by
    resolve_or_create_customer's own ON CONFLICT branch; get_customer/
    get_customer_by_name exist for Fase 3's /ingest autocomplete ("si el
    nombre tipeado matchea customers.name, se reutilizan automáticamente sus
    datos") and record_save's customer_id re-resolution, per the plan."""

    id: int
    name: str
    type: str | None
    document_type: str | None
    document_number: str | None
    document_relation: str | None
    phone: str | None
    email: str | None
    transit_license: str | None


_SELECT_COLUMNS = (
    "id, name, type, document_type, document_number, document_relation, "
    "phone, email, transit_license"
)


def get_customer(conn: psycopg.Connection[DictRow], customer_id: int) -> CustomerRecord | None:
    """Look up a customer by id (production_orders.customer_id)."""
    with conn.cursor(row_factory=class_row(CustomerRecord)) as cur:
        cur.execute(f"SELECT {_SELECT_COLUMNS} FROM customers WHERE id = %s", (customer_id,))
        return cur.fetchone()


def get_customer_by_name(conn: psycopg.Connection[DictRow], name: str) -> CustomerRecord | None:
    """Look up an existing customer by its CANONICAL name
    (normalize_requested_by(name)) -- customers.name always stores the
    canonical form, so this is an exact match against the canonicalized
    input, never ILIKE/fuzzy matching."""
    canonical = normalize_requested_by(name)
    with conn.cursor(row_factory=class_row(CustomerRecord)) as cur:
        cur.execute(f"SELECT {_SELECT_COLUMNS} FROM customers WHERE name = %s", (canonical,))
        return cur.fetchone()


def resolve_or_create_customer(conn: psycopg.Connection[DictRow], name: str) -> int:
    """Return the id of the customer canonically named `name`, creating a
    bare row (every attribute NULL) if none exists yet.

    Canonicalizes via normalize_requested_by BEFORE the lookup/insert -- the
    same key `customers.name`'s UNIQUE constraint and the Fase 1 backfill
    both use -- so 'PTOV'/'PTO DE VENTA'/'PTO.V' all resolve to the SAME
    row, never creating near-duplicate customers for spelling variants of
    one real intermediary (see normalize._REQUESTED_BY_MAP_RAW). Mirrors
    app.schema_migrate.backfill_customers' own
    "INSERT ... ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
    RETURNING id" idiom, which is safe under concurrent inserts -- a plain
    SELECT-then-INSERT would race.

    Only ever sets `name`: this does NOT accept/write customer attributes
    (type/document/phone/email/transit_license) -- capturing those for a
    brand-new customer is a later phase's concern (the /ingest "cliente"
    fieldset), not this function's job.
    """
    canonical = normalize_requested_by(name)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO customers (name) VALUES (%s)
            ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            (canonical,),
        )
        row = cur.fetchone()
        assert row is not None, "INSERT ... RETURNING id always yields a row"
        return row["id"]
