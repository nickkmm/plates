"""The customers domain: the /customers master-data screen (routes.py) and the
SQL for the customers and ownership_cards tables (app/customers/repository/).

Dependency rule: other code may import app.customers. app.customers imports
app.shared and app.auth; until they move, it also imports app.core.db.plates
(update_customer relinks plates) and app.web.helpers (routes.py, until phase 6
of docs/MIGRATION_PLAN.md). Stdlib and third-party libraries are always fine.
"""
