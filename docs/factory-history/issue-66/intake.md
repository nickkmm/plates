DECISION: ACCEPT
RISK: high
SURFACES: ui, security
RESCOPED: no

## Summary
- Accepted as written: the new `Producción` board, order detail, status actions, photo routes, status badges on existing screens, and `docs/PRODUCTION.md`.
- All the groundwork it builds on is already on `main`: `advance_order` / `revert_order`, the production db helpers, the three `production.*` permissions granted per role, and CSRF (#92).
- Risk is high because it adds permission-gated state changes with optimistic concurrency and serves private declaration photos (cédula, cards) to a new audience.
- It is large but tightly specified, and most of the logic already lives in `app/core`, so it should fit in one PR.

## Scope
Build what the issue specifies:
- A `Producción` screen at `GET /produccion` (`production.view`). It has status tabs with counts for the first three, a search by order number or plate that is kept across tabs, the listed columns, the empty state, and card layout on narrow screens. The nav entry goes right after `Declaraciones`, so a production user lands on it after login.
- An order detail at `GET /produccion/<order_id>`. It shows the order data without prices, payment or receipt, the plates (voided and annulled ones marked), one section per linked declaration with the declarant and card data and photos (no signature, no PDF), and the history. Unknown id returns 404.
- Photo routes for the cédula front/back and the card front/back/used plates. Each returns 404 unless the declaration is linked to that order and the card belongs to that declaration. They reuse the existing serving logic, moved into `app/web/uploads.py` as `send_stored_file`, with the same headers.
- `advance` / `undo` POSTs. To enter the route the user needs `production.manage` or `production.deliver`. Each specific transition is then checked: `production.deliver` alone may only do `ready -> delivered` and undo `delivered -> ready`. A disallowed transition returns 403. A stale `expected_status` re-renders the page with the Spanish message (400) and changes nothing. Each change is logged.
- The production-status badge (`prod-<status>` classes, labels and colors from the issue) on the declarations list and detail and on the order detail. The order detail also gets a `Ver en Producción` link for users with `production.view`.
- `docs/PRODUCTION.md` in Spanish, with a mermaid state diagram and a routes table, plus its row in `docs/README.md`. `docs/WEB.md` is not touched.
- The tests listed in acceptance criteria 1 to 7.

Left out, as the issue says: changes to declaration stages, approval or `/ingest`; assigning orders, deadlines, notifications and printing; showing prices or payment data to production.

## Why
Every dependency the issue names is already on `main`: `app/core/production.py` (`advance_order` / `revert_order` with the stale-status check), `count_by_production_status` and `production_statuses_for_orders` in `app/core/db/production.py`, `list_declarations_by_production_order` in `app/core/db/declarations.py`, the permission grants in `app/migrations/0015_roles_and_permissions.sql`, and `_send_stored_file` in `app/web/blueprints/declarations.py`. The permission rules for who may do which step are already decided in the issue and match those grants, so no business decision is left open. This is mostly web-layer work over existing core logic. Its sensitive parts (per-transition authorization, linking checks on the photo routes, no signature route) are covered by the acceptance tests.
