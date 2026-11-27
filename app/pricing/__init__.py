"""The pricing domain: the /precios tariff editor (routes.py) and the SQL for
the plate_type_prices table (repository.py).

Dependency rule: other code may import app.pricing. app.pricing imports
app.shared and app.auth; routes.py may also import app.web.helpers until phase
6 of docs/MIGRATION_PLAN.md. Stdlib and third-party libraries are always fine.
"""
