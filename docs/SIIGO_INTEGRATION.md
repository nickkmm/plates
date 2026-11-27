# Siigo integration plan

Status: **proposal**, approved in direction by the owner and by accounting
(contabilidad); nothing in this document is implemented yet. Owner of every
task: `nickkmm`.

**Scheduling (owner decision):** this is **Phase 8** of
`docs/MIGRATION_PLAN.md`, built **after every other phase**, directly in the
final architecture: the `app/siigo/` domain, JSON endpoints and Next.js
screens. No Jinja routes or templates are added for it. Until phase 8 the
point of sale keeps today's behavior: order number typed by hand from the
NT, current payment methods and receipt fields. The prototype
(`prototypes/`) already shows the final Siigo UI (read-only order number,
card payments, transfer state, "Facturar en Siigo" preview, "Envíos a
Siigo" list).

File and line references to today's Flask code (`ingest.py`, `records.py`,
`review.py`, `ingestion.py`) mark **the behavior** to carry over; by phase
8 that behavior lives in the `orders` domain service and the Next.js point
of sale, so apply each rule there.

## 1. Goal

Today every plate order is typed twice: once in Siigo Nube (the accounting
system) and once in plates. Staff first create a **"Nota de traslado entre
bodegas"** (NT) in Siigo, copy the number Siigo gives it (e.g. 3874) and
type it into plates as `production_orders.order_number`; then they record
the payment in Siigo by hand (a cash receipt or an electronic invoice).

Target: **staff type the order only in plates**, and plates creates in Siigo:

1. **Always**, when the order is saved: the inventory transfer that replaces
   the NT, numbered with the order number, continuing the NT sequence.
2. **Depending on the payment method**, from a button with a preview:
   - `EFECTIVO` → a cash receipt (recibo de caja, `RC-2`), no electronic invoice.
   - `TRANSFERENCIA`, `CRÉDITO`, `TARJETA DÉBITO`, `TARJETA CRÉDITO` → an
     electronic invoice (`FV-3`, sent to the DIAN by Siigo).

Out of scope for now (keep the design generic so they fit later): other INGSE
services billed through Siigo (signs, banners, signage, contracts), partial
payments (abonos), and the per-plate cost in the transfer (see §9).

## 2. Verified facts (live Siigo account, 2026-11-15 to 2026-11-26)

All of these were checked against the real account through the REST API.
Nothing here is an assumption unless marked *to verify*.

### 2.1 API basics

- Base URL `https://api.siigo.com`. Auth: `POST /auth` with
  `{"username", "access_key"}` and header `Partner-Id`; returns a Bearer token
  valid 86 400 s. Every call sends `Authorization: Bearer <token>`,
  `Partner-Id` and `Content-Type: application/json`.
- Rate limit: 100 requests/minute.
- Credentials live in the environment: `SIIGO_USERNAME`, `SIIGO_ACCESS_KEY`,
  `SIIGO_PARTNER_ID`. Never log, print or commit them.
- **There is no sandbox.** Every write goes to the real company books.
- **There is no API for "Nota de traslado entre bodegas".** No route exists
  (about 25 paths probed, all 404) and `document-types` rejects NT/TR. That is
  why the transfer is made as a **comprobante contable** (journal), which the
  API does support (`POST /v1/journals`).
- **The product average cost is not exposed** (product fields and ~14
  undocumented paths checked). It only exists in the Siigo UI.

### 2.2 The NT we replace (NT-3874, read from Siigo)

- Third party (tercero): INGSE itself, NIT 900773756.
- One line per plate product: product `000452` PLACA PARTICULAR, quantity 1,
  from **Bodega Productos en Proceso** to **Bodega Producto Terminado**.
- Observations: `1P PARTICULAR CEU215 EL CERRITO` (the same text staff put in
  the cash receipt of that sale, RC-2-652).
- Its accounting export: two lines on account `14300101 Placas`, credit at the
  origin warehouse and debit at the destination, **value 0** (only quantities
  move).

### 2.3 The journal that replaces it (tested and accepted by accounting)

- Accounting created a journal type **"Traslado placas api"**: document id
  **74172**, code **100**, **manual numbering** (`automatic_number: false`).
  Documents come out as `CC-100-<number>`.
- Tests run against it:

| Test | Request | Result |
|---|---|---|
| Shape | number 1, payload of §5.4 | `201`, `CC-100-1`, reviewed by accounting: OK |
| High number | number 99999 | `201`, `CC-100-99999`: any number is accepted, so plates can continue the NT sequence |
| Repeated number | number 1 again | `400`, `{"Code": "already_exists", "Message": "The number already exists: 1", "Params": ["number"]}`: Siigo itself refuses duplicates |

