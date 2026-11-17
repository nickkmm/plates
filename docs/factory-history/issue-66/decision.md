ESCALATE

## Summary
- Adds the `Producción` board (`/produccion`), the order detail with linked declaration data and photos, and permission-gated `advance` / `undo` status steps (issue #66, PR #95).
- Round 3 gate passed (1742 tests) and all four final reviews are `VERDICT: PASS`. Rounds 1 and 2 had UI contrast findings, and both were fixed.
- All 8 acceptance criteria are met. I spot-checked criteria 7 and 8 and read the blueprint's permission and photo-link checks myself.
- The change stays inside intake's Scope. It adds no migration, env var, Docker/compose change or dependency.
- It needs a person because it adds new route permissions, changes the production role's landing page, and shows declarants' personal data and cédula photos to a new audience.

## Why
The work is complete and well tested, and every automatic gate passed. Under this project's rules, a change that adds route permissions and exposes personal data from the public declaration form (cédula photos, document numbers) must be checked by a person before merge. That applies to this PR, so I am escalating it.

## Acceptance criteria
1. Production flow (board, three advances with 302s, tab counts, ends `delivered`, undo back to `ready`): met. `TestProductionFlow` passes in the round-3 gate (1742 passed).
2. Point of sale (no buttons and 403 before `ready`, can mark delivered, undo once, second undo 403): met. `TestPointOfSale` passes. `_may_advance` / `_may_undo` in `app/web/blueprints/production.py` limit `production.deliver` to `ready -> delivered` and its undo.
3. Stale `expected_status` gives 400 with the message and no new event row: met. `TestStaleStatus` passes.
4. Detail shows declarant and card data with no signature, PDF or prices, and photo routes give 200 / `no-store`, or 404 when the declaration or card is not linked or for `signature`: met. `TestDetailAndPhotos` passes. I confirmed `ID_PHOTO_KINDS` only allows `id_front` / `id_back` and that `_linked_declaration` checks `production_order_id == order_id`.
5. Production login lands on `/produccion` and the nav is limited: met. The updated `tests/web/test_login.py` tests pass.
6. Production badges on `/declaraciones` (list and detail) and on `/records/<id>`, with `Ver en Producción`: met. `TestBadges` passes.
7. `_send_stored_file` is moved to `uploads.send_stored_file`: met. I checked myself: the grep in `declarations.py` finds nothing (rc=1), the grep in `uploads.py` finds line 121, and the declarations tests pass in the gate.
8. `docs/PRODUCTION.md` in Spanish with a mermaid `stateDiagram-v2` and a routes table, linked from `docs/README.md`, `docs/WEB.md` untouched: met. I checked myself: the diff does not touch `docs/WEB.md`, and the advance and undo routes are in the table.

## For the human
Recommendation: don't merge yet

Code quality is not the problem: all checks and reviews pass. Under this project's rules, a person has to check changes to route permissions and to who can see personal data from the public declaration form. Please confirm these points:

1. **Who can see personal data.** Users with `production.view` (the production role and the point of sale) can now see declarants' names, document numbers, phones, cédula front and back photos and card photos for linked orders. The signature and notarized PDF are never served. Confirm this audience is acceptable. Look at `app/web/blueprints/production.py` (`declaration_file`, `declaration_card_photo`, `_linked_declaration`) and `app/web/templates/production_detail.html`.
2. **Who can change an order's status.** `advance` / `undo` let in anyone with `production.manage` or `production.deliver`. A user with only `production.deliver` may only do `ready -> delivered` and undo it. That check reads the *posted* `expected_status` (a spec Assumption). It is safe only because the existing core `advance_order` / `revert_order` refuse the change unless the stored status matches. Confirm you accept relying on the core check. See `_move` in `app/web/blueprints/production.py` and `app/core/production.py`.
3. **New landing and nav.** Production users now land on `/produccion` instead of `/records` (`app/web/navigation.py`, `tests/web/test_login.py`, `tests/web/test_users.py`).
4. **Docs.** `docs/WEB.md` still does not list the new routes and templates. The spec said to leave it, and they are documented in `docs/PRODUCTION.md`. Decide whether WEB.md needs a follow-up.
5. **Not tested in a browser.** No reviewer could open the pages (`agent-browser` is not installed). Before merging, look at `/produccion` and `/produccion/<id>` at desktop and phone width on localhost.

Optional, not blocking (from the UI and UX reviews): heading levels in `production_detail.html` skip from h1 to h3, `autofocus` on the board's search box opens the keyboard on phones, there is an unused `.tabs` CSS rule, and the tabs have no `aria-current`.
