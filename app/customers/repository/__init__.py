"""SQL for the customers domain, one module per table.

    customers.py       -- customers reads/writes (Fase 2).
    ownership_cards.py -- ownership_cards ("tarjeta de propiedad") reads/writes
                          and the find_card_conflicts classifier.

See app/shared/db.py for the connection pool and the transaction contract
every function here follows (it receives an open `conn` and never commits or
rolls back itself). Every public name is re-exported below, explicitly (not
`import *`), so callers write `customers_repository.list_customers(conn)`.
"""

from __future__ import annotations

from app.customers.repository.customers import (
    CustomerRecord,
    get_customer,
    get_customer_by_name,
    list_customers,
    resolve_or_create_customer,
    update_customer,
)
from app.customers.repository.ownership_cards import (
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

__all__ = [
    "CARD_LINK_EXCLUDED_CUSTOMER_TYPES",
    "CARD_STATUS_DATA_MISMATCH",
    "CARD_STATUS_NEW",
    "CARD_STATUS_OTHER_OWNER",
    "CARD_STATUS_SAME_CARD",
    "CardLookup",
    "CustomerRecord",
    "OwnershipCardRecord",
    "classify_card_lookup",
    "create_ownership_card",
    "find_card_conflicts",
    "get_customer",
    "get_customer_by_name",
    "get_ownership_card",
    "get_ownership_card_by_plate_number",
    "get_ownership_card_by_transit_license",
    "list_customers",
    "list_ownership_cards_for_customer",
    "reassign_ownership_card",
    "resolve_card_id_for_plate",
    "resolve_or_create_customer",
    "update_customer",
]