- CC-100-1 and CC-100-99999 are test documents that accounting must delete
  before go-live (each moved 1 unit of `000452` from warehouse 31 to 32).

### 2.4 Ids used by this plan

| What | Value |
|---|---|
| Transfer journal type | document id `74172` (CC code 100) |
| Inventory account | `14300101` (Placas) |
| INGSE NIT (third party of the transfer) | `900773756` |
| Warehouses | `31` Bodega Productos en Proceso (origin), `32` Bodega Producto Terminado (destination) |
| Cash receipt type | document id `42477` "Recibo de caja Punto de venta C2" (`RC-2`), voucher type `AdvancePayment` |
| Electronic invoice type | document id `42461` "FE Punto de Venta" (`FV-3`, prefix PVIN) |
| IVA 19 % | tax id `11056` |

Products per `plate_type`:

| plate_type | Siigo product |
|---|---|
| MOTO PARTICULAR | `000453` |
| CARRO PARTICULAR | `000452` |
| CARRO PÚBLICO | `000461` |
| MOTO CARRO PARTICULAR | `000473` |
| MOTO CARRO PÚBLICO | `000472` |
| REMOLQUE | `000479` |
| ORNAMENTAL | `000534` |

Payment methods (Siigo payment ids):

| plates value | Siigo payment |
|---|---|
| `EFECTIVO` | `13960` "Efectivo punto de venta caja 2" |
| `TRANSFERENCIA` + account `AHORROS 7716` | `13966` |
| `TRANSFERENCIA` + account `CORRIENTE 9655` | `4740` |
| `TRANSFERENCIA` + account `BANCO BOGOTÁ` | `34090` |
| `CRÉDITO` | `4736` (needs `due_date`) |
| `TARJETA DÉBITO` | `4737` |
| `TARJETA CRÉDITO` | `4738` |

Sellers (Siigo user ids): placas@ `759`, servicioalcliente@ `760`, ovelez@
`758`, contabilidad@ `763` (others exist; mapped per plates user, §6.6).

Retail plate price: `130000` with IVA included (Siigo stores base
`109243.697479` + 19 %). Credit plate invoices are due at 30 days. Invoice
observations always start with `SOMOS REGIMEN SIMPLE DE TRIBUTACIÓN`.

## 3. Business flow

```
staff saves the order in plates
  │
  ├─ plates assigns the next order number (last NT + 1, gapless)
  ├─ order + pending transfer row committed in ONE transaction
  └─ plates sends POST /v1/journals  ──► CC-100-<number>
         ok          → transfer "sent", order shows "Traslado CC-100-3875"
         error/timeout → transfer "pending"/"error", order is still saved,
                         production keeps working, "Reintentar" button

later, staff press "Facturar en Siigo" on the order (preview → confirm)
  ├─ EFECTIVO                       → cash receipt RC-2   (POST /v1/vouchers)
  └─ TRANSFERENCIA/CRÉDITO/TARJETA  → electronic invoice FV-3 (POST /v1/invoices)
```

Rules:

- The transfer is **automatic** (no button) and happens for **every new order**
  once the feature is on. Production relies on that number.
- Saving the order **never** fails because Siigo is down. The number is
  assigned by plates, not by Siigo.
- The receipt/invoice is **manual** (button + preview) because an invoice goes
  to the DIAN and a receipt moves cash; a person must look at it first.

## 4. Architecture

### 4.1 Where the code goes

`docs/MIGRATION_PLAN.md` moves the back end to domain packages
(`app/<domain>/{routes,schemas,service,repository}.py`) and the screens to
Next.js. By phase 8 that migration is complete, so:

- **New domain `app/siigo/`** (integration with the accounting system):
  `client.py` (HTTP), `transfers.py` / `documents.py` (pure payload builders
  and formatters), `service.py`, `repository.py` (the tables it owns:
  `siigo_transfers`, `siigo_documents`), `routes.py`, `schemas.py`.
- Dependency direction: **`orders` → `siigo`**, never back. The `orders`
  service passes plain data (order number, date, lines, observations,
  customer, payment) to `siigo`; `siigo` never queries `production_orders` or
  `plates` itself. The order-number counter belongs to `orders`. Screens that
  combine both (the pending-transfers list) are built by the `orders` service,
  which reads transfer state through the `siigo` service; no cross-domain SQL
  join.
- Keep the pure parts (payload builders, observations formatter, totals)
  free of I/O and strictly typed; only `client.py` does network calls. Add
  `siigo/` to the "Target layout" and the API table of
  `docs/MIGRATION_PLAN.md` in the docs task.
