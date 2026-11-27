"""SQL for the orders domain, one module per table.

    orders.py -- production_orders reads/writes and the shared order-summary
                 row shape (OrderSummary).
    plates.py -- plates reads/writes (plus distinct_cities, by symmetry with
                 orders.distinct_requested_by).

relink_plates, which recomputes plates.ownership_card_id when an order's
customer or a customer's type changes, lives in
app/customers/repository/ownership_cards.py so the dependency only points
app.orders -> app.customers.

See app/shared/db.py for the connection pool and the transaction contract
every function here follows (it receives an open `conn` and never commits or
rolls back itself). Every public name is re-exported below, explicitly (not
`import *`), so callers write `orders_repository.insert_order(conn, ...)`.
"""

from __future__ import annotations

from app.orders.repository.orders import (
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
from app.orders.repository.plates import (
    delete_plate,
    distinct_cities,
    existing_plate_numbers,
    find_orders_for_plate,
    insert_plate,
    last_fabrication_date,
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
    "insert_order",
    "insert_plate",
    "last_fabrication_date",
    "order_detail",
    "recent_orders",
    "resolve_plate",
    "review_rows",
    "search_orders_by_number",
    "search_plates_by_number",
    "update_order",
    "update_plate",
    "update_plate_full",
]
