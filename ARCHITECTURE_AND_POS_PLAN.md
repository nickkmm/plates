# Plan: full sales order capture (POS) + automation + cash reconciliation

## Context

Today the app only tracks plate *production* (an internal tool, for
lookups): order number, date, requester, plate type/number, city,
quantity. The user wants to expand it so it also captures the
**full sales order** that arrives from the point of sale — with customer,
document, payment method, receipt, price — because all of that
information is lost today. The explicit goals, in order:

1. Add the new fields to the data model and to the `/ingest` flow.
2. Automate their capture (as the PDF already does today for order_number/
   date/requester).
3. An end-of-day summary (everything entered + everything sold) to
   automate the manual **cash reconciliation** (cuadre de caja) done today —
   the main reason for this whole feature.

The new fields come from `data/ENCABEZADO BASE DATOS NUEVO FEAT.xlsx`
cross-checked against the real PEDIDO (order) format (`data/FORMATO_PEDIDOS_INGSE_2026_v2.xlsx`,
the same PDF that `pdf_extract.py` already parses today). Every assumption in
this document was verified against real evidence: `pdfplumber` run against
the two real PEDIDO files in `data/`, and queries against the live
production database. All business decisions were confirmed by the user
field by field — nothing was assumed.

## Naming (read first)

**See `CLAUDE.md`** (in the repo): every table/column/code identifier
is in **English**; the content that the user reads or types (UI, texts, and the
*values* stored in vocabulary columns — payment methods, plate
types, roles) stays in **Spanish**.

Phase 1 was built in Spanish before this convention was set; Phase 1c
already renamed everything to English **and is in production**. This whole
document uses the final English names.

## Current state of the repo (verified against HEAD, not against the plan)

**Already merged and deployed to production** — do not build it again:

