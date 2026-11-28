"""The production domain: the /produccion board, order detail and status steps
(routes.py), the transition rules (service.py) and the SQL for
production_orders.production_status and production_order_events
(repository.py).

Dependency rule: other code may import app.production. app.production imports
app.shared, app.auth, app.orders (the orders data) and app.declarations
(the declarations data and the declaration-file constants of its routes),
and routes.py imports app.web.helpers where it
needs it until phase 6 of docs/MIGRATION_PLAN.md. Stdlib and third-party
libraries are always fine.
"""
