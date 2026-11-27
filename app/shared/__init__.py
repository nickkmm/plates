"""Code every domain needs: the connection pool (db), normalize, timezones,
models and uploads.

The rule: a module in app/shared/ depends on no domain package -- not
app.auth, app.customers, app.pricing, app.inventory, app.production,
app.orders, app.cash_summary or app.declarations, nor today's app.core and
app.web, which still hold domain code. It may import only the standard
library, third-party libraries and other app.shared modules.
"""
