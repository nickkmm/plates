"""The customers domain: the /customers master-data screen (routes.py) and the
SQL for the customers and ownership_cards tables (app/customers/repository/).

Dependency rule: other code may import app.customers. app.customers imports
app.shared and app.auth; until phase 6 of docs/MIGRATION_PLAN.md, routes.py
also imports app.web.helpers. It never imports app.orders: relink_plates, which
update_customer calls, lives in repository/ownership_cards.py. Stdlib and
third-party libraries are always fine.
"""
