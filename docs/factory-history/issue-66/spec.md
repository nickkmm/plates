# Production board and order status actions

> Issue #66: Add the production board and order status actions

## Summary
- Adds a `Producción` screen (`/produccion`) where production sees every order, filters it by status, and moves it `Por realizar` → `En realización` → `Lista para entregar` → `Entregada`. The point of sale can only do the last step and undo it.
- Adds an order detail page that shows order, plate and linked-declaration data with photos. It hides prices, payment, the signature and the PDF. The photos are served through new routes that check the declaration is linked to the order.
- Moves `_send_stored_file` into `app/web/uploads.py` as `send_stored_file` so both blueprints share it.
- Shows the production-status badge on the declarations list, the declaration detail and the order detail (`/records/<id>`), and adds `docs/PRODUCTION.md`.
- Intake accepted the issue as written. All the core logic (`advance_order` / `revert_order`, the db helpers, the permissions) is already on `main`, so this is web-layer work.

## Context
- **Core rules (exist, reuse as-is):** `app/core/production.py` has `advance_order(conn, order_id, user_id, *, expected_status)` and `revert_order(...)`. Each returns the new status or raises `ValueError` with a Spanish message (`MSG_STATUS_CHANGED`, `MSG_ALREADY_DELIVERED`, `MSG_NOT_STARTED`, `MSG_ORDER_NOT_FOUND`). Neither commits. They also export `next_status` / `previous_status`.
- **Data layer (exists):** `app/core/db/production.py` has these:
  - The status constants `STATUS_PENDING`, `STATUS_IN_PROGRESS`, `STATUS_READY`, `STATUS_DELIVERED` and `PRODUCTION_STATUSES`.
  - `list_production_orders(conn, *, status, query)`, which returns `ProductionOrderSummary` with `plate_count`, `plates` and `declaration_ids`.
  - `count_by_production_status(conn)`, `get_production_status`, `list_production_events(conn, order_id)` (which returns `ProductionEvent` with username and `created_at`) and `production_statuses_for_orders(conn, ids)`.
  - `app/core/db/declarations.py` has `list_declarations_by_production_order(conn, order_id)` and `list_declaration_cards(conn, declaration_id)`. `app/core/db/orders.py` has `order_detail(conn, order_id)`, which returns `(order, plates)`. The plates carry `is_voided` and `is_plate_annulled`.
- **Material** of a plate is derived by `app.core.normalize.inventory_category(plate_type)`. It returns None for ornamental plates. There is no material column.
- **Permissions:** `app/migrations/0015_roles_and_permissions.sql` grants these:
  - `production`: `records.view`, `production.view`, `production.manage`, `production.deliver`.
  - `point_of_sale`: `production.view` and `production.deliver`, plus its existing codes.
  - `admin`: everything.

  `app/web/auth.py` provides `permission_required(*codes)` (any of the codes), `has_permission(code)` and `landing_url()`, which returns the first `NAV_ITEMS` entry the user may open. Templates get `can(code)` and `nav_items` from the context processor in `app/web/app.py`.
- **Navigation:** `app/web/navigation.py` `NAV_ITEMS` holds `(endpoint, label, permission, active_endpoints)` entries.
- **Blueprints** are registered in `app/web/app.py`, whose module docstring lists every blueprint and its routes. The pattern to copy is `app/web/blueprints/declarations.py`:
  - list tabs (`STAGE_FILTERS`, `?stage=`, `?q=`) and a label dict (`STAGE_LABELS`);
  - `_render_detail(..., error=...)` returns `(page, 400)` on a `ValueError`;
  - `session["user_id"]` is the acting user and `session.get("user")` goes in the logs;
  - logging uses `current_app.logger.info(...)`.
- **File serving:**
  - `app/web/blueprints/declarations.py` holds `FILE_KINDS`, `CARD_PHOTO_SIDES`, `declaration_file`, `declaration_card_photo` and the private `_send_stored_file(stored_name, mimetype)`. That last helper sends the file inline with `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`, and returns 404 when the file is missing or invalid.
  - `app/web/uploads.py` has `resolve_upload_path` and `PHOTO_MIMETYPES`, and imports no Flask today.
  - The existing tests are in `tests/web/test_declarations.py` (around lines 520-730, `uploads` fixture at line 165).
- **Templates** (`app/web/templates/`) each carry their own inline `<style>`:
  - `declarations.html` has the list with `data-label` cells and an `@media (max-width:700px)` card layout. Its `.badge.ready` etc. are the declaration stage badges.
  - `declaration_detail.html` shows the attended-order link in the `#attended` block (around line 99) and renders photos with the `.photos` figure layout.
  - `record_detail.html` has a read-only view (around line 200) and a plates table that marks annulled plates.
  - `_nav.html` renders `nav_items`.
