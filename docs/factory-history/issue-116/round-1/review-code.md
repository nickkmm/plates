VERDICT: PASS

## Summary
- The nine files moved into app/orders/ as renames (routes, service, parsers, repository), and the change is behavior-neutral.
- relink_plates and card_id_for_order_plate moved to app/customers/repository/ownership_cards.py, so no customers -> orders cycle exists. The move has a TODO(phase 4) and the bodies are unchanged.
- app/core/db no longer re-exports the orders and plates names. Callers use orders_repository.<name>, the repository package has explicit re-exports, and the strict mypy override was extended.
- Docs and docstrings were updated. The remaining old-path hits are only in the historical ARCHITECTURE_AND_POS_PLAN.md and the MIGRATION_PLAN mapping table.
- checks.md shows 1844 tests passed, the same count as main.

## Findings
None.

## Notes
- app/customers/__init__.py: the docstring line "also imports app.web.helpers. It never imports the orders domain: relink_plates, which" is longer than the lines around it. This is cosmetic.
