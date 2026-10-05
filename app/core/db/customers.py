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
    address: str | None
    neighborhood: str | None
    municipality: str | None


_SELECT_COLUMNS = (
    "id, name, type, document_type, document_number, document_relation, "
    "phone, email, address, neighborhood, municipality"
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
    address: str | None = None, neighborhood: str | None = None,
    municipality: str | None = None,
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
    on the INSERT branch for a genuinely brand-new customer, same as always.
    For an EXISTING customer, the ON CONFLICT branch fills in ONLY the
    attributes that are still NULL on that row (COALESCE(existing, new)) --
    it never overwrites an attribute the customer already has a value for,
    even if the caller passes a different one (see
    test_existing_customer_attributes_are_never_overwritten). This is what
    lets the 191 customers created by the pre-POS historical backfill (all
    NULL attributes -- the old Excel ledger never captured them) get their
    real data filled in naturally the next time someone types it while
    placing an order for them, instead of staying blank forever with no way
    to fix them short of a dedicated customers admin screen (see
    update_customer below, Fase 6) or a manual DB edit. ingestion.py's
    calls (name only, via
    process_rows/replace_order) and record_save's call (also name only) pass
    every keyword as NULL, so COALESCE(existing, NULL) = existing for them --
    a true no-op, unchanged from before.
    """
    canonical = normalize_requested_by(name)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO customers (
                name, type, document_type, document_number, document_relation,
                phone, email, address, neighborhood, municipality
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET
                name = EXCLUDED.name,
                type = COALESCE(customers.type, EXCLUDED.type),
                document_type = COALESCE(customers.document_type, EXCLUDED.document_type),
                document_number = COALESCE(customers.document_number, EXCLUDED.document_number),
                document_relation = COALESCE(customers.document_relation, EXCLUDED.document_relation),
                phone = COALESCE(customers.phone, EXCLUDED.phone),
                email = COALESCE(customers.email, EXCLUDED.email),
                address = COALESCE(customers.address, EXCLUDED.address),
                neighborhood = COALESCE(customers.neighborhood, EXCLUDED.neighborhood),
                municipality = COALESCE(customers.municipality, EXCLUDED.municipality)
            RETURNING id
            """,
            (canonical, type, document_type, document_number, document_relation,
             phone, email, address, neighborhood, municipality),
        )
        row = cur.fetchone()
        assert row is not None, "INSERT ... RETURNING id always yields a row"
        return row["id"]


def update_customer(
    conn: psycopg.Connection[DictRow], customer_id: int, *, name: str,
    type: str | None, document_type: str | None, document_number: str | None,
    document_relation: str | None, phone: str | None, email: str | None,
    address: str | None, neighborhood: str | None, municipality: str | None,
) -> None:
    """Overwrite every attribute of an existing customer -- the deliberate
    admin-edit counterpart to resolve_or_create_customer's passive,
    never-overwrite-an-existing-value backfill above. This is the ONE place
    in the app that may knowingly replace a customer's already-stored data
    (fixing a typo, correcting a wrong phone, filling in one of the 191
    pre-POS historical rows by hand) -- everywhere else (ingestion.py,
    record_save) only ever resolves/creates by name, never rewrites
    attributes (including the person fields address/neighborhood/
    municipality, which only apply to GESTOR/CLIENTE -- the caller, see
    app.web.helpers.customer_fields_from_form, already passes None for the
    other types). Used by the /customers admin screen (Fase 6).

    `name` is expected already-canonical (normalize_requested_by applied by
    the caller, same as resolve_or_create_customer) -- passed through
    as-is here, not re-normalized, so this function has one single
    responsibility (write exactly what it's given).

    No uniqueness pre-check -- same "let the DB constraint be the source of
    truth" convention as create_user/resolve_or_create_customer: renaming
    to a name that collides with a DIFFERENT existing customer raises
    psycopg.errors.UniqueViolation, which the web route catches to show a
    friendly message instead of a 500.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE customers SET
                name = %s, type = %s, document_type = %s, document_number = %s,
                document_relation = %s, phone = %s, email = %s,
                address = %s, neighborhood = %s, municipality = %s
            WHERE id = %s
            """,
            (name, type, document_type, document_number, document_relation,
             phone, email, address, neighborhood, municipality, customer_id),
        )
