"""The production domain: the /produccion board, order detail and status steps
(routes.py), the transition rules (service.py) and the SQL for
production_orders.production_status and production_order_events
(repository.py).

Dependency rule: other code may import app.production. app.production imports
app.shared and app.auth; until they move, it also imports app.core.db (the
orders and declarations data) and app.web.blueprints.declarations (the
declaration-file constants), and routes.py imports app.web.helpers where it
needs it until phase 6 of docs/MIGRATION_PLAN.md. Stdlib and third-party
libraries are always fine.
"""