- **Phase 1** (PR #14): tables `customers`/`users`/`plate_type_prices`/
  `schema_migrations`, new POS columns in `production_orders`/`plates`,
  index `plates_order_id_idx`, runner `app/schema_migrate.py`,
  `app/migrations/0001_pos_fields.sql`, customer/user backfill.
- **Phase 1b** (PR #16): table `plate_types` + real FKs
  (`plates.plate_type`, `plate_type_prices.plate_type`,
  `blank_preparations.category`), `app/migrations/0002_reference_integrity.sql`.
- **Phase 1c** (PR #17): full rename to English,
  `app/migrations/0003_rename_to_english.sql`. The complete rename map
  lives in that file and in `docs/DATABASE.md` — it is not repeated here.
- **Phase 6** (PR #13): cleanup of emojis/decorative copy in the existing
  templates. Confirmed with grep: no emoji is left today in
  `app/web/templates/`.
- The step `docker compose exec -T web python -m app.schema_migrate` **is
  already** in `.github/workflows/deploy.yml`, right after
  `docker compose up -d --build`.
- `init.sql` and `docs/DATABASE.md` already reflect the final state of 1/1b/1c.

**Still to build**: Phase 1d (architecture refactor), Phase 1e
(monitoring), Phase 2 onward (the POS feature itself).

Reference numbers verified at HEAD (`wc -l` / `grep`), so that an implementer
does not work against stale figures:

| File | Lines | Note |
|---|---|---|
| `app/web/app.py` | 687 | 19 routes |
| `app/core/db.py` | 556 | 24 functions (incl. `get_conn`) |
| `app/core/process.py` | 237 | `conn.commit()` at 141 and 235 |
| `app/web/auth.py` | 148 | not restructured |
| `app/core/normalize.py` | 341 | without the new vocabularies yet |
| `tests/web/test_app.py` | 1672 | |
| `tests/core/test_db.py` | 855 | ~189 `db.<func>(` calls |
| `tests/core/test_process.py` | 517 | ~35 calls to process_rows/replace_order |
| full suite | 512 tests | |

## ⚠️ Minor unresolved questions — reasonable defaults, not blocking

- **B4**: "everything entered" in the end-of-day summary — does it mean the
  day's preparation (`blank_preparations`) or the orders loaded that
  day? Default: the orders (already covered by the cash reconciliation query).
- **B5**: if a customer's name/data is edited from an already-saved order,
  that changes the shared history of ALL their orders.
  Recommended default: `record_save` can only change *which customer* the
  order points to (re-resolve `customer_id`), not edit the customer's own
  attributes — that is left for a future dedicated customers screen,
  out of scope for this feature.

## Final data design

> The tables and columns in this section **already exist in production**
> (Phases 1/1b/1c). They are documented here because Phases 2-5 are built
> on top of them; the authoritative schema is `init.sql` + `docs/DATABASE.md`.

### Table `customers`

`requested_by` (the requester — PTO.V, MUVA, etc.) and the "customer" in the
Header are **the same entity**, not two different things.

```sql
customers
  id                SERIAL PRIMARY KEY
  name              TEXT NOT NULL UNIQUE   -- canonical: normalize_requested_by(value)
  type              TEXT                    -- normalize.CUSTOMER_TYPES
  document_type     TEXT                    -- normalize.DOCUMENT_TYPES
  document_number   TEXT                    -- always TEXT: a NIT carries check digits/dashes
  document_relation TEXT                    -- ONLY if document_type = CÉDULA
  phone             TEXT
  email             TEXT
  transit_license   TEXT                    -- ONLY if type = PERSONA NATURAL
  created_at        TIMESTAMP DEFAULT now()
```

(The *values* of `type`/`document_type`/`document_relation` are Spanish
business terms — `PERSONA NATURAL`, `CÉDULA`, `DUEÑO` — only the table/column
names are English, per `CLAUDE.md`.)

The `UNIQUE` on `name` is the only uniqueness constraint in the schema
outside PKs — it is what gives "look up customer by name and reuse their
data" a clear definition, and it provides the lookup index for free.
**Collision policy**: two distinct real customers who type the same
canonical name are treated as the SAME customer — that is today's behavior
(`normalize._REQUESTED_BY_MAP_RAW` already deliberately leaves variants such as
`MUVA GRANADA`/`MUVA SERGIO` unmerged); to separate them, a qualified name is
typed (`PTO.V FABIO M`). No new mechanism is needed.

- **`production_orders.requested_by` (text) IS KEPT, `NOT NULL`, and is
  still always written** — it is a denormalized copy of
  `customers.name`, and it remains the source used by **8 read queries in
  `db.py`** (`recent_orders`, `find_orders_by_number`,
  `search_orders_by_number`, `search_plates_by_number`,
  `find_orders_for_plate`, `review_rows`, `order_detail`,
  `distinct_requested_by`) **and the 5 templates that display or edit it**
  (`_orders_table.html`, `record_detail.html`, `review.html`, `records.html`,
  `ingest.html`) — none of that is touched by this feature.
  **Rule: every write that sets `customer_id`
  also sets `requested_by = customers.name` in the same operation.**
  Already documented as a quirk in `docs/DATABASE.md`.
- `customer_id` on `production_orders` is **nullable** (the backfill left 1
  order unresolved; see Executed phases).
- In `/ingest`, if the typed name matches `customers.name` (lookup by
  `normalize_requested_by(typed)`), its data (type/document/phone/email/
  transit license) is **reused automatically**. If it does not
  match, it is treated as a new customer.
- Conditional fields (`document_relation` only if CÉDULA;
  `transit_license` only if PERSONA NATURAL): in the HTML they are `disabled`
  (not `readonly` — `readOnly` has no effect on `<select>`, unlike the
  trick `line_type` already uses with text inputs). A `disabled` field is
  not sent in the POST, which is correct here because the value must
  stay `NULL`. **The rule is also enforced on the server** (never
  trust the HTML alone): force `document_relation = NULL` if
  `document_type != 'CÉDULA'`, and `transit_license = NULL` if
  `type != 'PERSONA NATURAL'`.

### POS columns on `production_orders` (one per order)

`receipt_type`, `receipt_number`, `customer_id` (FK → `customers.id`),
`payment_method`, `tracking_number`, `client_order_number`, `delivery_date`,
`payment_date`, `dispatched_by`, `received_by`, `notes`, `created_by`
(FK → `users.id`).

Mapping to the original Excel/PDF, for traceability: `receipt_type`/
`receipt_number` = `COMPROBANTE`; `payment_method` = `FORMAS DE PAGO`/
`FORMA DE PAGO`; `tracking_number` = `GUÍA #`; `client_order_number` =
`PEDIDO #` (the customer-facing claim number, distinct from the real
`order_number`); `delivery_date` = `FECHA ENTREGA`; `payment_date` =
`FECHA TRANSF./PAGO`; `dispatched_by` = `RESPONSABLE DESPACHO`;
`received_by` = `NOMBRE QUIEN RECIBE EL PEDIDO`; `notes` = `OBSERVACIONES`.

The `TRÁNSITO:` field in the PDF header is dropped — redundant with the
customer name and rarely filled in. It is not mapped to any field.
(Note: `TRÁNSITO` also appears as one of the possible *values* of
`customers.type` — a name coincidence, unrelated to this decision.)

### POS columns on `plates` (per plate/line, not per order)

`unit_price NUMERIC(12,2)`, `reason` (deterioro/pérdida/otros — maps to
`MOTIVO`/`FABRICADA POR` in the Excel; i.e. deterioration/loss/other), `vehicle_brand` (maps to
`MARCA VEHICULO/MOTO`).

`reason` covers the `FABRICADA POR` of the draft Excel: **it is not a
separate field and has no relation to `prepared_by`** (which is who
prepared the blank, a different thing).

`reason` and `vehicle_brand` are vehicle data, not order data — one
order can cover several plates for different vehicles, so they go
in `plates`. (`transit_license` does NOT go here — it is data about the
natural-person *customer*.) Neither has a printed field in the PDF's
lines table (that table only has ITEM/PLACA/CIUDAD/CANT./type/range/
VALOR PLACA/VALOR TOTAL) — they are **always captured manually**.

**Re-snapshot rule for `unit_price`** (one rule, applied in three places):
- New plate (`/ingest`, PDF, or "+ agregar placa" in `record_save`) →
  takes the current rate for its `plate_type` at that moment.
- Existing plate edited and the `plate_type` **does not change** → `unit_price`
  is NOT touched, regardless of whether `quantity` changes (the total is
  recalculated on its own).
- Existing plate edited and the `plate_type` **does change** → the current
  rate for the new type is taken again.

Applies to both `db.update_plate_full` (Record detail) and
`db.update_plate` (the `/review` queue, which also allows changing
`plate_type`) — both need to read the old `plate_type` before the
`UPDATE` to know whether it changed.

### Table `plate_type_prices` (rates, admin-editable)

```sql
plate_type_prices
  plate_type TEXT PRIMARY KEY REFERENCES plate_types(plate_type)
  unit_price NUMERIC(12,2) NOT NULL
  updated_at TIMESTAMP DEFAULT now()
  updated_by TEXT
```

**Watch out for the two similar "namespaces"** (see
`docs/DATABASE.md`): `plates.plate_type` (7 granular values) vs.
`inventory_categories.category` (3 materials). This table is keyed on the 7
types, because the price is billed per plate type sold, not per
inventory material — mixing them up would leave `ORNAMENTAL` unable to have a
price and would give `REMOLQUE` the price of `CARRO PÚBLICO`. Since Phase
1b there is a **real FK** to `plate_types`; the Python validation against
`normalize.PLATE_TYPES` stays the same (double safety net, with a better
error message before reaching the DB).

**It is not seeded in `init.sql`** — the table starts empty. `/precios` shows
one row for each entry in `PLATE_TYPES` (read from the constant in
code), marking as "sin tarifa" (no rate) any that does not yet have a row. That way the UI never
depends on seeded data and the tests' `TRUNCATE` breaks nothing. A
plate with no configured rate stores `unit_price = NULL` and triggers a warning
in `/cuadre` (never a silent 0).

**It is a SUGGESTED value, not enforced**: the real price varies by customer and
by negotiated discounts — that explains, without needing to guess, the
price difference between the two sample PEDIDO files (different negotiation,
not a capture error). In `/ingest`, when the
`plate_type` of a line is chosen, the price field is pre-filled with this rate
via JS, but **stays editable**. `plates.unit_price` stores what was typed
at that moment — not a live reference to `plate_type_prices`, so
if the rate changes later, old orders are not altered. The total
is always `unit_price × quantity`, computed on read, never stored; if a
specific case needs a different total, the `unit_price` of that line is adjusted.

**Price and `recalc_inventory` are separate things and must stay
separate**: `recalc_inventory`/`_consumed_by_material`/`inventory_view`
aggregate QUANTITIES by MATERIAL (3 categories); price aggregates MONEY by
TYPE (7 values). Never "unify" these two loops.

### Table `users` (real RBAC, replaces the env vars)

```sql
users
  id SERIAL PRIMARY KEY
  username TEXT UNIQUE NOT NULL
  password_hash TEXT NOT NULL   -- same werkzeug format as today (scrypt/pbkdf2)
  role TEXT NOT NULL            -- Spanish value, see the note on accents below
  active BOOLEAN DEFAULT true
  created_at TIMESTAMP DEFAULT now()
```

- The backfill (already run) created one user for each entry in
  `AUTH_USERS`/`AUTH_ROLES`, with the same hashes — nobody's password
  needs to be reset.
- **Pending (Phase 2)**: `app/web/auth.py` moves from reading the env vars to
  querying `users`. **Risk to manage**: login now depends on the
  DB (today it works even if the DB is down) — acceptable given that the
  rest of the app already depends on the DB for everything else.
- ⚠️ **Accents in role values — verify before writing code**:
  the literal value in code and in `AUTH_ROLES` today is **`produccion` without
  an accent** (`auth.py:30` → `ROLE_PRODUCCION = "produccion"`; `tests/conftest.py:55`
  → `AUTH_ROLES=...:produccion`; `init.sql` comment → `admin /
  produccion / consulta / punto_venta`). The prose of `docs/WEB.md`,
  `docs/README.md` and of this plan writes "producción" with an accent because it is
  ordinary Spanish, but **the stored and compared value has no accent**.
  When building the `ROLES` tuple and when reading/writing `users.role`, use
  exactly the literals that already exist: `"admin"`, `"produccion"`,
  `"consulta"`, `"punto_venta"`. Changing them would break the login of
  existing users.
- **New role `punto_venta` (PTO.V) — scoped permission**: today `auth.py` only
  has two levels (`FULL_ACCESS_ROLES = {admin}` via `@login_required`, and
  "any authenticated role" via `@consulta_allowed`). A third,
  intermediate level is needed:
  - `POS_ROLES = {"admin", "punto_venta"}` + new decorator `@pos_allowed`,
    applied to: `/ingest` (GET/POST), `/ingest/pdf` (GET/POST),
    `/records/<id>/save`, and `/cuadre` (view + PDF export).
  - `@login_required` (remains admin-only, no behavior
    change) stays on: `/review/*`, `/inventory/alistado`,
    `/records/delete`, `/precios`.
  - `@consulta_allowed` does not change — `punto_venta` falls there too for
    reading (`/records`, `/records/<id>` in read mode), since that
    decorator lets "any authenticated role" through by design.
  - The existing `produccion` role **does not change** — it stays read-only,
    like `consulta`.
- `users.role` is free text validated against a `ROLES` tuple in
  `auth.py` (same pattern as `normalize.PLATE_TYPES`), not a `CHECK` in the
  DB.
- `production_orders.created_by` (FK → `users.id`): the logged-in user who
  pressed "Guardar" on the order (distinct from `dispatched_by`/`received_by`,
  which are physical delivery data). `process_rows`/`replace_order` today
  receive no notion of "who is calling" — they gain a parameter
  `created_by: int | None` that the route passes from `session["user_id"]` (the
  login now stores the numeric id in the session, not just the username).

### Migration runner (`app/schema_migrate.py`) — already in production

```sql
schema_migrations
  version TEXT PRIMARY KEY
  applied_at TIMESTAMP DEFAULT now()
```

Runner: `python -m app.schema_migrate`. The name is deliberately different from
`app/migrate.py`, which already exists and does something completely different
(one-off migration of the historical Excel, invoked against production);
confusing the two commands would be a very costly mistake.

Rules any future migration must respect:
- The `.sql` files go in `app/migrations/NNNN_name.sql`, **not** in a
  `migrations/` at the root — `web.Dockerfile` only does `COPY app/ ./app/`,
  so anything outside `app/` does not reach the image.
- Each file is applied **and** recorded in `schema_migrations` within
  the same transaction (a half-applied file is never marked as
  complete). Running the runner twice must be a no-op.
- Python backfills are recorded under synthetic versions
  (`BACKFILL_CLIENTES_VERSION = "0002_backfill_clientes"`,
  `BACKFILL_USUARIOS_VERSION = "0003_backfill_usuarios"`). ⚠️ **Those
  *strings* are already written in the production `schema_migrations` table:
  changing their value would make the runner believe the backfill never ran and
  run it again against real data.** The names of the Python constants
  and functions can be renamed (see Phase 1d, step 4); the string
  values cannot.
- The runner already runs on deploy: `.github/workflows/deploy.yml` executes
  `docker compose exec -T web python -m app.schema_migrate` right after
  `docker compose up -d --build`.

## Cash reconciliation (Cuadre de caja)

- **Report computed live** (not a "closing" that gets saved and locked) —
  route `/cuadre?desde=YYYY-MM-DD&hasta=YYYY-MM-DD` (`desde`/`hasta` = from/to), both parameters
  default to today (use `_today_iso()` with the Bogotá time zone, which already exists
  at `app.py:86` — never a raw `date.today()`, the container runs in UTC).
- **Access**: `@pos_allowed` (`admin` + `punto_venta`).
- **Export to PDF**: button/route to download the same totals as a simple
  PDF, so PTO.V can take it printed to the admin. **Confirmed: `reportlab`
  is used** (already proven in the project for generating the synthetic test
  PDFs, reliable, enough for a table of totals) — it is promoted from
  `requirements-dev.txt` (`reportlab==5.0.0`) to `requirements.txt` as a real
  production dependency in this phase.
- **Always** grouped by `order_date` (not by payment/
  transfer date — simpler).
- Grouped by `payment_method`, with an explicit **"SIN FORMA DE
  PAGO"** ("no payment method") bucket for `NULL` (all historical orders have
  `payment_method = NULL` — without this bucket, a `GROUP BY` hides or
  silently drops them). The bucket is Spanish text because it is a
  label the user sees.
- Excludes `is_voided` (never produced) and `is_plate_annulled` (lost
  blank, not charged).
- A plate with `unit_price IS NULL` generates an explicit warning with its
  `plate_type`/`order_number` — **never** `COALESCE(unit_price, 0)`.

Reference query (money in `plates`, payment method in
`production_orders`):

```sql
SELECT o.order_date,
       COALESCE(o.payment_method, 'SIN FORMA DE PAGO') AS payment_method,
       count(*)                                        AS n_plates,
       COALESCE(SUM(p.quantity), 0)                    AS total_qty,
       SUM(p.unit_price * p.quantity)                   AS total,
       count(*) FILTER (WHERE p.unit_price IS NULL)     AS missing_price_count
FROM plates p
JOIN production_orders o ON o.id = p.order_id
WHERE o.order_date BETWEEN %s AND %s
  AND p.is_voided = false
  AND p.is_plate_annulled = false
GROUP BY o.order_date, COALESCE(o.payment_method, 'SIN FORMA DE PAGO')
ORDER BY o.order_date, 2
```

`unit_price`/`total` come back from psycopg as `decimal.Decimal` — keep
`Decimal` end to end (never mix with `float`); add a Jinja filter
for Colombian formatting (`$1.954.000`), something the app does not have today.

Since `FORMA DE PAGO:` comes blank in both real sample PEDIDO files, and
it is the key field of the reconciliation, in `/ingest` it must be a **required**
`<select>` (no pre-selected blank option, with an explicit `OTROS` for
genuinely unknown cases) — if it arrives empty from a PDF, show the same kind of
warning that `_form_from_pedido` already uses for other ambiguous fields
("El PDF no indica la forma de pago — elegila antes de
guardar." — i.e. the PDF does not state the payment method, choose it before saving).

## Phases 1 / 1b / 1c — already executed (log)

Summary of what was done, for context. **There is nothing to execute here.**

- **Phase 1** created the POS schema and the migration runner. The customer backfill
  grouped `SELECT DISTINCT requested_by FROM production_orders` by
  `normalize.normalize_requested_by(value)` (the grouping key is the
  **canonical** form, never the raw string), discarded values without any
  alphanumeric character (`'//'`), inserted one `customers` row per group with
  NULL attributes, and pointed each order's `customer_id` to its group **without
  rewriting `requested_by`**. Real result in production: **190
  customers, 1 order with `customer_id IS NULL`** (the `'//'`, left for
  manual review, not as a junk customer). The algorithm lives in
  `app/schema_migrate.py` (function `backfill_clientes` today; becomes
  `backfill_customers` in Phase 1d, step 4).
- **Phase 1b** added `plate_types` (seeded with the 7 values of
  `normalize.PLATE_TYPES`) and the real FKs for `plates.plate_type`,
  `plate_type_prices.plate_type` and `blank_preparations.category`. It was
  verified against production that the data was already clean (0 orphan rows
  in both cases), so nothing needed cleaning first.
  The Python validation was kept — the FK is an additional safety net,
  not a replacement.
- **Phase 1c** renamed to English the 2 tables and the 24 columns that Phase 1
  had created in Spanish. Full map in
  `app/migrations/0003_rename_to_english.sql` and in `docs/DATABASE.md`. The 2
  existing FKs kept working after the rename (Postgres tracks them
  by OID, not by name).

**Invariant that remains in force for every future migration**: a new DB
built from `init.sql` and an old DB migrated with `app/migrations/*.sql`
must end up with the exact same schema (compare
`information_schema.columns` + `table_constraints` of both). And before
touching production: a backup `pg_dump` and run the migration first against
a copy restored locally (there is no staging environment).

`tests/conftest.py` is already adapted: `clean_db` truncates the new tables, and
`_db_ready` always runs the migration runner after the
`to_regclass('public.plates')` check, to converge a new or old DB to the same
schema.

## Phase 1d / 1e / 2 — already executed (log, 2026-09-20)

**There is nothing to execute here either.** Summary so that an agent
implementing Phase 3 onward does not touch this again:

- **Phase 1d** (PR #19, merged): full refactor by domains. `db.py`
  → package `app/core/db/{orders,plates,inventory}.py` with explicit
  re-exports in `__init__.py`; `process.py` → `app/core/ingestion.py`
  (`_is_alistado` → `is_alistado`, public); `app/web/app.py` (687 lines)
  → 5 blueprints in `app/web/blueprints/` (`ingest.py`, `records.py`,
  `review.py`, `inventory.py`, `auth_routes.py`) + `app.py` reduced to 83
  lines; new `app/web/helpers.py`; real connection pool
  (`psycopg_pool.ConnectionPool`, lazy, `DB_POOL_MIN_SIZE`/
  `DB_POOL_MAX_SIZE`); explicit `conn` in the 3 functions of
  `ingestion.py` (fixes the real non-atomic transactions bug);
  `row_factory=dict_row`; `psycopg.sql.SQL` for the order summary
  query (`OrderSummary`, typed dataclass); deduplication of
  `is_real_plate(...) and not is_voided` and of the counters block.
  Steps 10 (test reorg + rollback fixture) and 11 (docs rewrite) of this phase's
  original plan were done/updated separately
  (docs) or left as a non-blocking fast-follow. **Step 10, test
  reorg half: DONE (2026-09-21)** -- `tests/web/test_app.py` and
  `tests/core/test_db.py` were split following the same mirror as
  the source code (`tests/web/test_login.py`/`test_review.py`/
  `test_inventory.py`/`test_records.py`/`test_ingest.py`/`test_roles.py` and
  the new package `tests/core/db/` with `test_connection.py`/
  `test_orders.py`/`test_plates.py`/`test_inventory.py`/`test_customers.py`/
  `test_users.py`/`test_pricing.py`), zero behavior change (789
  tests, before and after). **Step 10 complete (2026-09-21)**: the other
  half -- converting `clean_db` from `TRUNCATE` to a transaction with
  per-test rollback -- is also done (`tests/conftest.py::clean_db`),
  via manual `SAVEPOINT`/`RELEASE SAVEPOINT`/`ROLLBACK TO SAVEPOINT` (not
  psycopg's `conn.transaction()` -- that forbids the explicit `conn.commit()`/
  `.rollback()` that the real code and ~100 tests already did).
  Investigated and explicitly ruled out: this does NOT enable running pytest
  in parallel against the same `plates_test` (two workers would compete for
  the same Postgres locks just as with `TRUNCATE`; real parallelism
  would need a separate schema/database per worker, out of scope) -- the
  real benefit is speed and that a test that fails midway does not leave
  corrupt data for the next one. Full detail in the docstring of
  `clean_db`.
- **Phase 1e** (PR #18 + PR #20, merged): Docker log rotation
  (`json-file`, `max-size: 10m`, `max-file: 3`) on `db`/`web`; Sentry
  (`sentry-sdk[flask]`) initialized in `app.py` only if `SENTRY_DSN` is
  set — **the variable is not yet set in production** (pending
  the user creating a Sentry account and passing the DSN as a secret).
  UptimeRobot (external, pings `/healthz`) — **pending configuration by
  the user**, no code required.
- **Phase 2** (PR #21, merged): `models.py` (~15 optional POS fields in
  `ExtractedRow`, default `None`, with a `mode="before"` validator for
  `delivery_date`/`payment_date`); `normalize.py` (`RECEIPT_TYPES`,
  `CUSTOMER_TYPES`, `DOCUMENT_TYPES`, `DOCUMENT_RELATIONS`,
  `PAYMENT_METHODS`, `REASONS` + `normalize_choice()`); new `db/customers.py`
  (`resolve_or_create_customer`, `get_customer`,
  `get_customer_by_name`); new `db/users.py` (`get_user_by_username`,
  `get_user_by_id`, `create_user`, `list_users`); new `db/pricing.py`
  (`current_prices`, `price_list`, `set_price`); `db/orders.py` — 
  `insert_order` now takes the full `ExtractedRow` + `customer_id` +
  `created_by`; `update_order` gains `customer_id` (with an `_UNSET` sentinel
  so old callers do not overwrite it by accident — important for
  Phase 3: `record_save` today does NOT pass `customer_id`, so it still does not
  touch it until Phase 3 updates it on purpose); `db/plates.py` —
  `unit_price` re-snapshot rule implemented in
  `_resnapshot_unit_price()` (used by `update_plate`/`update_plate_full`);
  `ingestion.py` — resolves/creates the customer per group, snapshots
  `current_prices` before each `insert_plate` (only if the row does not
  already carry a `unit_price`), accepts `created_by: int | None`, generalizes the
  "all rows in the group match" check to
  `_ORDER_LEVEL_FIELDS` (10 fields, excludes `reason`/`vehicle_brand`/
  `unit_price` which are per line), and fixes the real ALISTAMIENTO (preparation)
  bug of using `plate_type` directly as `category` (it now goes through
  `normalize.inventory_category()`, and raises if the type is `ORNAMENTAL` —
  it has no material). **`auth.py` is now DB-backed**: `authenticate()`
  queries `users` (via `db/users.py`) instead of `AUTH_USERS`/
  `AUTH_ROLES`; `login_post` stores `session["user_id"]`; new role
  `punto_venta` (`ROLE_PUNTO_VENTA = "punto_venta"`, no accent/with an underscore)
  + `POS_ROLES = {admin, punto_venta}` + `@pos_allowed` decorator —
  **no blueprint uses `@pos_allowed` yet, that is Phase 3 work**.
  `load_users()`/`load_roles()` are kept ONLY for the historical backfill
  in `schema_migrate.py`, the live login no longer uses them. Verified in
  production: the 3 real users (`admin`/`ptov`/`produccion`) are still in
  `users` with their hashes intact; login rejects invalid credentials with a
  clean 401 (not 500) against the real DB.

**For whoever implements Phase 3 onward**: the module/function names
that appear in the Phase 3-7 sections below ALREADY reflect this new
structure (`app/core/db/orders.py`, `app/core/ingestion.py`,
`app/web/blueprints/ingest.py`, etc.) — nothing needs to be translated, it can be
implemented exactly as written.

## Phase 3 / 5a — already executed (log, 2026-09-20)

- **Phase 3** (PR #23, merged): `/ingest` and `/ingest/pdf` are now
  `@pos_allowed` (before `@login_required`) — `punto_venta` can now use them
  as its daily entry point (`landing_url()` sends it there). New
  full-order fieldset (receipt/customer/required payment method/
  tracking #/order #/dates/dispatcher/notes) + per-line columns
  `reason`/`vehicle_brand`/`unit_price` (pre-filled by JS from
  `plate_type_prices`, editable). Customer autocomplete: the full list
  of `customers` is embedded as JSON in `ingest.html`
  (`_customers_context`, same pattern as `distinct_requested_by`/
  `distinct_cities` — this app has no AJAX endpoints) and the JS auto-fills
  the customer fieldset when the typed name matches. The 3
  independent copies of the "sticky" dict were unified in
  `_sticky_header_from_form(form)` (single source, also used by
  `_form_from_pedido`) — the full 20-field key set lives in the
  constant `_STICKY_HEADER_FIELDS` of `app/web/blueprints/ingest.py`, which
  any Phase 4 (PDF) work must reuse as is, not reinvent.
  `resolve_or_create_customer` gained attribute kwargs, **only written in
  the INSERT branch** (a genuinely new customer) — the
  `ON CONFLICT` branch still does not touch the attributes of an existing customer, per B5.
  Full parity in `record_detail.html`/`record_save`
  (`@pos_allowed` also on `/records/<id>/save`): it re-resolves
  `customer_id` from `requested_by` on every save, but never rewrites
  the customer's own attributes (B5, confirmed). `unit_price` of an
  edited plate is still not a parameter exposed in any form — the
  re-snapshot rule in `db/plates.py` is the only path that sets it.
  `format_cop` (new, `app/web/helpers.py`) is the Jinja filter `| cop` for
  Colombian currency.
- **Phase 5a** (PR #22, merged): new `app/web/blueprints/pricing.py`,
  `GET/POST /precios`, `@login_required` (admin-only, never `@pos_allowed`
  — `punto_venta` does not edit rates). "Precios" tab in `_nav.html`
  (gated by `full_access_roles`, not `pos_roles`).

**For whoever implements Phase 4 onward**: the `header` dictionary that
`/ingest` produces (both by hand and from a PDF) has EXACTLY the 20
keys of `_STICKY_HEADER_FIELDS` in `app/web/blueprints/ingest.py` — Phase 4
must map the new labels in the PDF footer to those keys
(`customer_type`/`customer_document_type`/`customer_document_number`/
`customer_document_relation`/`customer_phone`/`customer_email`/
`customer_transit_license` for the customer; the rest as this plan already
described). `_form_from_pedido` today leaves all those keys blank except
`order_date`/`order_number`/`requested_by` — it is the exact point that Phase 4
extends.

---

## Phase 1d — Domain-based architecture refactor (before Phase 2)

Explicit request from the user: "I want a more robust architecture with more
separation of responsibilities following SOLID principles... it is very
hard for me to follow the traceability... without overengineering." (original request in Spanish)

Today `app/web/app.py` has **687 lines with 19 routes** all together, and
`app/core/db.py` **556 lines with 24 functions** for `production_orders`/
`plates`/inventory mixed together. Phase 2 onward adds 4 more domains
(`customers`, `users`, `pricing`, cash reconciliation) — without this refactor, both
files would go past 1000 lines.

**Principle (explicit decision, not the obvious one)**: split by **business
domain within the two layers that ALREADY exist** (web ↔ core), **not**
by adding a new layer such as services/repositories.

### No ORM, no DI framework, no repository/Protocol layer

**No ORM**: this app is read-heavy, with report queries
(`array_agg ... FILTER`, `string_agg(CASE ...)`, `count(*) FILTER`) and a small
write surface — an ORM is worse, not better, precisely for that kind
of query.

**No interfaces/`Protocol`** to invert the dependency from `ingestion.py`
to `db.py`, for three reasons:

1. The only real readability gain would be not repeating `conn` in every
   call — the rest of the body stays the same. The value of "documenting
   the contract" is already better provided by the docstrings `db.py` has today
   (`recent_orders` explains why it sorts by `order_date`, `delete_plate`
   explains the flag-cleanup rule).
2. In Python, `Protocol` is structural (duck typing): there is no
   explicit declaration of "this implements that", so "go to definition" does not
   follow the link. That makes traceability **worse**, exactly the opposite of
   what this refactor is after.
3. **Decisive**: the reason for passing `conn` explicitly is to make visible in the
   signature who owns the transaction/commit. An abstract interface
   would have to hide `commit()` (lying about being "storage-agnostic")
   or add a "unit of work" abstraction to solve the
   problem that the first one created.

**No DI framework**, but note: "the tests hit a real Postgres"
does not prove there is no dependency-inversion problem — there is one,
and it is the transaction bug of improvement 3. What is true is that mocks
are not needed: real business rules deliberately live in the SQL of
`db.py` (e.g. `recalc_inventory` iterates fixed categories precisely because of a real bug
that was found once, see `docs/DATABASE.md`), so an in-memory
fake would have repeated the same wrong mental model and passed
green anyway.

**Signals that would warrant reopening this** (none of them is "the team grew"):
(a) a second real, nameable implementation appears (a second
concrete backend already planned, not "maybe change DB someday"); (b)
`ingestion.py` stops being the only writer of business logic and the
surface of `db.py` functions it consumes crosses ~15; (c) the suite
becomes so slow that the team starts skipping it — and there the solution is a
fixture with transactional rollback (which step 7 enables and step 10
implements), not fake doubles.

**Also ruled out**: Alembic (the in-house runner is already in production with
real versions recorded — migrating now would be pure churn; revisit only if
the future pain is specifically in migrations, not in queries);
the `create_app()` factory (see below); and Jinja's `StrictUndefined` — it was
verified that it **would break today**: `records_search_pdf` renders `records.html`
without `pdf_results`/`pdf_filename` in its three error routes, and `records()`
does not pass them either. Revisit only after splitting into blueprints, when
each one is small enough to audit.

### The 6 concrete improvements of this phase

1. **`row_factory=dict_row`** (from `psycopg.rows`, ships with the library,
   zero new dependencies) in `get_conn()` — deletes the **8** repetitions
   of `dict(zip(cols, r, strict=False))` + the **8** `assert cur.description
   is not None` in `db.py` (~50 lines). Do it **before** splitting `db.py`
   into a package, so that already-clean code is what gets divided.
2. **`psycopg.sql.SQL`** to compose the order summary query that today
   is copied 3 times almost identically (`recent_orders` db.py:251,
   `find_orders_by_number` db.py:294, `search_orders_by_number` db.py:369 —
   their own docstrings say "same row shape as recent_orders()") — a
   single `_ORDER_SUMMARY_SELECT` + a `WHERE` composed per case. In passing
   it removes the only SQL built with an f-string in the project (`db.py:149`,
   `_consumed_by_material` — it is not an injection today because the callers
   pass literal constants, but it is the pattern someone will reuse
   when `/cuadre` adds date-range filters).
3. **Pass `conn` explicitly upward in `ingestion.py` (formerly
   `process.py`)** — **real bug**: today `find_duplicate_plate_numbers`
   (process.py:60), `process_rows` (process.py:85) and `replace_order`
   (process.py:193) each open their own `db.get_conn()`, so a
   single `POST /ingest` runs in **4 separate transactions** (the 3
   above + the render's `_options()`) — the duplicate check and the
   insert it protects are NOT atomic with respect to each other. With 5 users it is a
   tolerable race today, but it is the wrong shape for a system that is going
   to handle money (`unit_price`, `/cuadre`), and the connection pool
   makes it cheaper to keep doing it wrong, not better.
   Fix: `process_rows(conn, rows)` / `replace_order(conn, ...)` /
   `find_duplicate_plate_numbers(conn, rows)` receive the connection; the
   caller opens `with db.get_conn() as conn:` and owns the commit —
   the same pattern each function in `db.py` already uses.
   ⚠️ **The internal `conn.commit()` calls in `process.py` (lines 141 and 235)
   must be DELETED, not "moved"**: psycopg3's `with db.get_conn() as conn:`
   already commits on its own when the block exits cleanly; if the
   internal `commit()` survives, `ingestion.py` commits midway through the route and the rest
   of the work ends up in a second transaction — the same bug is reproduced,
   more subtly. Add an explicit test (force an exception after the
   insert and confirm that EVERYTHING is rolled back, not just part of it).
   Done in the **same commit as the pool** (both change
   connection/transaction semantics, so they are best reviewed/reverted together).
4. **A typed dataclass (`class_row`) for the "order summary" row**
   shared by the 3 queries from point 2 and consumed by
   `_orders_table.html` (included twice from `records.html`) and
   `ingest.html` — the
   only shared shape that really warrants this (do not do it for every
   query, only where the shape truly repeats). With this mypy catches a
   `row.order_numbre` (typo) that would silently pass today.
5. **Deduplicate `ingestion.py`** — the real traceability problem behind
   this discussion: the condition
   `is_real_plate(r.plate_number) and not r.is_voided` is copied **8
   times** inside `process.py` today (lines 55, 58, 80, 98, 125, 188, 204,
   219), and the per-row counters block
   (`plates_inserted`/`possible_duplicates`/`voided`/`plate_annulled`/
   `batch_replacements`) is fully duplicated between `process_rows` and
   `replace_order`. Extract a helper `_dedup_candidates(rows)` and a helper
   `_count_row(summary, row, is_dup)`, used by both functions.
   **In addition there is a ninth, divergent copy in `app/web/app.py:622-625`**
   (inside `record_save`) that checks `db.existing_plate_numbers(conn,
   [...])` directly, without going through `ingestion.py` — it violates the
   project's own rule ("no SQL query or business rule should live outside
   `app/core/`", `docs/CORE.md:7`) and can drift out of sync with the real
   logic. Redirect that route to the `ingestion.py` helper.
6. **A module docstring in `db/__init__.py`** writing down the
   contract that is already followed implicitly: "every function here receives an
   already-open connection and never commits — the caller owns the
   transaction." One sentence, it documents most of the value a formal
   interface would give, without any new layer. Mirror the same in
   `docs/CORE.md`.

### Final structure

```
app/
  core/
    models.py                 # unchanged
    normalize.py               # unchanged
    pdf_extract.py              # unchanged
    ingestion.py                # renamed from process.py (process_rows, replace_order,
                                 # find_duplicate_plate_numbers, is_alistado -- the "_" is dropped on rename,
                                 # since app/migrate.py imports it across the module boundary)
    db/                         # package, replaces the flat db.py
      __init__.py               # get_conn() (with pool, see below) + explicit re-exports from each submodule
      orders.py                 # insert_order, update_order, delete_order, order_detail, recent_orders,
                                 # find_orders_by_number, search_orders_by_number, distinct_requested_by
      plates.py                 # insert_plate, update_plate, update_plate_full, delete_plate, resolve_plate,
                                 # review_rows, existing_plate_numbers, find_orders_for_plate,
                                 # search_plates_by_number, distinct_cities (does NOT go in inventory.py -- it queries
                                 # plates, not inventory_categories/blank_preparations; included here for
                                 # symmetry with distinct_requested_by in orders.py)
      inventory.py               # insert_blank_preparation, recalc_inventory, inventory_view,
                                 # ornamental_count, _consumed_by_material (private) -- the last two
                                 # query plates but are implementation details of this page/function,
                                 # they stay here on purpose (a deliberate exception to "group by table")
      # customers.py/users.py/pricing.py are NOT created yet -- they belong to Phase 2, creating empty stubs
      # now would be the overengineering we were asked to avoid
  web/
    app.py                      # keeps: creation of `app`, context processor, blueprint registration,
                                 # and the 2 orphan routes (/, /healthz) that do not warrant their own blueprint
    auth.py                     # not restructured (148 lines, cohesive) -- gains `landing_url()` (public,
                                 # formerly private `_landing_url` in app.py:280, moves here because it already operates on
                                 # session["role"]/FULL_ACCESS_ROLES which already belong to this module)
    helpers.py                  # NEW: today_iso(), _options(), _padded(), _validate_order_header(),
                                 # FORM_PLATE_TYPES, INVENTORY_CATS -- what 2+ blueprints use at once
    blueprints/
      ingest.py                 # /ingest, /ingest/pdf + _rows_from_form, _sticky_lines_from_form,
                                 # _form_from_pedido (NOTE: tests/web/test_ingest_pdf.py imports it directly,
                                 # that import must be updated in the same commit)
      records.py                # /records, /records/<id>, /records/<id>/save, /records/delete,
                                 # /records/search-pdf + _edit_rows_from_form, _order_voided, _record_detail_error
      inventory.py               # /inventory, /inventory/alistado
      review.py                  # /review/*
      auth_routes.py             # /login (GET and POST), /logout
      # pricing.py/cash_summary.py are NOT created yet -- they belong to Phase 5a/5b
    templates/                  # unchanged
```

**No Flask factory** (`create_app()`) — a single config, a single deploy,
no need for multi-instance. Switching to a factory would force touching
`web.Dockerfile` (the gunicorn command) and `tests/web/conftest.py`
without solving any real problem. `app = Flask(__name__)` stays at module
level. Context processors (`_inject_full_access_roles`, app.py:68)
keep working the same with blueprints — they are app-level, not
blueprint-level.

**Import decision**: `db/__init__.py` explicitly re-exports every public
function of each submodule (not `import *`), so
`db.insert_order(conn, ...)` **keeps working exactly the same** in the
9 files that import `db` today (including the ~189 `db.<func>(` calls
in `tests/core/test_db.py`). This contradicts the intuition of "have
`__init__.py` contain only `get_conn`", but it avoids a giant mechanical rename
with no real benefit — the traceability problem was "where each function
is defined", not "what it is called". Revisit this decision if the `db` package
grows a lot again after Phase 2.

### Connection pool (confirmed: "it goes in, no matter what")

Today `db.get_conn()` makes a new `psycopg.connect(...)` on every call — no
pool. A `psycopg_pool.ConnectionPool` is added (an official psycopg3 extra, not
included with `psycopg[binary]` — change `requirements.txt` to
`psycopg[binary,pool]==3.2.3`).

- **Lazy construction, not at import**: the pool is built on the first real
  call to `get_conn()`, never when importing `app.core.db`. This is
  necessary, not optional: `tests/conftest.py` always imports `app.core.db`,
  even when `POSTGRES_PASSWORD` is not set (so the pure
  `app/core` tests can run without depending on the DB) — building the pool at
  import would break that.
- **`get_conn()` becomes a generator with `@contextmanager`** that
  wraps `pool.connection()`. The **21** call sites in `app/` that already use
  `with db.get_conn() as conn:` (17 in `app.py`, 3 in `process.py`, 1 in
  `migrate.py`) and the 24 in `tests/web/test_app.py` **do not change a single
  letter** — `with` works the same on the generator.
- **The 6 call sites that do a manual `conn = db.get_conn(); ...; conn.close()`
  DO have to be rewritten** to `with db.get_conn() as conn:` —
  calling `.close()` on the generator would break with `AttributeError`.
  Exact list: `tests/conftest.py:170` (`_db_ready`), `:206` (`clean_db`),
  `:223` (`db_conn`); `app/schema_migrate.py:234` (`main()`);
  **`tests/test_schema_migrate.py:308` and `:319`**.
- **Gunicorn**: `web.Dockerfile` runs `gunicorn --workers 2` **without**
  `--preload` — each worker imports the app after forking, so
  each worker builds its own pool on its first request. No `post_fork`
  hook is needed. Leave an explicit comment in the code: if `--preload` is ever
  added, this reasoning breaks and the pool construction has to move to a
  gunicorn `post_fork` hook.
- **Size**: `min_size=1, max_size=5` per worker (env-overridable as
  `DB_POOL_MIN_SIZE`/`DB_POOL_MAX_SIZE`, same pattern as `DB_HOST`/
  `DB_PORT`) — 2 workers × 5 = 10 connections max. from the app, against
  Postgres's default `max_connections=100`, with plenty of headroom for
  `migrate.py`/`schema_migrate.py`/manual `psql`.
- **Shutdown**: no `pool.close()`/`atexit` hook is needed — gunicorn already
  waits for in-flight requests to finish before killing a worker
  (SIGTERM + `--timeout 120`), and this app has no other graceful-shutdown
  machinery.
- **Tests**: `tests/core/test_db.py::TestGetConn` (4 tests, lines 25-70)
  today mocks `psycopg.connect` assuming one call per invocation — that stops
  being true with a pool (built once, not per call). Rewrite
  those 4 tests against a new pure function `_conninfo_kwargs()` (separates
  reading env vars from building the pool, with no need to mock
  anything). The rest of the suite benefits for free: reusing connections across
  the hundreds of tests that use `db_conn`/`clean_db` is faster than opening
  a new socket per test, and since `pool.connection()` rolls back
  automatically on leaving the `with` if there was an exception, isolation does not
  change.
- **Separate commit from the one that splits `db.py` into a package** (even though they touch the
  same file, they are different risks: one moves code without changing
  behavior, the other does change it at runtime).

### Commit sequence (incremental, not a single atomic PR)

1. **`row_factory=dict_row`** in `get_conn()` — deletes the 8 `dict(zip(...))`
   and the 8 `assert cur.description` from `db.py`. Tests green, commit.
   (Before touching the file structure, so that already-clean code is what gets divided.)
2. **`psycopg.sql.SQL`** to deduplicate the order summary query (3
   almost identical copies) and kill the f-string in `_consumed_by_material`
   (`db.py:149`). Tests green, commit.
3. **Typed dataclass (`class_row`)** for the order summary row used
   by those 3 queries + `_orders_table.html`/`ingest.html`. Tests green,
   commit.
4. **Naming renames** (`CLAUDE.md`), with no behavior change:
   - `app/core/process.py` → `app/core/ingestion.py`; `_is_alistado` →
     `is_alistado` (public). Update imports in `app/web/app.py:42`,
     `app/migrate.py:26` and `tests/core/test_process.py:19` (also rename
     that file to `tests/core/test_ingestion.py`).
   - `app/schema_migrate.py`: `backfill_clientes` → `backfill_customers`,
     `backfill_usuarios` → `backfill_users`, `BACKFILL_CLIENTES_VERSION` →
     `BACKFILL_CUSTOMERS_VERSION`, `BACKFILL_USUARIOS_VERSION` →
     `BACKFILL_USERS_VERSION`. ⚠️ **The string VALUES
     (`"0002_backfill_clientes"` / `"0003_backfill_usuarios"`) are NOT touched**
     — they are already written in the production `schema_migrations` and changing them
     would re-trigger the backfill against real data (it is explained in the
     docstring of `app/schema_migrate.py`; update that docstring so it
     keeps describing reality). Also update the test names
     in `tests/test_schema_migrate.py` (`..._cliente`/`..._clientes`/
     `..._usuario`/`..._usuarios`).
   Tests green, commit.
5. **Deduplicate `ingestion.py`** (see improvement 5): extract
   `_dedup_candidates`/`_count_row`, use them in `process_rows` and
   `replace_order`, and redirect the divergent check at
   `app/web/app.py:622-625` so that it goes through here. Tests green, commit.
6. **Split `db.py` → `db/` package** (with the location of
   `distinct_cities` from above). Zero call sites outside `db/` change (thanks to
   the explicit re-export). Add the transaction contract docstring
   (improvement 6) to `db/__init__.py` and to `docs/CORE.md`. Tests green, commit.
7. **Combined commit — connection pool + explicit `conn` in
   `ingestion.py`** (deliberately together: both change
   connection/transaction semantics):
   - Add `psycopg[binary,pool]==3.2.3`, lazy pool in
     `db/__init__.py`, pure function `_conninfo_kwargs()`.
   - `process_rows(conn, rows)` / `replace_order(conn, ...)` /
     `find_duplicate_plate_numbers(conn, rows)` receive the connection,
     **deleting** their internal `conn.commit()` calls (process.py:141 and :235).
   - Update **all** callers, not just those in `app.py`:
     · `app/web/app.py:364` (`replace_order`), `:383`
       (`find_duplicate_plate_numbers`), `:396` (`process_rows`) — the
       `ingest_post` route now opens **a single** `with db.get_conn() as conn:`
       that wraps all three and does the commit.
     · **`app/migrate.py:234` and `:250` (`process_rows`)** — this script
       runs against production, do not leave it out of the refactor. The two
       calls are in different branches of the same `if args.since: ... else:`:
       the one at `:234` (the `--since` branch) needs a **new** `with db.get_conn() as conn:`;
       the one at `:250` (the reconciled branch) falls right after the
       `with` that already exists at `:243` for inserting
       `RECONCILED_ALISTAMIENTO` — extend that block so it also
       wraps `process_rows(plate_rows)` and delete its `conn.commit()`
       at `:249`, so that the reconciled preparation and the plates
       end up in ONE single transaction.
     · `tests/core/test_ingestion.py` — ~35 calls to
       `process_rows`/`replace_order`/`find_duplicate_plate_numbers`; all
       change to receive the fixture's `db_conn`.
   - Rewrite `tests/core/test_db.py::TestGetConn` (4 tests) against
     `_conninfo_kwargs()`.
   - Rewrite the 6 manual call sites listed above to `with`.
   - Add the explicit atomicity test (exception midway through the route →
     everything is rolled back).
   - **Fix the docstrings/comments that claim the opposite** (today
     they say "each `get_conn()` opens a new connection, no injection
     point"): `app/core/db.py` (module docstring, when adding the pool),
     `tests/conftest.py:190` (docstring of `clean_db`),
     `tests/core/test_ingestion.py:4-8` (formerly `test_process.py`, module
     docstring), `tests/core/test_db.py:3-8` (module docstring),
     `tests/web/test_app.py:219` (section comment) and
     `docs/LOCAL_DEVELOPMENT.md:43-46`.
   Tests green, commit.
8. **Extract `app/web/helpers.py`** and move `_landing_url` (app.py:280) to
   `auth.py` as `landing_url()` — the routes stay in `app.py` in this
   step, only the helpers move. Tests green, commit.
9. **Create `app/web/blueprints/`, one at a time, one commit per blueprint**:
   `review.py` (the simplest) → `inventory.py` → `auth_routes.py` (`/` and
   `/healthz` stay directly in `app.py`) → `records.py` → `ingest.py`
   (update the import in `tests/web/test_ingest_pdf.py` and the
   `monkeypatch.setattr("app.web.app.process_rows", ...)` calls in
   `tests/web/test_ingest_pdf.py:112` and
   `tests/web/test_records_search_pdf.py:65`, which point to the old module).
   Tests green after each one.
10. **Recommended fast-follow, non-blocking -- test reorg DONE
    (2026-09-21)**: `tests/web/test_app.py` (it reached 2096 lines) and
    `tests/core/test_db.py` (it reached 1529 lines) were already larger than
    the code they test, and without splitting them the same traceability
    problem was being recreated on the test side. `test_app.py` (already
    organized by `TestXRoute` class) was split into `tests/web/test_login.py`/
    `test_review.py`/`test_inventory.py`/`test_records.py`/`test_ingest.py`/
    `test_roles.py` (plus `tests/web/_helpers.py` for the shared
    `make_row`/`_insert_order` helpers), and `test_db.py` into the new package
    `tests/core/db/` (`test_connection.py`/`test_orders.py`/
    `test_plates.py`/`test_inventory.py`/`test_customers.py`/
    `test_users.py`/`test_pricing.py`, plus `tests/core/db/_helpers.py`) —
    same mirror as the source code, zero behavior change (789
    tests, before and after). `test_roles.py` was kept separate because
    `TestProduccionRole`/`TestPuntoVentaRole` cut across several blueprints at
    once, they do not fit in a single file per blueprint.

    **Also DONE (2026-09-21)**: converting `clean_db` from `TRUNCATE` to
    a transaction with per-test rollback (enabled by step 7: now that
    `ingestion.py` receives `conn` instead of opening its own, a test can
    wrap everything in a single transaction and roll it back at the end) — real,
    not trivial: `conn.commit()`/`.rollback()` are called explicitly in
    real code (`records.py`/`review.py`/`pricing.py`/`inventory.py`) and at
    ~100 test sites, so `psycopg`'s `conn.transaction()` (which
    forbids calling them inside its own context manager) does not work — the
    solution was manual `SAVEPOINT`/`RELEASE SAVEPOINT`/`ROLLBACK TO SAVEPOINT`,
    reassigning `conn.commit`/`conn.rollback` to operate on that
    savepoint instead of the real database, taking special care to replicate the
    "commit on an aborted transaction = implicit rollback, no error"
    behavior that Postgres gives for free and that several tests depended on without knowing it
    (`tests/test_schema_migrate.py`, `TestCreateUser::
    test_duplicate_username_raises`). Verified with the full suite
    (789 tests) run 3 times in a row with no change in results. It does **not**
    enable running `pytest` in parallel against the same physical
    `plates_test` — that part of this step's original promise was investigated and
    explicitly ruled out (see the docstring of `clean_db` and
    `docs/LOCAL_DEVELOPMENT.md` for why): the real benefit achieved
    is speed, not parallelism.
11. **Rewrite the documentation** at the end, once the code has settled:
    `docs/ARCHITECTURE.md` (the diagram explicitly names `app/core/process.py` and
    `app/core/db.py`), `docs/CORE.md` (sections `## process.py`
    and `## db.py`), `docs/WEB.md` and `docs/LOCAL_DEVELOPMENT.md`.
## Phase 1e — Monitoring and observability (mostly in parallel with 1d)

Today there is nothing: two stray `app.logger.exception()` calls, a `/healthz` that
nobody queries, zero log rotation (verified: `docker-compose.yml` has no
`logging:` block, and there is no `sentry` in the repo or in
`requirements.txt`). With real money entering the system this becomes
necessary, not a luxury. Deliberately minimal scope — no
Prometheus/Grafana/ELK/APM, that is overengineering for a VPS with Docker
Compose and a handful of users.

⚠️ **File conflict with 1d — order these two items**: 1e touches
`requirements.txt`, `docker-compose.yml` and `app/web/app.py` (the Sentry
init), and 1d rewrites `app.py` entirely and also touches
`requirements.txt` (step 7). The two items that do NOT touch `app.py` (log
rotation and UptimeRobot) can be done in parallel with 1d without risk. The
Sentry init is best done **after step 9 of 1d** (when
`app.py` has already been reduced to app creation + blueprint registration), or
else accept resolving the conflict by hand. Do not start the Sentry init in a
parallel worktree while 1d is halfway through.

- **Sentry** (`sentry-sdk[flask]`, the free plan is enough): automatically captures
  any unhandled exception, including the already-existing
  `app.logger.exception()` calls, with an email alert and the full stack
  trace. It is initialized once in `app.py`, right after
  `app = Flask(__name__)`, with the DSN via an environment variable
  (`SENTRY_DSN`; if it is not set, do not initialize — that way local dev and
  tests send nothing).
- **UptimeRobot** (free, no code changes): ping
  `https://placas.ingse.co/healthz` every 5 minutes, alert if it goes down.
  External configuration, it does not touch the repo other than mentioning it in
  `docs/ARCHITECTURE.md`.
- **Docker log rotation**: add to `docker-compose.yml` (service
  `web`, and `db` if applicable):
  ```yaml
  logging:
    driver: json-file
    options:
      max-size: "10m"
      max-file: "3"
  ```
  Without this, the containers' logs grow without limit on the VPS disk,
  indefinitely.

## Phase 2 — `app/core` (models, normalize, ingestion, db) + rates

**Requires Phase 1d to be merged already** — it is built against the new
package structure (`app/core/db/orders.py`,
`app/core/ingestion.py`), not against the flat `db.py`/`process.py`. The new
modules (`db/customers.py`, `db/users.py`, `db/pricing.py`) follow the same
package pattern that 1d established.

- `models.py`: ~15 new optional fields in `ExtractedRow`, all with
  default `None` (so `app/migrate.py` still does not need touching). Add them to the
  existing `_blank_to_none` validator (otherwise an empty string from the form
  stays as `""` instead of `NULL`, and breaks any `IS NULL` in
  `/cuadre`). `delivery_date`/`payment_date` need a `mode="before"`
  validator that maps `""` → `None` (an empty `<input type="date">`
  posts `""`, which pydantic rejects as is). **`delivery_date` must NOT
  go through the "date not in the future" guard of `_validate_order_header`** —
  a future delivery date is valid and expected.
- `normalize.py`: fixed vocabularies (constant names in English,
  *values* in Spanish) + a generic helper
  `normalize_choice(value, allowed) -> str | None`: empty → `None`; if it
  matches (comparing case/accent-folded) it returns the canonical entry from
  `allowed`; if it does not match, `None` — it never invents a value, same spirit
  as `is_real_plate`/`pdf_extract` ("faithful, not clever"). For the PDF, an unrecognized
  value is shown as a warning in `notices` so the person
  picks it from the dropdown.

  ```python
  RECEIPT_TYPES       = ("FACTURA", "RECIBO", "OTROS")
  CUSTOMER_TYPES      = ("PERSONA NATURAL", "TRÁNSITO", "EMPRESA", "OTROS")
  DOCUMENT_TYPES      = ("NIT", "CÉDULA", "OTROS")
  DOCUMENT_RELATIONS  = ("DUEÑO", "POSEEDOR", "OTROS")   # the draft had "POSEDOR", which is a typo
  PAYMENT_METHODS     = ("EFECTIVO", "TRANSFERENCIA", "CRÉDITO", "OTROS")
  REASONS             = ("DETERIORO", "PÉRDIDA", "OTROS")
  ```
- `core/ingestion.py`: `process_rows`/`replace_order` resolve/create the
  customer from the same `head`/`group[0]` row they already use for
  `order_date`/`requested_by`; they fetch the current price (`current_prices`)
  before each `insert_plate`; they accept `created_by: int | None` →
  `production_orders.created_by`. **Generalize** the check that today only
  exists for `requested_by` ("do all rows in the group match?") to
  **all** the order-level fields (`receipt_type`/`receipt_number`,
  `payment_method`, `tracking_number`, `client_order_number`,
  `delivery_date`, `payment_date`, `dispatched_by`, `received_by`, `notes`,
  resolved customer) — iterate a tuple `_ORDER_LEVEL_FIELDS` and emit one
  `review_notes` for each field that does not match within the same group.
  `reason`/`vehicle_brand`/`unit_price` are left out (they are genuinely per
  line).
  **`replace_order` takes the current rate again AT THE TIME OF THE
  REPLACEMENT**, not the one in force on the order's original date —
  document it in the docstring of `replace_order` and in the text of the
  `confirm()` of "Reemplazar" (Replace) in `ingest.html` ("...y los precios se toman
  de la tarifa vigente hoy, no la de la fecha original" — i.e. prices are taken
  from today's current rate, not the original date's).
- `core/db/customers.py` (new): CRUD of `customers`
  (`resolve_or_create_customer(conn, name) -> int`, reused by
  `ingestion.py` AND by `record_save` — see Phase 3).
- `core/db/users.py` (new): lookup/CRUD of `users`, used by `auth.py`.
- `core/db/pricing.py` (new): `current_prices`/`price_list`/`set_price`.
- `core/db/orders.py`: grow `insert_order` (pass the full
  `head`/`ExtractedRow` row + `customer_id` instead of ~13 loose kwargs,
  just as `insert_plate(conn, *, order_id, row,
  is_possible_duplicate)` already does), `order_detail` (+ `JOIN customers` to fetch
  its attributes), `update_order` (+ `customer_id`). The listing queries
  (`recent_orders`, `find_orders_by_number`, `search_orders_by_number`)
  **do not change shape** — they keep using `requested_by` as is, so
  `_orders_table.html` does not need to be touched.
- `core/db/plates.py`: `insert_plate` (+ `unit_price`), `update_plate_full`
  and `update_plate` (+ `unit_price` re-snapshot rule;
  `search_plates_by_number` does not change shape).
- `auth.py`: moves from reading `AUTH_USERS`/`AUTH_ROLES` from environment variables
  to querying `users` (via `core/db/users.py`, never inline SQL in
  `auth.py`). Login stores `session["user_id"]` in addition to `user`/`role`.
  Respect the role literals without accents (see the note in the `users` table).
- **Boundary with Phase 5a**: everything about prices in `core/db/pricing.py` is
  done HERE — Phase 5a is *only* the `/precios` route + template, so that they
  do not step on each other if they run in parallel.
- **`recalc_inventory` is not touched** — see the note on price/inventory
  separation above.
- **Bug found along the way, by the Phase 1b agent**: the
  ALISTAMIENTO branch of `ingestion.py` uses `row.plate_type` directly as
  `category` instead of mapping it with `normalize.inventory_category()`. It is
  dead code today (the real flow of `/inventory/alistado` does not go through
  there), but fix it now since this part is being rewritten anyway.

## Phase 3 — `/ingest` UI

- Order fieldset: receipt (`receipt_type`/`receipt_number`), customer
  (with autocomplete that reuses data if it already exists), payment method
  (`payment_method`, required `<select>`), tracking#/order#
  (`tracking_number`/`client_order_number`), dates
  (`delivery_date`/`payment_date`), dispatcher/recipient
  (`dispatched_by`/`received_by`), notes (`notes`).
- Plate line table: new columns `reason`/`vehicle_brand`/
  **`unit_price`** per row. `unit_price` is pre-filled via JS with the rate
  from `plate_type_prices` when the plate type of that line is chosen (same
  `HINTS`/per-type placeholders pattern that `ingest.html` already uses), but it is
  a normal editable input. `transit_license` goes in the customer block of the
  header (only visible if `type` = PERSONA NATURAL).
- `@pos_allowed` route (`admin` + `punto_venta`), replaces the current
  `@login_required` on `/ingest`, `/ingest/pdf` and
  `/records/<id>/save`.
- **The form today builds the "sticky" dictionary (to repopulate the form if a
  duplicate warning appears) in 3 different, independent places**
  (twice inline in `ingest_post`, `app.py:375-379` and `:389-393`; once
  in `_form_from_pedido`, `app.py:261`) — with ~15 new fields, if any of the three
  falls out of date the person has to retype the whole
  customer/payment block every time a warning bounces. Factor it into a single
  helper `_sticky_header_from_form(form) -> dict` used in both bounces, and
  make `_form_from_pedido` return exactly the same set of keys.
  `_sticky_lines_from_form` also needs to add `reason`/`vehicle_brand`
  wrapped in `_padded(...)` as is already done with `city` (otherwise a shorter
  optional list truncates the `zip` and plate lines silently
  disappear).
- Parity in `record_detail.html`/`record_save`: besides the new
  fields, `record_save` must call `db.resolve_or_create_customer` if the
  customer's name is edited (today it does not re-resolve `customer_id` at all)
  — see B5 for the exact scope of what can be edited.

## Phase 4 — PDF automation (depends on the shape of the Phase 3 `header` dict)

**The PDF has fields both ABOVE and BELOW the plate table** —
confirmed by running `pdfplumber` on the two real PEDIDO files. Today
`_find_header_value` only searches in `header_rows = rows[:header_index]` (everything
ABOVE the table). Six of the new labels (`RESPONSABLE
DESPACHO...`, `TELÉFONO:`, `NOMBRE QUIEN RECIBE...`, `FORMA DE PAGO:`,
`OBSERVACIONES:`, and `CIUDAD Y DIRECCIÓN DE ENTREGA:` which is out of
scope) are BELOW `TOTAL PEDIDO:`, in the page footer — they are
physically invisible to the code as it stands today.

- Add a second block `footer_rows = rows[footer_start:]` (from the
  row that starts with `RESPONSABLE DESPACHO`) in addition to the existing
  `header_rows`, and search there for the footer labels. **Do not touch**
  `_find_header_value` itself (the `_LABEL_PEDIDO_HASH_BARE` guard lives
  inside it and must keep applying for free to any new lookup).
- **The labels must be the EXACT printed text** (matching is by
  equality, not by substring) — the real name is `RESPONSABLE DESPACHO
  (INGSE SAS):` and `NOMBRE QUIEN RECIBE EL PEDIDO:`, not the short versions.
- Maps (header): `CLIENTE:` (resolves/creates the customer just as in manual —
  `NIT / CÉDULA:` → `document_number`, may come blank if the
  value cell falls on a different row than the label because of a merged-cell
  artifact; keep the "same row only" scan as is,
  NEVER add a "look at the next row" heuristic — that risks
  pulling in the value of another field), `GUÍA #:` → `tracking_number`, `PEDIDO #:`
  → `client_order_number` (appears only in the new declarative table, never
  in `_LABELS_ORDEN`), `FECHA ENTREGA:` → `delivery_date`,
  `FECHA TRANSF./PAGO:` → `payment_date`.
  Maps (footer): `RESPONSABLE DESPACHO (INGSE SAS):` → `dispatched_by`,
  `TELÉFONO:` → `customers.phone`, `NOMBRE QUIEN RECIBE EL PEDIDO:` →
  `received_by`, `FORMA DE PAGO:` → `payment_method`, `OBSERVACIONES:` →
  `notes`.
- **The PDF's money fields are ignored on purpose**, just like
  `TRÁNSITO:`: the header's `VALOR:`, the lines' `VALOR PLACA`/`VALOR TOTAL`,
  and the footer's `TOTAL PEDIDO:` — `unit_price` always comes from the rate
  (`plate_type_prices`) as a pre-filled, editable suggestion, and is never read
  from the document. (`TOTAL PEDIDO:` still fulfills its current role of marking
  the end of the plate table, and now also the start of the footer block.)
- **Always manual, with no printed field anywhere in the PDF**:
  `receipt_type`, `receipt_number`, `customers.type`,
  `customers.document_type`, `customers.document_relation`,
  `customers.email`, `customers.transit_license`, and per line
  `reason`/`vehicle_brand`.
- New synthetic fixtures in `tests/pdf_fixtures.py` with footer rows
  carrying real values (today the fixture already adds those label rows
  but without values) + a case with the `NIT / CÉDULA` offset to confirm
  that it returns `""` and not an incorrect value.

## Phase 5a — Rates (`/precios`)

ONLY `blueprints/pricing.py` + template (the logic of `core/db/pricing.py`
was already done in Phase 2). Does not touch `ingest.html`/`ingestion.py`. Route
is `@login_required` (admin-only).

## Phase 5b — Cash reconciliation (`/cuadre`)

Depends on Phase 3 already generating real `payment_method` and on
Phase 5a for the rates. The aggregation logic (the query in the
"Cash reconciliation" section) goes in `core/cash_summary.py` (new — there is real logic
beyond CRUD, it warrants its own orchestration module instead of living
directly in a `db/*.py`); the route goes in `blueprints/cash_summary.py`. Route
is `@pos_allowed`. Includes the HTML view and the route/button to export the same
report as a simple PDF.

## Phase 6 — UI cleanup (already done for the existing templates)

The pass over the existing templates **was already done** (PR #13): grep
confirms that no emoji/decorative symbol is left today in
`app/web/templates/`. What remains is **applying the same criterion to the
new templates of this feature**:

- No emojis or decorative symbols in the HTML — plain text or CSS (e.g. a
  remove-line button says "Eliminar" (Delete), not "✕").
- On text: **keep only what explains how to use something** (e.g.
  "Disponible = Alistado − Consumido..." (Available = Prepared − Consumed...) in `inventory.html`, or "Subí el PDF
  del pedido para revisar..." (Upload the order PDF to review...) in `records.html`) and **cut what is purely
  decorative/redundant**. Review every `<p class="muted">`/similar in the
  new templates with that criterion.

## Phase 7 — Documentation (after 5b)

- `docs/DATABASE.md` — review in case anything changed compared with what is already
  documented in Phases 1/1b/1c.
- `docs/CORE.md` — new functions of `db`/`normalize`/`ingestion`, the
  `unit_price` re-snapshot rule, the re-pricing behavior of
  `replace_order`.
- `docs/WEB.md` — `/precios` and `/cuadre` in the route table, with their
  decorators; the `punto_venta` role.
- `docs/ARCHITECTURE.md` — the CI/CD table today describes `deploy.yml` without
  mentioning the `python -m app.schema_migrate` step that already runs; add it
  together with the migration runner as a piece of infrastructure.
- `README.md` — how to run the migrations locally.

## Execution order

```
Phase 1   (schema + migration + deploy hook)     ── DONE (PR #14)
Phase 1b  (real FKs)                             ── DONE (PR #16)
Phase 1c  (rename to English)                    ── DONE (PR #17)
Phase 6   (UI cleanup of existing templates)     ── DONE (PR #13)
        │
        ▼
Phase 1d (refactor: db/ package, blueprints, pool)  ── blocks Phase 2
        │
        ▼
Phase 2 (core: models, normalize, db —incl. rates—, ingestion)
        │
        ├──► Phase 5a (only the /precios route/template)  ── parallel worktree
        │
        ▼
Phase 3 (/ingest UI + record_detail/record_save parity)
        │
        ├──► Phase 4 (PDF: header + page footer)
        └──► Phase 5b (/cuadre)
                │
                ▼
        Phase 7 (docs)

Phase 1e (monitoring) ── logs+UptimeRobot in parallel with 1d; the Sentry init
                       after step 9 of 1d (conflict in app.py)
```

**Important about "parallel worktree"**: parallelizing the WRITING of
code in different worktrees is fine, but **running the test suite from
two worktrees at the same time is NOT safe** — `docker-compose.yml` uses fixed
names/ports (`plates-db-1`:5433, `plates-web`:8000), the test DB
is a single shared `plates_test`, and `clean_db` does a `TRUNCATE` without
isolating by transaction (`docs/LOCAL_DEVELOPMENT.md`). Two worktrees
running `pytest` at the same time will clobber each other's tables. Solution:
keep using the file lock (`/tmp/plates_docker_lock`) — only one
worktree runs DB tests at a time. (Step 10 of Phase 1d lifts this
restriction by switching `clean_db` to transactional rollback.)

Everything that remains to be done is new additive code or refactoring with no schema
change; the only phases that touched real production rows were already
executed. If a future phase needs a migration again, the same
rule applies: backup `pg_dump` and test against a dump restored
locally before touching the real database.

## Instruction for any agent executing this

**Do not assume any ambiguous business/schema decision — ask
the user directly and wait for an answer, just as was done in this very
conversation field by field.** This applies in particular to any new
field that shows up during implementation and is not explicitly resolved in this
document, and to questions **B4/B5**, which were left with a reasonable default
that was not explicitly confirmed — if, when implementing Phase 3, more
precision is needed on either of them, ask before assuming the
default.

## Verification

**Phase 1d (refactor)**
- Full suite green after EVERY commit in the sequence (not just at
  the end) — if something breaks, it must be obvious which step broke it.
  `ruff`/`mypy` clean.
- Manual verification in Docker: each route still responds the same as
  before the refactor (test at least one from each blueprint).
- `docker compose exec web python -m app.schema_migrate` still works
  (it uses `db.get_conn()`, now a package) and is still a no-op on the second
  run — confirm that `schema_migrations` did not gain new rows after the
  constant renames of step 4.
- `python -m app.migrate --help` (or a dry run) still works
  after the signature change of `process_rows`.

**Phase 1d (pool)**
- 2 concurrent requests to different routes do not block each other nor
  exhaust the pool (`max_size=5`).
- Killing and recreating the `web` container leaves no orphan connections in
  Postgres (`SELECT count(*) FROM pg_stat_activity`).
- The 6 call sites rewritten to `with db.get_conn()` (3 fixtures in
  `tests/conftest.py`, `schema_migrate.py:main()`, 2 in
  `tests/test_schema_migrate.py`) still clean up connections between tests.
- Atomicity test: an exception midway through `POST /ingest` rolls back
  EVERYTHING (neither the order nor the plates nor the `recalc_inventory` are left written).

**Phase 1e**
- Sentry captures a forced test error and it shows up in the dashboard.
- UptimeRobot configured and confirming "up" against `/healthz`.
- `docker compose logs web` respects the configured `max-size`/`max-file`
  (verify with `docker inspect` the `LogPath` and its size after
  generating traffic).

**Phase 2 onward**
- `replace_order` after a rate change → the new plates end up
  with the rate in force at the time of the replacement, not the original one
  (documented behavior, not a bug).
- Editing `plate_type` on a saved plate re-takes `unit_price`; editing
  only `quantity` does not touch it.
- `record_save` renaming the customer moves `customer_id` and keeps
  `requested_by` in sync with `customers.name`.
- A duplicate-warning bounce on `/ingest` preserves all the new POS fields
  and the line fields (`reason`/`vehicle_brand`).
- `/cuadre`: the "SIN FORMA DE PAGO" bucket appears; `is_voided`/`is_plate_annulled`
  are excluded; a plate with `unit_price NULL` generates a
  warning and does NOT count as 0.
- `pdf_extract`: a fixture with footer rows parses the footer
  fields; the bare `PEDIDO #` guard keeps working; `PEDIDO #: 29` lands
  in `client_order_number` and never in `order_number`; `NIT / CÉDULA` with a
  value misaligned by row returns `""`.
- `/precios` returns 403 for everything that is not `admin` (including
  `punto_venta`). `/cuadre` returns 403 for `consulta`/`produccion` but
  200 for `punto_venta`. `punto_venta` can enter `/ingest` and save
  an order, but gets 403 on `/records/delete`, `/inventory/alistado` and
  `/review/*`. Existing users (`admin`, `produccion`, `consulta`)
  can still log in after moving `auth.py` to the `users` table.
- Full suite green + `ruff`/`mypy`.
- Manual verification in Docker: load a new order with a new customer,
  load another with the same customer name and confirm it autocompletes,
  check that day's `/cuadre`.

---

## Update: customer types and person data

`customers.type` now has 4 values (`normalize.CUSTOMER_TYPES`):
`TRÁNSITO`, `EMPRESA`, `GESTOR` and `CLIENTE` (the former `PERSONA NATURAL`
renamed; `OTROS` is gone). `DOCUMENT_TYPES` adds `CÉDULA DE EXTRANJERÍA`.
The columns `address`, `neighborhood` and `municipality` were added (only for
`GESTOR` and `CLIENTE`), Owner/Holder (Propietario/Poseedor) (`document_relation`) applies only to
`CLIENTE`, and `customers.transit_license` was removed (the license number becomes
a datum of the ownership card, not of the customer). Migration
`app/migrations/0005_customer_types_and_person_fields.sql`. The earlier mentions
of `PERSONA NATURAL`, `OTROS` as a customer type and
`transit_license` in this document describe the original design.

## Note: ownership cards data layer (Phase 2 of the declarations plan)

The `ownership_cards` table exists (migration `0006`) and its data layer
`app/core/db/ownership_cards.py`, including `find_card_conflicts`. They are used by the
declarations flow (`/declaracion`, `/declaraciones`) and the link with the
manufactured plates (following notes); see `docs/DATABASE.md` and `docs/CORE.md`.

## Note: declarations, business logic (Phase 3 of the declarations plan)

The `declarations` table exists (migration `0007`), its data layer
`app/core/db/declarations.py` and the logic `app/core/declarations.py`
(validate, submit, approve, reject), plus `db.last_fabrication_date` for the
6-month rule. They are used by the public form `/declaracion` and the
internal screen `/declaraciones` (following notes); see `docs/DATABASE.md` and
`docs/CORE.md`.

Update (migration `0009`): a declaration no longer models a single vehicle.
It has a single declarant (the person who signs, once) and several ownership cards
in the `declaration_cards` table (each with its own reason, plate quantity,
owner and alerts). If a single card has alerts, the whole declaration ends up
pending review. Details in `docs/DATABASE.md` and `docs/CORE.md`.

## Note: formal link manufactured plate -> card (last phase of the declarations plan)

Migration `0008` adds `plates.ownership_card_id` (nullable, FK to
`ownership_cards`). The link is automatic and silent: when inserting or editing a
plate, the card with the same plate number is looked up. It is only attempted for
orders of `CLIENTE`, `GESTOR` or untyped customers; never for `EMPRESA` nor
`TRÁNSITO`, nor for ranges, replacements, annulled plates or plates without a real
plate number. The Record detail shows the license of the linked card. Rules and
column: `docs/DATABASE.md`; functions: `docs/CORE.md`.

## Note: internal screen `/declaraciones`

The review screen `/declaraciones` exists (blueprint `declarations.py`):
list with status filter and search, detail with the explanation of each
alert and its real context, authenticated download of signatures and PDF, and approve or
reject (admin only). Listing, viewing and downloading is `@pos_allowed`; the
`production` role gets 403 on everything. `/customers/<id>` also shows the customer's
ownership cards. See `docs/WEB.md`.

## Note: public route `/declaracion` (Phase 4 of the declarations plan)

`app/web/blueprints/public_declaration.py` exists: the first public route that
writes (`GET/POST /declaracion`, no login). It combines honeypot, signed token,
Turnstile and per-IP limit against `declarations`, stores signatures and PDF in
`UPLOADS_DIR` and calls `submit_declaration`. It stays off (503) until the
Turnstile keys are configured. `tests/web/test_public_routes.py` pins the
list of public routes; see `docs/WEB.md`.
