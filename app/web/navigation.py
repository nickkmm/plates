"""The nav bar's entries, in display order, each gated by one permission code.

_nav.html renders the entries the current user's permissions open (the
`nav_items` context variable in app.py), and auth.landing_url() sends a user
to the first of them.
"""

from __future__ import annotations

# (endpoint, label, permission, active_endpoints). Later tasks append their
# screens' entries here.
NAV_ITEMS: list[tuple[str, str, str, tuple[str, ...]]] = [
    ("ingest.ingest", "Registrar", "orders.capture", ("ingest.ingest", "ingest.ingest_pdf")),
    (
        "cash_summary.cuadre",
        "Cuadre",
        "cash_summary.view",
        ("cash_summary.cuadre", "cash_summary.cuadre_pdf"),
    ),
    (
        "declarations.declarations",
        "Declaraciones",
        "declarations.review",
        ("declarations.declarations", "declarations.declaration_detail"),
    ),
    ("review.review", "Revisión", "duplicates.review", ("review.review",)),
    ("inventory.inventory", "Inventario", "inventory.manage", ("inventory.inventory",)),
    ("pricing.precios", "Precios", "pricing.manage", ("pricing.precios",)),
    (
        "customers.customers",
        "Clientes",
        "customers.manage",
        ("customers.customers", "customers.customer_detail"),
    ),
    ("users.users", "Usuarios", "users.manage", ("users.users", "users.user_new", "users.user_edit")),
    ("records.records", "Registros", "records.view", ("records.records", "records.record_detail")),
]
