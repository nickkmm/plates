"""The cash summary domain: the /cuadre daily cash-reconciliation report and
its PDF export (routes.py) and the aggregation behind them (service.py).

Dependency rule: other code may import app.cash_summary. app.cash_summary
imports app.shared and app.auth; routes.py also imports app.web.helpers until
phase 6 of docs/MIGRATION_PLAN.md. Stdlib and third-party libraries are
always fine.
"""