- Screens: Next.js only (the point of sale order form, the order detail with
  the transfer state and the "Facturar en Siigo" preview, the "Envíos a
  Siigo" list), using the generated API client. Follow the prototype in
  `prototypes/` and `docs/FRONTEND.md`.

### 4.2 Siigo client (`client.py`)

- Stdlib `urllib` only (no `requests`), same pattern as
  `app/web/antispam.py:98`: one module-level `_request(method, path, body,
  timeout)` wrapper that tests monkeypatch.
- Environment read lazily at call time (`os.environ.get`), like
  `antispam.py`. Missing credentials → a typed error with a Spanish message
  ("La integración con Siigo no está configurada").
- Token cache: module-level `(token, expires_at)`, refreshed when less than
  10 minutes remain or on any `401` (retry the call once with a new token).
  Each gunicorn worker keeps its own token; that is fine (auth is not
  rate-limited separately and tokens last 24 h).
- Timeouts: gunicorn runs only 2 sync workers (`web.Dockerfile:23`), so a
  slow Siigo must never hold a worker for long. The **inline send on order
  save has a 5 s total budget** (cached token; if a new token is needed, it
  counts against the same budget); on timeout the transfer stays `pending`.
  Calls triggered by an explicit button (retry, receipt, invoice) may use
  15 s.
- Errors: Siigo returns `{"Status": 400, "Errors": [{"Code", "Message",
  "Params", "Detail"}]}`. Convert to `SiigoError(status, code, message,
  params)`; keep the raw body (truncated to 2 000 chars) for
  `error_message`. Network errors and timeouts → `SiigoUnavailable`.
- `429` → `SiigoUnavailable` (treated as retryable, never retried in a loop
  inside a request).
- Functions (each returns parsed JSON or raises):
  `create_journal(payload)`, `find_journal(name)`,
  `create_voucher(payload)`, `create_invoice(payload)`,
  `find_customer(identification)`, `create_customer(payload)`,
  `get_product(code)`.
- Never log the request headers. Log method, path, status and the Siigo
  document name only.

### 4.3 Configuration

| Variable | Meaning | Default |
|---|---|---|
| `SIIGO_USERNAME`, `SIIGO_ACCESS_KEY`, `SIIGO_PARTNER_ID` | API credentials | none |
| `SIIGO_TRANSFERS_ENABLED` | `1` turns on number assignment + automatic transfer | off |
| `SIIGO_BILLING_ENABLED` | `1` shows the "Facturar en Siigo" button | off |

The ids of §2.4 are constants in `siigo/config.py` (they are business
configuration that changes only if accounting changes Siigo), not env vars.

- `docker-compose.yml`, service `web`: the container only reads `.env`
  today, so add the five variables to `.env.example` (names only, no
  values). Production gets them through the `ENV_FILE_B64` GitHub secret.
- With the flags off the app behaves exactly as today (manual order number,
  no Siigo calls). This allows merging every PR before go-live.

## 5. Transfer (the NT replacement) in detail

### 5.1 Order number: gapless counter

The NT sequence must continue without gaps or duplicates, so a Postgres
`SEQUENCE` is **not** used (a rolled-back transaction would burn a number).

- New table `order_number_counter` (single row):
  `id BOOLEAN PRIMARY KEY DEFAULT true CHECK (id)`,
  `last_number INTEGER NOT NULL`, `updated_at TIMESTAMP NOT NULL DEFAULT now()`.
  The row is **not** created by the migration (the cutover creates it, §10).
- Assignment, inside the same transaction that inserts the order:
  `UPDATE order_number_counter SET last_number = last_number + 1,
  updated_at = now() RETURNING last_number`. The row lock serializes
  concurrent saves; a rollback returns the number. No row → the feature is
  not cut over yet → error "Falta configurar el último número de NT".
- **Where the number is assigned:** only in the point-of-sale order-create
  service (`POST /api/orders`; today POST `/ingest`,
  `app/web/blueprints/ingest.py:540`), right before
  `insert_order`, as the **last** statement before it so the row lock is held
  briefly. Never in `process_rows` (it groups rows by `order_number` and can
  create several orders per call, `app/core/ingestion.py:226-234`) and never
  in `app/migrate.py` (historic import, `migrate.py:232,252`), which keep
  using the numbers they are given.
  - With the flag on, the create endpoint makes exactly one order per request and
    rejects ALISTAMIENTO rows (they create no order, `ingestion.py:203`, and
    would burn a number); blank preparation stays in `/inventory`.
  - The create service returns `order_id` and the assigned number to the
    caller (today `Summary` returns neither); the send-after-commit and the
    declaration link use them.
  - `_mark_declaration_attended` (`ingest.py:493`, callers at `:590`, `:631`)
    links the declaration by `order_id` instead of looking the order up by
    `rows[0].order_number`.
- The number is stored as text in `production_orders.order_number` (the
  column stays `TEXT`; historic values like `NT 895` remain valid).
- Uniqueness: the number is also stored in `siigo_transfers.number`
  (`UNIQUE`), which protects the new range without touching historic data
  (`order_number` is not unique today and must stay that way for old rows).

### 5.2 Table `siigo_transfers`

| Column | Type | Notes |
|---|---|---|
| `id` | `SERIAL PK` | |
| `order_id` | `INTEGER NOT NULL UNIQUE REFERENCES production_orders(id)` | `ON DELETE RESTRICT`: an order with a transfer cannot be deleted silently (§5.7) |
| `number` | `INTEGER NOT NULL UNIQUE` | the order number sent to Siigo |
| `status` | `TEXT NOT NULL` | `pending`, `sent`, `error`, `skipped` (order with nothing to move; keeps the number audited). Validated in Python like the rest of the schema (`docs/DATABASE.md:244`: no CHECK constraints), unless that rule is changed explicitly there |
| `payload` | `JSONB NOT NULL` | exactly what was / will be sent, for audit and retries |
| `siigo_id` | `TEXT` | journal GUID |
| `siigo_name` | `TEXT` | e.g. `CC-100-3875` |
| `error_message` | `TEXT` | Spanish message shown to staff + Siigo code |
| `attempts` | `INTEGER NOT NULL DEFAULT 0` | |
| `created_by` | `INTEGER REFERENCES users(id)` | |
| `created_at`, `sent_at`, `last_attempt_at` | `TIMESTAMP` | |

Index on `status` for the pending list.

### 5.3 Product mapping

- Migration: `plate_types.siigo_product_code TEXT`, seeded with the table in
  §2.4. A plate type without a code blocks the transfer with a clear error
  (no silent skip).

### 5.4 Payload builder (pure function, strictly typed)

Input: order number, order date, list of plate lines `(plate_type, quantity,
plate_number, plate_to, city, is_voided, ...)`. Output:

```json
{
  "document": {"id": 74172},
  "date": "<order_date YYYY-MM-DD>",
  "number": 3875,
  "observations": "<generated, §5.5>",
  "items": [
    {"account": {"code": "14300101", "movement": "Credit"},
     "customer": {"identification": "900773756", "branch_office": 0},
     "product": {"code": "000452", "quantity": 2.0, "warehouse": 31},
     "value": 0},
    {"account": {"code": "14300101", "movement": "Debit"},
     "customer": {"identification": "900773756", "branch_office": 0},
     "product": {"code": "000452", "quantity": 2.0, "warehouse": 32},
     "value": 0}
  ]
}
```

- **Two items per Siigo product** (credit at 31, debit at 32), quantities
  summed per product across the order's plates. Items ordered by product
  code so the payload is deterministic.
- Excluded: plates with `is_voided = true` (order annulled, blank not
  consumed). *To verify with production:* whether `is_plate_annulled` plates
  (blank consumed) and `is_batch_replacement` rows count; default = they count,
  because a blank was produced.
- **Quantity unit — must be settled before task 3.** The code counts
  physical plates in `plates.quantity`: `max_plates_per_card` gives 2 for
  cars (`app/core/normalize.py:147-149`) and declaration orders copy
  `plate_quantity` into `quantity` (`ingest.py:465`). But NT-3874 shows
  quantity `1.00` for "1P PARTICULAR" (one car sale, RC-2-652 = one unit at
  130 000). Check the stored `quantity` of the CEU215 order (NT 3874) in
  production and 2-3 other car orders, then decide the mapping (likely Siigo
  quantity = number of sales units, i.e. car plates / 2). Pin it with a test.
- Value is always `0` (§9).
- An order whose lines all map to zero quantity → nothing is sent; insert the
  `siigo_transfers` row with status `skipped` so the number is not a silent
  hole, and show "Sin placas para trasladar".

### 5.5 Observations

Same text staff type today, generated, max 4 000 chars (truncate with
`…`). One segment per plate line, joined by `; `:
`<quantity>P <short type> <plate or range> <city>`, e.g.
`1P PARTICULAR CEU215 EL CERRITO`, `2P MOTO ABC12D`, `10P PARTICULAR
ABC100 A ABC109 CALI`. Short types: `PARTICULAR`, `PÚBLICO`, `MOTO`,
`MOTOCARRO PARTICULAR`, `MOTOCARRO PÚBLICO`, `REMOLQUE`, `ORNAMENTAL`.
*To verify with production staff:* the exact wording they read today
(collect 5 real NTs with several types before coding the formatter).

### 5.6 Sending and retrying

1. In the order-create request, after the order **and** a `pending`
   `siigo_transfers` row (with its payload) are committed, call
   `create_journal`.
2. `201` → `status = sent`, store `siigo_id`, `siigo_name`, `sent_at`.
3. `400 already_exists` on `number` → the journal already exists (an earlier
   attempt reached Siigo but the response was lost). Call
   `find_journal("CC-100-<number>")` (list `GET /v1/journals` filtered by
   date/created date and match `name`); if found, mark `sent` with its id;
   if not found, mark `error` with "El número ya existe en Siigo" for a
   person to check.
4. Other `4xx` → `error` with Siigo's message; no automatic retry (the data
   is wrong and needs fixing).