- **Time:** `app/core/timezones.format_bogota` is available in templates as the `|bogota` filter (`%d/%m/%Y %H:%M`).
- **Tests:** `tests/web/conftest.py` provides `client`, `authenticated_client` (admin), `produccion_client` and `punto_venta_client`, with CSRF off. `tests/web/_helpers.py` provides `make_row` and `_insert_order`. `tests/core/test_production.py` covers the core module.
- **Existing tests this change affects:** `tests/web/test_login.py`, `test_production_role_lands_on_records_not_ingest` and `test_root_redirects_production_role_to_records`, assert that production lands on `/records`. With the new nav entry it lands on `/produccion`.
- **Docs:** `docs/README.md` has the "Mapa de documentos" table. `docs/WEB.md` must not be edited in this task.

## Requirements
1. **Shared upload serving.** Move `_send_stored_file` from `app/web/blueprints/declarations.py` to `app/web/uploads.py` as public `send_stored_file(stored_name, mimetype)`. It keeps the same behaviour and headers: inline, `nosniff`, `no-store`, a 404 for a missing, invalid or unknown-extension file, and the mimetype from the extension when `mimetype` is None. `declarations.py` uses it and keeps no private copy. Update the `uploads.py` module docstring to mention it.
2. **New blueprint** `app/web/blueprints/production.py` (`Blueprint("production", __name__)`), registered in `app/web/app.py` and listed in that module's docstring. It defines these shared constants, used by every screen that shows the status:
   - `PRODUCTION_STATUS_LABELS`: `pending` `Por realizar`, `in_progress` `En realización`, `ready` `Lista para entregar`, `delivered` `Entregada`.
   - The tab definitions.
   - The badge is rendered as `<span class="badge prod-<status>">label</span>` with these colors: pending `#fff3d6` / `#8a5a00`, in_progress `#e6f0fb` / `#0b5cad`, ready `#e8f6ee` / `#1a7a45`, delivered `#eeeeee` / `#555555`. Keep the markup and CSS in one partial (e.g. `_production_badge.html`, included or imported as a macro) so the four templates don't each repeat the colors.
3. **Nav:** insert `("production.production", "Producción", "production.view", ("production.production", "production.order_detail"))` into `NAV_ITEMS` right after the `Declaraciones` entry. A production-role user then lands on `/produccion` after login and on `/`, and their nav shows only `Producción` and `Registros`.
4. **`GET /produccion`** (endpoint `production.production`, `@permission_required("production.view")`), template `production.html`:
   - **Tabs `?status=`:** `pending` `Por realizar` (default; an unknown value falls back to it), `in_progress` `En realización`, `ready` `Listas para entregar`, `delivered` `Entregadas`, `all` `Todas` (no status filter). The first three show their count from `count_by_production_status`.
   - **Search:** `?q=` with placeholder `Buscar por número de orden o placa`, passed to `list_production_orders(query=...)`. Every tab link keeps the current `q`.
   - **Columns:** `Orden`, `Fecha`, `Cliente` (`requested_by`), `Placas` (`plate_count` plus the `plates` text), `Entrega` (`delivery_date` or `—`), `Declaración` (`#N` per id in `declaration_ids`, plain text, not links), `Estado` (badge), and a `Ver` link to the detail.
   - **Empty state:** `No hay órdenes en este estado.`
   - **Narrow screens:** rows become cards, using the same `data-label` + `@media (max-width:700px)` pattern as `declarations.html`.
5. **`GET /produccion/<int:order_id>`** (endpoint `production.order_detail`, `production.view`; unknown id → 404), template `production_detail.html`:
   - **Title:** h1 `Orden <order_number>` followed by the status badge.
   - **`Datos de la orden`:** date, customer (`requested_by`), delivery date, client order number, tracking number, notes. No unit price, receipt type or number, payment method, payment date or `cop` values appear anywhere on the page.
   - **`Placas`:** type, plate number (or `<from> – <to>` for a range), quantity, city, material (`inventory_category(plate_type)` or `—`), reason, vehicle brand. Voided and annulled plates are marked the way `record_detail.html` marks them (`Orden anulada` for the order, a per-plate annulled marker).
   - **One `Declaración #<id>` section per declaration** from `list_declarations_by_production_order`:
     - Declarant name, document type, document number and phone, read from `submitted_data["declarant"]` like `declarations._declarant`.
     - For each card from `list_declaration_cards`: plate, vehicle type, reason badge, plate quantity, transit licence, engine, chassis, registered in, brand.
     - The card's photos (front, back, used plates, only those that exist) and the declarant's cédula photos (front, back, only those that exist), as `<img>` tags pointing at the routes in requirement 6.
     - No signature and no notarized PDF: neither a link nor an image.
   - **`Historial`:** one line per `list_production_events` row, `<created_at|bogota> · <username or —> · <De label> → <A label>`, or `Sin cambios todavía.` when there are none.
   - **Actions** (each a `method="post"` form with `csrf_token()` and a hidden `expected_status` holding the current status):
     - `pending`: button `Empezar realización`, needs `production.manage`.
     - `in_progress`: button `Marcar lista para entregar`, needs `production.manage`.
     - `ready`: button `Marcar entregada`, needs `production.manage` or `production.deliver`.
     - `delivered`: the text `Orden entregada.` and no forward button.
     - A secondary button `Deshacer último cambio`, with `onsubmit` confirm `¿Deshacer el último cambio de estado?`, posts to `/undo`. It is shown when the status is not `pending` and the user may undo: `production.manage`, or `production.deliver` when the status is `delivered`.
   - **On a `ValueError`:** the page is rendered with the message and status 400.
