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
    inventory.py  -- blank_preparations / inventory_categories reads/writes.
    customers.py  -- customers reads/writes (Fase 2).
    ownership_cards.py -- ownership_cards ("tarjeta de propiedad") reads/writes
                     and the find_card_conflicts classifier.
    declarations.py -- declarations + declaration_cards (sworn declarations of
                     the public form, one declarant and many ownership cards)
                     reads/writes; the decision rules live in app/core/declarations.py.
    pricing.py    -- plate_type_prices reads/writes (Fase 2).
    production.py -- production_orders.production_status and
                     production_order_events reads/writes; the transition
                     rules live in app/core/production.py.
The users and roles tables are read and written by app/auth/repository/.
Every public function each submodule exposes is re-exported below,
explicitly (not `import *`), so existing call sites (`db.insert_order(...)`,
etc.) keep working completely unchanged.
"""

from __future__ import annotations

from app.core.db.customers import (
    CustomerRecord,
    get_customer,
    get_customer_by_name,
    list_customers,
    resolve_or_create_customer,
    update_customer,
)
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
from app.core.db.ownership_cards import (
    CARD_LINK_EXCLUDED_CUSTOMER_TYPES,
    CARD_STATUS_DATA_MISMATCH,
    CARD_STATUS_NEW,
    CARD_STATUS_OTHER_OWNER,
    CARD_STATUS_SAME_CARD,
    CardLookup,
    OwnershipCardRecord,
    classify_card_lookup,
    create_ownership_card,
    find_card_conflicts,
    get_ownership_card,
    get_ownership_card_by_plate_number,
    get_ownership_card_by_transit_license,
    list_ownership_cards_for_customer,
    reassign_ownership_card,
    resolve_card_id_for_plate,
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
from app.core.db.pricing import PriceRow, current_prices, price_list, set_price
from app.core.db.production import (
    ACTIVE_STATUSES,
    PRODUCTION_STATUSES,
    STATUS_DELIVERED,
    STATUS_IN_PROGRESS,
    STATUS_PENDING,
    STATUS_READY,
    ProductionEvent,
    ProductionOrderSummary,
    count_by_production_status,
    get_production_status,
    list_production_events,
    list_production_orders,
    production_statuses_for_orders,
    set_production_status,
)
from app.shared.db import get_conn

__all__ = [
    "ACTIVE_STATUSES",
    "CARD_LINK_EXCLUDED_CUSTOMER_TYPES",
    "CARD_STATUS_DATA_MISMATCH",
    "CARD_STATUS_NEW",
    "CARD_STATUS_OTHER_OWNER",
    "CARD_STATUS_SAME_CARD",
    "CardLookup",
    "CustomerRecord",
    "DeclarationCardRecord",
    "DeclarationRecord",
    "OrderSummary",
    "OwnershipCardRecord",
    "PRODUCTION_STATUSES",
    "PriceRow",
    "ProductionEvent",
    "ProductionOrderSummary",
    "STATUS_DELIVERED",
    "STATUS_IN_PROGRESS",
    "STATUS_PENDING",
    "STATUS_READY",
    "classify_card_lookup",
    "count_by_production_status",
    "count_recent_by_ip",
    "create_ownership_card",
    "current_prices",
    "delete_order",
    "delete_plate",
    "distinct_cities",
    "distinct_requested_by",
    "existing_plate_numbers",
    "find_card_conflicts",
    "find_orders_by_number",
    "find_orders_for_plate",
    "get_conn",
    "get_customer",
    "get_customer_by_name",
    "get_declaration",
    "get_ownership_card",
    "get_ownership_card_by_plate_number",
    "get_ownership_card_by_transit_license",
    "get_production_status",
    "insert_blank_preparation",
    "insert_declaration",
    "insert_declaration_card",
    "insert_order",
    "insert_plate",
    "inventory_view",
    "last_fabrication_date",
    "list_customers",
    "list_cards_for_declarations",
    "list_declaration_cards",
    "list_declarations",
    "list_declarations_by_production_order",
    "list_ownership_cards_for_customer",
    "list_production_events",
    "list_production_orders",
    "order_detail",
    "ornamental_count",
    "price_list",
    "production_statuses_for_orders",
    "reassign_ownership_card",
    "relink_plates",
    "resolve_card_id_for_plate",
    "recalc_inventory",
    "recent_orders",
    "resolve_or_create_customer",
    "resolve_plate",
    "review_rows",
    "search_orders_by_number",
    "search_plates_by_number",
    "set_declaration_card_links",
    "set_declaration_notarized_received",
    "set_declaration_production_order",
    "set_declaration_review",
    "set_price",
    "set_production_status",
    "update_customer",
    "update_order",
    "update_plate",
    "update_plate_full",
]