5. Timeout / `5xx` / `429` / network → stays `pending`, `attempts + 1`.
6. The order page shows the state: "Traslado CC-100-3875" (sent),
   "Traslado pendiente" or "Error en el traslado: <message>", with a
   **Reintentar** button for `pending`/`error` (permission `orders.capture`).
   Retry rebuilds the payload from the current order (in case it was
   corrected), stores it, and repeats steps 2-5 with **the same number**.
   Retry first locks the transfer row (`SELECT … FOR UPDATE`) and does nothing
   if it is already `sent`, so a double click never sends twice.
7. A list "Envíos a Siigo" (all `pending`/`error`) for admins.
   Optional later: a periodic job that retries `pending` rows; not in the
   first version.

The Siigo call happens **after** the DB commit and outside any DB
transaction, so a slow Siigo never holds a row lock.

### 5.7 Editing, replacing, deleting and voiding orders

Once a transfer is `sent`, the Siigo document reflects the plates at that
moment. The rule is **not** "no edits": it is "the journal must not change".

- One service function, `assert_transfer_unchanged(conn, order_id,
  new_state)`, rebuilds the payload **items and date** from the edited state
  and rejects the change only if they differ from the sent payload, with
  "Esta orden ya tiene traslado en Siigo (CC-100-3875). Para cambiar placas o
  cantidades, pide a contabilidad anular el traslado." So recovering the plate
  numbers of a reposición batch (one row qty N → N single rows, same totals)
  is still allowed; plate numbers, cities and other fields stay editable
  (they only change the observations, which are not resent).
