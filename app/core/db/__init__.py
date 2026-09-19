"""Database access for plate-production ingestion (psycopg 3).

Connection settings come from environment variables (same ones the Postgres
container uses, plus DB_HOST/DB_PORT for where to reach it):
    DB_HOST (default "db"), DB_PORT (default "5432"),
    POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD

Connections are opened with row_factory=dict_row, so every SELECT below comes
back as a dict keyed by column name (or list of such dicts) -- never a plain
positional tuple.

**Transaction contract**: every function in this package receives an already-
open connection (`conn`) and never calls commit()/rollback() itself -- whoever
calls it is the one that opened the connection (`with db.get_conn() as conn:
...`) and is responsible for its transaction. This is deliberate: passing
`conn` explicitly, instead of hiding it behind an interface, keeps visible in
every function's signature who actually owns the commit -- see
docs/CORE.md's "Contrato de transacción" for the real bug (several functions
in app/core/ingestion.py each opening their own connection) that made this
worth writing down instead of leaving implicit.

This package replaces what used to be a single flat db.py, split by table:
    orders.py     -- production_orders reads/writes, the shared order-summary
                     row shape (OrderSummary).
    plates.py     -- plates reads/writes (plus distinct_cities, by symmetry
                     with orders.distinct_requested_by).
    inventory.py  -- blank_preparations / inventory_categories reads/writes.
Every public function each submodule exposes is re-exported below,
explicitly (not `import *`), so existing call sites (`db.insert_order(...)`,
etc.) keep working completely unchanged.
"""

from __future__ import annotations

import os

import psycopg
from psycopg.rows import DictRow, dict_row

from app.core.db.inventory import (
    insert_blank_preparation,
    inventory_view,
    ornamental_count,
    recalc_inventory,
)
from app.core.db.orders import (
    OrderSummary,
    delete_order,
    distinct_requested_by,
    find_orders_by_number,
    insert_order,
    order_detail,
    recent_orders,
    search_orders_by_number,
    update_order,
)
from app.core.db.plates import (
    delete_plate,
    distinct_cities,
    existing_plate_numbers,
    find_orders_for_plate,
    insert_plate,
    resolve_plate,
    review_rows,
    search_plates_by_number,
    update_plate,
    update_plate_full,
)

__all__ = [
    "OrderSummary",
    "delete_order",
    "delete_plate",
    "distinct_cities",
    "distinct_requested_by",
    "existing_plate_numbers",
    "find_orders_by_number",
    "find_orders_for_plate",
    "get_conn",
    "insert_blank_preparation",
    "insert_order",
    "insert_plate",
    "inventory_view",
    "order_detail",
    "ornamental_count",
    "recalc_inventory",
    "recent_orders",
    "resolve_plate",
    "review_rows",
    "search_orders_by_number",
    "search_plates_by_number",
    "update_order",
    "update_plate",
    "update_plate_full",
]


def get_conn() -> psycopg.Connection[DictRow]:
    return psycopg.connect(
        host=os.environ.get("DB_HOST", "db"),
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        row_factory=dict_row,
    )