6. **Photo routes** (`production.view`):
   - `GET /produccion/<int:order_id>/declarations/<int:declaration_id>/file/<kind>`, where `kind` is only `id_front` or `id_back`.
   - `GET /produccion/<int:order_id>/declarations/<int:declaration_id>/cards/<int:card_id>/photo/<side>`, where `side` is `front`, `back` or `used_plates`.
   - Both return 404 when:
     - the kind or side is unknown;
     - the declaration does not exist or its `production_order_id != order_id`;
     - the card does not belong to that declaration;
     - the stored file is missing.
   - Both serve the file through `uploads.send_stored_file`, with None mimetype for photos. Reuse the attribute maps from `declarations.py` (`CARD_PHOTO_SIDES`, and the `id_front` / `id_back` entries of `FILE_KINDS`) instead of redefining them. No route under `/produccion` serves `signature` or `notarized_pdf`.
7. **`POST /produccion/<int:order_id>/advance`** and **`POST /produccion/<int:order_id>/undo`**, with `@permission_required("production.manage", "production.deliver")`:
   - Read `expected_status` from the form. Unknown order → 404.
   - **Permission check,** against the posted `expected_status`:
     - With `production.manage`, every transition is allowed.
     - Otherwise, `advance` is allowed only when `expected_status == "ready"` and `undo` only when `expected_status == "delivered"`.
     - Anything else answers 403 and changes nothing.
   - **Allowed:** call `advance_order` / `revert_order(conn, order_id, session["user_id"], expected_status=...)`, commit, log `production status: user=<session user> order_id=<id> <from> -> <to>` with `current_app.logger.info`, and redirect (302) to the detail page.
   - **`ValueError`:** re-render the detail with the message and status 400. Nothing is committed.
8. **Badges on existing screens:**
   - **`declarations.html` (list):** an attended declaration (one with `production_order_id`) shows a second badge with its order's production-status label. The statuses come from one `production_statuses_for_orders` call for the page, never one query per row.
   - **`declaration_detail.html`:** in the attended block, next to the order link, show `Estado en producción:` and the badge.
   - **`record_detail.html`:** show `Estado en producción:` and the badge in both the edit and read-only views. Users with `production.view` (`can('production.view')`) also get a `Ver en Producción` link to `/produccion/<id>`.
9. **Tests** (pytest, in a new `tests/web/test_production.py` plus the edits below):
   - Cover acceptance criteria 1-6.
   - Update the two `tests/web/test_login.py` landing tests to expect `/produccion`.
   - Every existing test in `tests/web/test_declarations.py` must still pass unchanged.
10. **Docs** (Spanish prose):
    - Add `docs/PRODUCTION.md`. It explains the full flow (declaración → revisión del punto de venta → orden → producción → entrega), includes a `mermaid` `stateDiagram-v2` of the four statuses with forward and undo transitions, and covers who may make each step (permissions) and the stale-status rule. It ends with a routes table: method, route, permission, what it does.
    - Add its row to the "Mapa de documentos" table in `docs/README.md`.
    - Do not edit `docs/WEB.md`.

## Acceptance criteria
1. A production-role test user does the following, in `tests/web/test_production.py`:
   - creates an order (e.g. via `_insert_order`) and sees it on `GET /produccion` (200), under the default tab with `Por realizar`;
   - posts `advance` with `expected_status=pending`, then `in_progress`, then `ready`, and gets a 302 to `/produccion/<id>` each time;
   - checks that the tab counts on `/produccion` change after each step (e.g. the `Por realizar` count drops and the `En realización` count rises);
   - checks that the order ends `delivered` (`db.get_production_status`);
   - posts `undo` with `expected_status=delivered`, gets a 302, and the order is back to `ready`.
