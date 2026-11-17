STATUS: READY

# Build: Production board and order status actions

## Summary
- New `Producción` board at `/produccion` (status tabs with counts, search kept across tabs, cards on narrow screens) and nav entry after `Declaraciones`; production now lands there.
- Order detail `/produccion/<id>` with order data (no prices/payment/receipt), plates with material, linked declarations with cédula and card photos (no signature/PDF), history and permission-gated advance/undo forms.
- `advance` / `undo` POSTs check the transition per permission (403), run the core functions, commit, log, and re-render with 400 on a stale status.
- `send_stored_file` moved to `app/web/uploads.py` and shared; `prod-<status>` badge (one partial) on the declarations list/detail and `/records/<id>` (with `Ver en Producción`).
- `docs/PRODUCTION.md` added and linked from `docs/README.md`; `docs/WEB.md` untouched. Draft PR #95.
- Round 3: the `prod-<status>` badge has a 1px outline in its own text color (`box-shadow: inset 0 0 0 1px currentColor`), so it stays visible on the light-blue `attended` box of the declaration detail.
- Round 2: the order detail's annulled plate rows are no longer greyed (low contrast) and the advance button uses the darker green `#1a7a45`.

## Changes
- `app/web/uploads.py`: new public `send_stored_file(stored_name, mimetype)` (same behaviour/headers as the old private helper); module docstring mentions it.
- `app/web/blueprints/declarations.py`: private `_send_stored_file` removed, uses `send_stored_file`; list computes production statuses with one `production_statuses_for_orders` call; detail passes the attended order's `production_status`.
- `app/web/blueprints/production.py` (new): `PRODUCTION_STATUS_LABELS`, `STATUS_FILTERS` tabs, `ADVANCE_LABELS`; routes `production`, `order_detail`, `declaration_file` (only `id_front`/`id_back`, reusing `FILE_KINDS`), `declaration_card_photo` (reusing `CARD_PHOTO_SIDES`), `advance`, `undo`.
- `app/web/app.py`: registers the blueprint, lists it in the docstring, exposes `PRODUCTION_STATUS_LABELS` to Jinja as the global `production_status_labels`.
- `app/web/navigation.py`: `Producción` entry after `Declaraciones`.
- `app/web/blueprints/records.py`: passes `production_status` to `record_detail.html` (detail and error re-render).
- Templates (round 2: `production_detail.html` drops the `#999` annulled-row style, marking annulled plates only with the `Sí` tag like `record_detail.html`, and the `.advance` button is `#1a7a45` on white, about 5.4:1): new `_production_badge.html` (macros `style()` and `badge(status)`, colors in one place), `production.html`, `production_detail.html`; `declarations.html`, `declaration_detail.html`, `record_detail.html` show the badge (`Estado en producción:` on the two detail pages; `Ver en Producción` link with `can('production.view')`).
- Tests: new `tests/web/test_production.py` (16 tests); `tests/web/test_login.py` landing tests now expect `/produccion` plus a nav test; `tests/web/test_users.py::TestPassword::test_reset_password` final landing assertion updated to `/produccion` (the production user it logs in with now lands on the board).
- Docs: new `docs/PRODUCTION.md`, row in `docs/README.md`.

**Deviation from the spec's assumption on constants:** the spec assumed `declarations.py` and `records.py` import the labels from the production blueprint, but requirement 6 also has `production.py` import `FILE_KINDS`/`CARD_PHOTO_SIDES` from `declarations.py`, which would make an import cycle. So the labels are defined once in `production.py` and reach every template through the Jinja global set in `app.py`; `declarations.py`/`records.py` only pass the status value.

