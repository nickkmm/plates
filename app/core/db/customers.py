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


def list_customers(conn: psycopg.Connection[DictRow]) -> list[CustomerRecord]:
    """Every customer, alphabetically -- embedded whole into /ingest (Fase 3)
    so its JS can auto-fill the 'cliente' fieldset the instant a typed name
    matches an existing customer, the same "embed the small lookup list"
    pattern already used for distinct_requested_by/distinct_cities (this app
    has no JSON/AJAX endpoints anywhere else)."""
    with conn.cursor(row_factory=class_row(CustomerRecord)) as cur:
        cur.execute(f"SELECT {_SELECT_COLUMNS} FROM customers ORDER BY name")
        return cur.fetchall()


def resolve_or_create_customer(
    conn: psycopg.Connection[DictRow], name: str, *,
    type: str | None = None, document_type: str | None = None, document_number: str | None = None,
    document_relation: str | None = None, phone: str | None = None, email: str | None = None,
    transit_license: str | None = None,
) -> int:
    """Return the id of the customer canonically named `name`, creating a
    row if none exists yet.

    Canonicalizes via normalize_requested_by BEFORE the lookup/insert -- the
    same key `customers.name`'s UNIQUE constraint and the Fase 1 backfill
    both use -- so 'PTOV'/'PTO DE VENTA'/'PTO.V' all resolve to the SAME
    row, never creating near-duplicate customers for spelling variants of
    one real intermediary (see normalize._REQUESTED_BY_MAP_RAW). Mirrors
    app.schema_migrate.backfill_customers' own
    "INSERT ... ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
    RETURNING id" idiom, which is safe under concurrent inserts -- a plain
    SELECT-then-INSERT would race.

    The keyword attributes (Fase 3's /ingest "cliente" fieldset) are written
    ONLY on the INSERT branch, i.e. only for a genuinely brand-new customer
    -- the ON CONFLICT branch still only ever touches `name` (a no-op
    "rewrite it to itself", purely so RETURNING id fires on the existing
    row). This is deliberate, not an oversight: per the plan's B5, editing
    an order may only change WHICH customer it points to, never rewrite an
    existing customer's own stored attributes (that's out of scope, deferred
    to a future dedicated customers screen) -- ingestion.py's calls (name
    only, via process_rows/replace_order) and record_save's call (also name
    only, per B5) both keep working unchanged against this same signature.
    """
    canonical = normalize_requested_by(name)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO customers (
                name, type, document_type, document_number, document_relation,
                phone, email, transit_license
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            (canonical, type, document_type, document_number, document_relation,
             phone, email, transit_license),
        )
        row = cur.fetchone()
        assert row is not None, "INSERT ... RETURNING id always yields a row"
        return row["id"]
