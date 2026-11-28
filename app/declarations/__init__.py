"""The declarations domain: the staff review screens (/declaraciones, in
routes.py), the public sworn-declaration form (/declaracion, in
public_routes.py) and its anti-spam checks (antispam.py), the submission and
review rules (service.py), the printable PDF (pdf.py) and the SQL for the
declarations and declaration_cards tables (repository.py).

Dependency rule: other code may import app.declarations. app.declarations
imports app.shared, app.auth, app.customers, app.orders and app.production,
and the routes import app.web.csrf until phase 6 of docs/MIGRATION_PLAN.md.
Stdlib and third-party libraries are always fine.
"""