## Acceptance criteria
1. [x] Production flow: `TestProductionFlow::test_production_moves_an_order_through_every_status_and_undoes_the_last`: board 200 with `Por realizar` and `prod-pending`, three `advance` POSTs each 302 to `/produccion/<id>`, the tab counts are checked after each step, status ends `delivered`, `undo` with `delivered` gives 302 and `ready`. Passed.
2. [x] Point of sale: `TestPointOfSale::test_sees_no_action_and_gets_403_before_ready[pending|in_progress]` (200 on board and detail, none of the four labels, 403 on advance and undo, status and event count unchanged) and `test_marks_a_ready_order_delivered_and_undoes_only_that` (`Marcar entregada` → delivered, `Deshacer último cambio` → ready, second undo with `ready` → 403). Passed.
3. [x] Stale status: `TestStaleStatus::test_stale_expected_status_is_400_and_changes_nothing`: 400, `El estado de la orden cambió mientras tanto` in the body, still `pending`, no new `production_order_events` row. Passed.
4. [x] Detail and photos: `TestDetailAndPhotos`: declarant name/document, card plate/licence/chassis shown; no `/file/signature`, `notarized_pdf`, `$` (cop), payment method or receipt; `file/id_front` and `cards/<card>/photo/front` give 200 with the JPEG bytes, `no-store` and `nosniff`; 404 for a declaration linked to another order, for a card of another declaration, for an unknown side, and for `file/signature`, `file/notarized_pdf`; missing file 404. Passed.
5. [x] Landing and nav: `tests/web/test_login.py` `test_production_role_lands_on_the_production_board_not_ingest`, `test_root_redirects_production_role_to_the_production_board` (both `/produccion`) and `test_production_role_nav_shows_only_production_and_records` (no href to `/ingest`, `/cuadre`, `/declaraciones`, `/review`, `/inventory`, `/precios`, `/customers`, `/usuarios`). Passed.
6. [x] Badges: `TestBadges`: `/declaraciones?stage=attended` and `/declaraciones/<id>` contain `En realización` and `prod-in_progress` (detail also `Estado en producción:`); one `production_statuses_for_orders` call for the list; `/records/<order_id>` as admin shows `Estado en producción:`, the label, `href="/produccion/<id>"` and `Ver en Producción`; read-only view (production role) shows it too. Passed.
7. [x] `grep -n "def _send_stored_file" app/web/blueprints/declarations.py` prints nothing (rc=1); `grep -n "def send_stored_file" app/web/uploads.py` prints `121:def send_stored_file(...)`. `tests/web/test_declarations.py` unchanged and passing (part of the 249 passed run and the full run).
8. [x] `docs/PRODUCTION.md` is in Spanish, has a ```` ```mermaid ```` `stateDiagram-v2` block and a routes table with `/produccion`, `/produccion/<order_id>`, both photo routes, `/advance` and `/undo`; `docs/README.md` links `PRODUCTION.md`; `git diff --stat origin/main` lists no `docs/WEB.md`.

Page check: the `agent-browser` CLI is not installed on this machine (`agent-browser: command not found`, no node/npx), so I could not look at the pages in a browser. The rendered HTML of every page was checked through the Flask test client in the tests above.

## Validation
- `.venv/Scripts/python -m ruff check app/`: passed.
- `.venv/Scripts/python -m mypy app/`: passed (50 files).
- `pytest tests/web/test_production.py tests/web/test_login.py tests/web/test_declarations.py tests/web/test_records.py` (TEST_POSTGRES_DB=plates_test_10, TEST_REQUIRE_DB=1): 249 passed.
- `bash factory/checks.sh` first run: 1 failed (`test_users.py::TestPassword::test_reset_password` expected `/records`), 1741 passed. Fixed the assertion (see Log).
- `bash factory/checks.sh` second run: passed (ruff, mypy, 1742 passed, shell and factory script checks; exit 0).

## Validation (round 3)
- `pytest tests/web/test_production.py tests/web/test_login.py`: 43 passed; `ruff check app/`: passed.
- `bash factory/checks.sh`: passed (ruff, mypy, 1742 passed, shell and factory script checks; exit 0).

## Validation (round 2)
- `pytest tests/web/test_production.py`: 17 passed.
- `bash factory/checks.sh`: passed (ruff, mypy, 1742 passed, shell and factory script checks; exit 0).

## Log
### Round 1
- Set up `.venv` in the worktree and installed `requirements-dev.txt`.
- Implemented the spec: shared `send_stored_file`, the production blueprint and templates, nav entry, badges on the existing screens, docs and tests.
- Resolved the import-cycle conflict between the spec's assumption (declarations/records import labels from production) and requirement 6 (production imports the file maps from declarations) by exposing the labels as a Jinja global from `app.py`.
- The first full gate run found one more landing test outside `test_login.py` (`test_users.py` password reset logs in the production user and expected `/records`); updated it to `/produccion`, as the spec intends for production's landing.
- Opened draft PR #95 (no AI-attribution line, per CLAUDE.md).

### Round 2
Review findings (`round-1/review-ui.md`, VERDICT: CHANGES; the other three reviews passed):
1. Annulled plate rows greyed with `#999` (about 2.9:1): removed `tr.annulled td` and the row class; annulled plates are marked only by the `Sí` tag in "Placa anulada", as in `record_detail.html`.
2. White on `#1a9c5b` advance button (about 3.5:1): changed to `#1a7a45` (about 5.4:1).
Optional notes not changed: `.muted #777` and hard-coded hex match the neighbouring screens; `autofocus` on the search matches `declarations.html`; the `.tabs` rule is not unused (`_nav.html` renders `<nav class="tabs">`); the heading levels follow `declaration_detail.html`.

### Round 3
Review findings (`round-2/review-ui.md`, VERDICT: CHANGES; code, security and UX passed):
1. The `prod-in_progress` badge (`#e6f0fb`) disappears on the declaration detail's `.next.done` box, which has the same background: added `box-shadow: inset 0 0 0 1px currentColor` to the four `prod-*` badges in `_production_badge.html`. Colors stay as the spec gives them. Commit 8ef2279.
Optional notes not changed (same reasons as round 2): heading levels follow `declaration_detail.html`; `autofocus` matches `declarations.html`; the `.tabs` rule styles the shared `<nav class="tabs">` from `_nav.html`; `.muted #777` and hard-coded hex match the neighbouring screens.