2. A point_of_sale user does the following, in `tests/web/test_production.py`:
   - gets 200 on `/produccion` and on the detail;
   - on a `pending` order and on an `in_progress` order, sees none of `Empezar realización`, `Marcar lista para entregar`, `Marcar entregada` or `Deshacer último cambio`, and gets 403 on both `advance` and `undo`, with the status unchanged;
   - on a `ready` order, sees `Marcar entregada`, and its POST moves the order to `delivered`;
   - then sees `Deshacer último cambio`, its `undo` returns the order to `ready`, and a second `undo` (`expected_status=ready`) returns 403.
3. A test posts `advance` with `expected_status=in_progress` on a `pending` order. The response is 400 and contains `El estado de la orden cambió mientras tanto`, the status is still `pending`, and no `production_order_events` row was added.
4. Photo and detail tests:
   - The detail of an order linked to a declaration shows the declarant name and document number and the card's plate, transit licence and chassis.
   - It does not contain `/file/signature`, `notarized_pdf` or any price formatted with `cop`.
   - `/produccion/<order>/declarations/<decl>/file/id_front` and `.../cards/<card>/photo/front` return 200 with the photo bytes and `Cache-Control: no-store`.
   - The same URLs return 404 for a declaration linked to a different order and for a card of another declaration.
   - `/produccion/<order>/declarations/<decl>/file/signature` returns 404.
5. The login tests check landing and nav:
   - A production user's login POST redirects to `/produccion`, and `GET /` redirects there too.
   - On `/produccion`, the nav contains `Producción` and `Registros`, and no `href` for `/ingest`, `/cuadre`, `/declaraciones`, `/review`, `/inventory`, `/precios`, `/customers` or `/usuarios`.
6. Badge tests:
   - For an attended declaration whose order is `in_progress`, `GET /declaraciones?stage=attended` and `GET /declaraciones/<id>` contain `En realización` and the class `prod-in_progress`. The detail also contains `Estado en producción:`.
   - `GET /records/<order_id>` as admin contains `Estado en producción:`, the label, and a link to `/produccion/<order_id>` (`Ver en Producción`).
7. `grep -n "def _send_stored_file" app/web/blueprints/declarations.py` returns nothing, and `grep -n "def send_stored_file" app/web/uploads.py` returns one line. Every test in `tests/web/test_declarations.py` passes.
8. `docs/PRODUCTION.md` exists, is written in Spanish, contains a ```` ```mermaid ```` block with `stateDiagram`, and has a table listing `/produccion`, `/produccion/<order_id>`, both photo routes, `/advance` and `/undo`. `docs/README.md` links to `PRODUCTION.md`. `git diff --stat origin/main` shows no change to `docs/WEB.md`.

## Out of scope
- Changing declaration stages, approval, notarization or `/ingest`.
- Assigning orders to a production user, deadlines, notifications, printing.
- Showing prices, payment method, payment date or receipt data to production.
- Any change to `app/core/production.py`, `app/core/db/production.py`, the migrations, `init.sql` or the permission grants. No new migration.
- Editing `docs/WEB.md`, even though it lists routes and templates.
- A signature or PDF route under `/produccion`, a ZIP download for production, and links from the board's `Declaración` column to `/declaraciones`.
- Changing what `/records` or `/declaraciones` allow per role.
- Refactoring the inline-style template pattern into a shared stylesheet.
- Editing `factory/checks.sh` or `factory/config.json`.

## Assumptions
- **Permission check uses the posted `expected_status`.** The 403 check in `advance` / `undo` reads the posted value, not the stored status. The reason is that the core function already refuses the change unless the stored status equals the posted one, so a deliver-only user can never move an order out of any status other than `ready` or `delivered`.
- **`advance` / `undo` on an unknown order answer 404.** The issue does not say what happens here. There is no detail page to re-render the message on, which also matches the detail route.
- **The status tabs are named `Listas para entregar` / `Entregadas` in the plural**, while the badges are singular. The issue's tab and badge lists word them that way.
- **Material is `inventory_category(plate_type)`.** No material column exists, and this is the function inventory uses.
- **The landing tests in `tests/web/test_login.py` are updated to `/produccion`** instead of kept. The issue explicitly wants production to land on the new screen.
- **The label constants live in `app/web/blueprints/production.py`**, and `declarations.py` and `records.py` import them from there. The issue asks for shared constants in the new blueprint, and the new blueprint does not import from those two, so there is no import cycle.
- **The badge markup and colors live in one Jinja partial.** This avoids repeating the hex colors in four inline `<style>` blocks, and the `prod-` class prefix still keeps it apart from the declaration `.badge.ready`.