- Every path that changes plates or orders calls it (in the `orders`
  service, not per route):
  - order update, `PUT /api/orders/{id}` (today `record_save`,
    `app/web/blueprints/records.py:177`);
  - plate update, `PATCH /api/plates/{id}` (today `/review/update`,
    `app/web/blueprints/review.py:24-37`, changes `plate_type`, `quantity`,
    `is_voided` through `db.update_plate`);
  - plate delete, `DELETE /api/plates/{id}` (today `/review/delete`,
    `review.py:50-56`).
- `order_number` is **read-only for any order with a transfer row** (any
  status: retries reuse `siigo_transfers.number`). With the flag on,
  `record_save` (`records.py:192,209`) also rejects setting a numeric
  `order_number` above the cutover number on any other order, so old orders
  cannot be renumbered into the new range.
- `replace_order` (`app/core/ingestion.py:254`) deletes and recreates the
  order with a new id: forbidden for an order with any `siigo_transfers` row
  (checked first, Spanish message).
- Delete (`/records/delete`, `records.py:302-307`, and `replace_order`): the
  orders service checks the transfer **before** deleting and shows a Spanish
  message; the FK `ON DELETE RESTRICT` is only a backstop (today
  `records_delete` has no try/except, so an FK error would be a 500).
  - `sent` → refused.
  - `error` → allowed; the `siigo` service deletes the transfer row first.
  - `pending` → allowed only after `find_journal` confirms Siigo has no
    `CC-100-<number>` (a timeout may have created it); otherwise mark it
    `sent` and refuse.
  - The number is **not** reused: the counter only moves forward; the gap is
    accepted.
- Voiding an order (all plates `is_voided`): no automatic reverse journal in
  the first version; the order page warns "Pide a contabilidad anular
  CC-100-…". *Open question for accounting:* should plates later create a
  reverse journal (debit 31 / credit 32) or delete the journal through the
  API (*to verify:* whether `DELETE /v1/journals/{id}` exists)?
- While `pending`/`error`, everything stays editable; the retry sends the
  current state.

### 5.8 Order form changes

With `SIIGO_TRANSFERS_ENABLED`:

- Next.js point of sale: the order-number field becomes read-only text
  "Se asigna al guardar"; the `POST /api/orders` schema stops requiring it
  (today `_rows_from_form`, `app/web/blueprints/ingest.py:93`). The number
  shown after saving is the assigned one.
