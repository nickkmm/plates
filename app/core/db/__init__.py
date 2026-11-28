"""Database access for plate-production ingestion (psycopg 3), one module per table.

The connection pool (`get_conn()`), its environment variables, the dict_row
row factory and the transaction contract every function here follows (it
receives an open `conn` and never commits or rolls back itself) live in
app/shared/db.py; `get_conn` is re-exported below so `db.get_conn()` keeps
working.

This package replaces what used to be a single flat db.py, split by table:
    declarations.py -- declarations + declaration_cards (sworn declarations of
                     the public form, one declarant and many ownership cards)
                     reads/writes; the decision rules live in app/core/declarations.py.
The production_orders and plates SQL now lives in app/orders/repository/
(orders.py, plates.py), and relink_plates lives in
app/customers/repository/ownership_cards.py.
The users and roles tables are read and written by app/auth/repository/.
The customers, ownership_cards, plate_type_prices, blank_preparations and
inventory_categories tables live in app/customers/repository/,
app/pricing/repository.py and app/inventory/repository.py, and the SQL for
production_orders.production_status and production_order_events lives in
app/production/repository.py.
Every public function each submodule exposes is re-exported below,
explicitly (not `import *`), so existing call sites (`db.insert_declaration(...)`,
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
from app.shared.db import get_conn

__all__ = [
    "DeclarationCardRecord",
    "DeclarationRecord",
    "count_recent_by_ip",
    "get_conn",
    "get_declaration",
    "insert_declaration",
    "insert_declaration_card",
    "list_cards_for_declarations",
    "list_declaration_cards",
    "list_declarations",
    "list_declarations_by_production_order",
    "set_declaration_card_links",
    "set_declaration_notarized_received",
    "set_declaration_production_order",
    "set_declaration_review",
]
