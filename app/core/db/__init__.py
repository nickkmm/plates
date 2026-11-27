"""Database access for plate-production ingestion (psycopg 3), one module per table.

The connection pool (`get_conn()`), its environment variables, the dict_row
row factory and the transaction contract every function here follows (it
receives an open `conn` and never commits or rolls back itself) live in
app/shared/db.py; `get_conn` is re-exported below so `db.get_conn()` keeps
working.

This package replaces what used to be a single flat db.py, split by table:
    orders.py     -- production_orders reads/writes, the shared order-summary
                     row shape (OrderSummary).
    plates.py     -- plates reads/writes (plus distinct_cities, by symmetry
                     with orders.distinct_requested_by).
    declarations.py -- declarations + declaration_cards (sworn declarations of
                     the public form, one declarant and many ownership cards)
                     reads/writes; the decision rules live in app/core/declarations.py.
The users and roles tables are read and written by app/auth/repository/.
The customers, ownership_cards, plate_type_prices, blank_preparations and
inventory_categories tables live in app/customers/repository/,
app/pricing/repository.py and app/inventory/repository.py, and the SQL for
production_orders.production_status and production_order_events lives in
app/production/repository.py.
Every public function each submodule exposes is re-exported below,
explicitly (not `import *`), so existing call sites (`db.insert_order(...)`,
etc.) keep working completely unchanged.
"""

from __future__ import annotations

from app.core.db.declarations import (
    DeclarationCardRecord,
    DeclarationRecord,
    count_recent_by_ip,
    get_declaration,
    insert_declaration,
    insert_declaration_card,
    list_cards_for_declarations,
    list_declaration_cards,
    list_declarations,
    list_declarations_by_production_order,
    set_declaration_card_links,
    set_declaration_notarized_received,
    set_declaration_production_order,
    set_declaration_review,
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
    last_fabrication_date,
    relink_plates,
    resolve_plate,
    review_rows,
    search_plates_by_number,
    update_plate,
    update_plate_full,
)
from app.shared.db import get_conn

__all__ = [
    "DeclarationCardRecord",
    "DeclarationRecord",
    "OrderSummary",
    "count_recent_by_ip",
    "delete_order",
    "delete_plate",
    "distinct_cities",
    "distinct_requested_by",
    "existing_plate_numbers",
    "find_orders_by_number",
    "find_orders_for_plate",
    "get_conn",
    "get_declaration",
    "insert_declaration",
    "insert_declaration_card",
    "insert_order",
    "insert_plate",
    "last_fabrication_date",
    "list_cards_for_declarations",
    "list_declaration_cards",
    "list_declarations",
    "list_declarations_by_production_order",
    "order_detail",
    "relink_plates",
    "recent_orders",
    "resolve_plate",
    "review_rows",
    "search_orders_by_number",
    "search_plates_by_number",
    "set_declaration_card_links",
    "set_declaration_notarized_received",
    "set_declaration_production_order",
    "set_declaration_review",
    "update_order",
    "update_plate",
    "update_plate_full",
]