- Customer portal pedidos (`docs/CUSTOMER_PORTAL.md`) become orders only
  through the point of sale, so they get the assigned number, the transfer
  and billing like any other order. The portal already requires the
  customer's document type and number, which §6.2 needs.
- Pedido upload (`pedido_upload.py`, `pdf_extract.py` read `ORDEN DE
  PRODUCCION`/`NT` from the document): the extracted number is ignored for new
  orders (show it as "Número en el documento: …" for reference only).
- The flows keyed on a typed number are switched off for new orders with the
  flag on: the order-number collision check, `confirm_order_duplicate` and
  the "Reemplazar" path (`ingest.py:575-615`), and the PDF-preview duplicate
  check (`ingest.py:691`). "Reemplazar" is hidden and rejected server-side.
  Recovering plate numbers for a reposición batch goes through
  `/records/<id>/save`.
- Plate-number duplicate checks (`find_duplicate_plate_numbers`) are
  unaffected.

## 6. Receipt and invoice

Built after the transfer is live. Common parts first.

### 6.1 Payment data on the order

- `normalize.PAYMENT_METHODS` adds `TARJETA DÉBITO`, `TARJETA CRÉDITO`.
  `OTROS` stays valid for old orders but leaves the select for new ones and
  cannot be billed. `PAYMENT_METHODS` also validates `record_save`
  (`records.py:41`), so keep it complete and add a separate
  `PAYMENT_METHODS_FOR_NEW_ORDERS` tuple for the select.
- Migration: `production_orders.transfer_account TEXT` (CHECK in
  `AHORROS 7716`, `CORRIENTE 9655`, `BANCO BOGOTÁ`), required in the form
  only with `TRANSFERENCIA`; `production_orders.due_date DATE`, shown with
  `CRÉDITO`, prefilled with order date + 30 days, editable.
- `cash_summary` groups the new methods like the others.

### 6.2 Customer in Siigo

- Lookup by **document number**, never by name (plates names differ from
  Siigo, e.g. "JOANY ANDRES BOLIVAR" vs "GEOVANY ANDRES BOLIVAR AGUDELO" for
  the same ID). `find_customer(identification)`; if found, use it as is.
- If missing, the preview asks for what Siigo requires and plates does not
  have: person type (natural/jurídica), first and last names separately (or
  company name), document type mapped to Siigo id types (`13` cédula,
  `31` NIT, …), check digit for NIT (computed by plates, DIAN algorithm),
  fiscal responsibility (default `R-99-PN`), email/phone/address if present.
  *To verify in the official docs before coding:* the exact required fields of
  `POST /v1/customers` (address/city DANE code).
- New optional column `customers.siigo_id TEXT` caches the Siigo GUID.
- Billing is blocked if the customer has no document type and number.

### 6.3 Cash receipt (EFECTIVO → RC-2)

- `POST /v1/vouchers`, document id `42477`, type `AdvancePayment`
  (what the real RC-2 receipts use), customer identification, payment
  `13960` with the order total, observations = the same generated text as the
  transfer. *To verify in the official docs:* the exact voucher body for
  `AdvancePayment` (fields `type`, `payment`, `items`/`value`).
- Total = sum of `unit_price * quantity` of plates that are neither
  `is_voided` nor `is_plate_annulled`, the same rule as `/cuadre`
  (`app/core/cash_summary.py:118-119`) (+ extra items once §6.6 exists).
  Blocked if any line has no price.
- One receipt per order in this version (abonos are undecided; the table
  allows several documents per order later, see §6.5).

### 6.4 Electronic invoice (TRANSFERENCIA / CRÉDITO / TARJETA → FV-3)

- `POST /v1/invoices`, document id `42461`, customer, seller (§6.6), items
  `{code: <siigo product>, quantity, price: <base without IVA>, taxes:
  [{id: 11056}]}`, payment = id from §2.4 with the total (with `due_date`
  for credit), observations starting with
  `SOMOS REGIMEN SIMPLE DE TRIBUTACIÓN` + generated text.
- IVA: plates prices are final prices with IVA (130 000). Base =
  `price / 1.19` with `Decimal`, rounded as Siigo does (6 decimals for the
  unit price, 2 for totals). *To verify with one real invoice:* 130 000 with
  IVA gives exactly 130 000 in Siigo (compare with an existing FV-3).
  The broader "IVA per line, editable" decision (plates.tax_rate /
  tax_included) is only needed when products with other tax treatments
  appear; keep it out of the first version unless a plate product needs it.
- Idempotency: send a stable idempotency key per document (*to verify:* the
  header name Siigo accepts; if none, rely on the `siigo_documents` status
  row + a lookup before retrying, as for transfers).

