"""The orders domain: the point of sale (/ingest, /ingest/pdf), the records
screen (/records) and the duplicates review (/review) in routes/, the
ingestion pipeline (service.py), the pedido parsers (pdf_extract.py,
xlsx_extract.py, pedido_upload.py) and the SQL for the production_orders and
plates tables (repository/).

Dependency rule: other code may import app.orders. app.orders imports
app.shared, app.auth, app.customers, app.pricing, app.inventory and
app.declarations (the declarations data), and the routes import app.web.helpers until phase 6 of
docs/MIGRATION_PLAN.md. Stdlib and third-party libraries are always fine.
"""
