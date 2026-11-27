"""The inventory domain: the /inventory cards and ALISTAMIENTO form (routes.py)
and the SQL for the blank_preparations and inventory_categories tables
(repository.py).

Dependency rule: other code may import app.inventory. app.inventory imports
app.shared and app.auth; routes.py also imports app.web.helpers until phase 6
of docs/MIGRATION_PLAN.md. Stdlib and third-party libraries are always fine.
"""