### 6.5 Table `siigo_documents`

`id`, `order_id` FK (`RESTRICT`), `kind` (`receipt`/`invoice`), `status`
(`pending`/`sent`/`error`), `payload JSONB`, `siigo_id`, `siigo_name`
(`RC-2-…`/`FV-3-…`), `total NUMERIC(14,2)`, `error_message`, `attempts`,
`created_by`, `created_at`, `sent_at`. Partial unique index: one non-error
document per order and kind (`UNIQUE (order_id, kind) WHERE status <> 'error'`)
until abonos are designed. On success also fill
`production_orders.receipt_type`/`receipt_number` as staff do today
(`RECEIPT_TYPES`, `app/core/normalize.py:224`): receipt → `RECIBO` +
`RC-2-<n>`; invoice → `FACTURA` + `FV-3-<n>`.

Once a document is `sent`: lock price, quantity, payment method, transfer
account and customer on the order (same mechanism as §5.7).

### 6.6 Sellers and extra products (later tasks)

- `users.siigo_seller_id INTEGER` + admin field; billing blocked without it.
- Extra items sold with plates (FLETES `000361`, PORTA PLACA MOTO `000644`,
  PORTA PLACA CARRO `000646`, TERCERA PLACA `001092`, JUEGO TERCERA PLACA
  `000474`, MATRÍCULA INICIAL `000924`/`000925`/`001109`): table
  `order_items` (order_id, product_code, description, quantity, unit_price),
  included in receipt/invoice and `/cuadre`. They are **not** part of the
  transfer (not produced plates).

## 7. Endpoints

JSON only (migration API conventions: resources, `{"error": {"code",
"message"}}` errors with Spanish `message`, lists as `{"items": [...]}`,
the custom-header CSRF rule of `docs/MIGRATION_PLAN.md`):

| Endpoint | Permission | Does |
|---|---|---|
| `POST /api/orders/{id}/siigo-transfer` | `orders.capture` | retry the transfer; `200` with the transfer |
| `GET /api/siigo-transfers?status=pending` | `siigo.manage` | pending/error transfers ("Envíos a Siigo" screen) |
| `GET /api/orders/{id}/siigo-document-preview` | `siigo.bill` | preview receipt/invoice |
| `POST /api/orders/{id}/siigo-documents` | `siigo.bill` | create it; `201`; `409` if one already exists; `502` code `siigo_unavailable` when Siigo is down |

The order resource (`GET /api/orders/{id}`) includes the transfer state and
the payment document, so the order detail needs no extra call.

New permissions (migration adding rows to `permissions`, mirrored in the
permission catalog of the `auth` domain; update the docs list): `siigo.bill`
"Facturar y hacer recibos en Siigo", `siigo.manage` "Ver y reintentar envíos
a Siigo". They take the **next free `sort_order` values** after the customer
portal's `customer_pedidos.review` (phase 7), not 14/15. Which roles get
them by default is decided with the owner (proposal: `siigo.bill` to
point-of-sale roles, `siigo.manage` to admin).

## 8. Tests

- No test ever calls Siigo: monkeypatch `client._request`.
- New tables go into `TABLES` in `tests/conftest.py`; schema changes are a new
  migration + `init.sql` (including its `schema_migrations` row) +
  `docs/DATABASE.md`.
- Payload builder: unit tests per plate type, mixed orders, voided plates,
  deterministic ordering, observations formatting, truncation.
- Counter: two concurrent saves get consecutive numbers; a rolled-back save
  does not consume a number; missing counter row → clear error. The
  concurrency test cannot use `clean_db` (it pins every `get_conn` to one
  shared connection with savepoints, `tests/conftest.py:253-262`): it uses
  two real pool connections with its own setup and teardown.
- `order_number_counter`, `siigo_transfers` and `siigo_documents` are added
  by name to `TABLES` (`tests/conftest.py:145`).
- Sending: `201`, `already_exists` (found / not found), other `400`,
  timeout, `401` → token refresh, `429`. The order is saved in every case.
- Locks: `record_save`, `/review/update`, `/review/delete`, `replace_order`,
  delete with `sent` / `error` / `pending` transfers, renumbering into the new
  range, the reposición-batch recovery that must stay allowed.
- `POST /api/orders` with the flag on: one order per request, ALISTAMIENTO
  rejected, no "Reemplazar", declaration linked by `order_id`.
- API tests with the Flask test client for every new endpoint (permissions
  included), and a Playwright end-to-end test of "save order → transfer
  state shown" with Siigo mocked at the client.
- Flags off: the existing suite passes unchanged; no Siigo call is made.
- Smoke script (manual, read-only): auth + `GET /v1/document-types?type=CC`
  shows id 74172 with `automatic_number: false`.

## 9. Deferred: cost per plate

Accounting mentioned that the monthly average cost per plate (≈ 3 000 in
November, without IVA) is entered by hand today. The NT itself carries
value 0, and the API does not expose the average cost. Decision: transfers
stay at value 0; the cost topic is revisited with accounting later (likely a
monthly "costo por tipo de placa" table that accounting fills in plates).

## 10. Go-live (cutover)

1. Accounting deletes CC-100-1 and CC-100-99999.
2. Deploy with both flags off; set the Siigo variables in `ENV_FILE_B64`.
3. Agreed day, end of shift: staff report the **last NT number** made by
   hand; from then on no NT is made in Siigo for plates.
4. An admin runs the cutover command (new):
   `python -m app.siigo.cutover --last-number <N>` (or the `core` path),
   which inserts the `order_number_counter` row and refuses to run twice or
   with a number lower than the highest numeric `order_number` in plates
   (parsing values like `NT 895` too). A second mode,
   `--advance-to <N>`, only moves the counter forward and only with the flag
   off (used after a rollback, §10).
5. Turn on `SIIGO_TRANSFERS_ENABLED`, redeploy.
6. First real order with accounting present: compare `CC-100-<N+1>` with a
   manual NT (lines, warehouses, quantities, observations).
7. Billing: turn on `SIIGO_BILLING_ENABLED` later, after the first receipt
   and the first invoice were checked against hand-made ones with accounting.

Rollback: resolve every `pending`/`error` transfer first, then turn the flag
off; staff go back to manual NTs, continuing from the counter's
`last_number`. To come back later, run `--advance-to <last manual NT>` and
turn the flag on again.

## 11. Tasks (one PR each, in order)

| # | Task | Depends on |
|---|---|---|
| 1 | Siigo client (`client.py`, config, errors, token cache, tests, `.env.example`) | none |
| 2 | Order-number counter + cutover command (`--last-number`, `--advance-to`); `POST /api/orders` + Next point of sale behind `SIIGO_TRANSFERS_ENABLED`: number assigned in the create service, one order per request, ALISTAMIENTO rejected, collision check / Reemplazar / PDF-preview check off, declaration linked by `order_id` (no Siigo calls yet) | none |
| 3 | `plate_types.siigo_product_code`, payload builder + observations formatter (pure, tested); quantity unit settled first (§5.4) | 1 |
| 4 | `siigo_transfers` table (incl. `skipped`), send on save (5 s budget), retry button with row lock, transfer state in the order resource and the Next order detail, `already_exists` handling, delete/replace checks in the orders service | 2, 3 |
| 5 | `assert_transfer_unchanged` on `record_save`, `/review/update`, `/review/delete`; `order_number` read-only / no renumbering into the new range; "Traslados pendientes" list; `siigo.manage` permission | 4 |
| 6 | Payment methods (cards), `transfer_account`, `due_date` | none |
| 7 | Siigo customer lookup/creation + preview fields, `customers.siigo_id` | 1 |
| 8 | `siigo_documents`, cash receipt RC-2 (preview, confirm, locks), `siigo.bill` permission | 6, 7 |
| 9 | Electronic invoice FV-3 + `users.siigo_seller_id` | 8 |
| 10 | Extra products (`order_items`) | 9 |
| — | Docs, in the PR that changes each: `docs/DATABASE.md`, `docs/CORE.md` (permissions list), `docs/WEB.md` (routes), `docs/MIGRATION_PLAN.md` (`siigo/` domain + endpoints) | each task |

All tasks start after phases 1-7 of `docs/MIGRATION_PLAN.md` are done; each
task includes its API endpoints, schemas and Next.js screen changes (no
Jinja). Tasks 1-5 deliver the NT replacement and can go live alone.

## 12. Open questions

| Question | Who | Blocks |
|---|---|---|
| Does `is_plate_annulled` / `is_batch_replacement` count in the transfer? | production | task 3 |
| Exact observations wording (5 sample NTs) | production | task 3 |
| `plates.quantity` counts physical plates (car = 2) but NT-3874 has 1 for one car sale: confirm with the CEU215 order's data | dev + production | task 3 |
| Default roles for `siigo.bill` / `siigo.manage` | owner | task 5, 8 |
| Voided order: reverse journal, API delete, or manual? | accounting | after task 5 |
| Partial payments (abonos): several receipts per order? | owner | task 8 (single receipt until then) |
| Required fields of `POST /v1/customers` and the `AdvancePayment` voucher body | dev, official docs | tasks 7, 8 |
| Idempotency header for invoices | dev, official docs | task 9 |
| Monthly cost per plate | accounting | none (deferred) |
