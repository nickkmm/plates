# Migration plan: stateless API + separate front end

Status: **proposal**, not started. Nothing in this document is implemented yet.

## Goals

1. **Stateless authentication with JWT.** Today the app keeps a Flask
   signed-cookie session (`app/web/auth.py`) and reloads the user's roles and
   permissions from the database on every request.
2. **Separate front end and back end.** Flask becomes a JSON-only REST API;
   the screens move to a Next.js app.
3. **Organize the back end by domain** instead of by technical layer, so
   everything about one business area (routes, logic, SQL) lives in one
   package.

## Non-goals (for now)

- **No change of language or framework.** The back end stays on Flask. A
  later move to Go (Gin + sqlc + oapi-codegen) is the long-term direction, and
  every decision below is chosen so it survives that move: the API contract,
  the SQL migrations and the JWT format are all language-neutral.
- **No ORM.** SQL stays hand-written (`psycopg`), because it ports almost
  one-to-one to sqlc in Go.
- **No schema redesign.** The existing tables stay as they are. The only new
  table is `refresh_tokens` (phase 2), plus whatever later features need.
  The result is stateless app servers: no server-side session (phase 2)
  and no files on the server disk (phase 5).
- **No big-bang rewrite.** Production keeps working at every step; each
  phase is a series of small PRs that deploy on their own.

## Decisions

| Topic | Decision |
|---|---|
| Access token | JWT, **5 minutes**, signed HS256 with a dedicated secret. Claims: `sub` (user id), `username`, `perms` (effective permission codes), `iat`, `exp`. The API checks permissions from the token without touching the database. |
| Refresh token | Random opaque value, 30 days, **stored hashed** in a new `refresh_tokens` table and **rotated** on every use (reuse of an old token revokes the whole family). |
| Revocation | Deactivating a user, changing their roles or changing their password deletes their refresh tokens. The change takes effect within 5 minutes at most (the access token's lifetime). Logout deletes the refresh token. |
| Token storage in the browser | **`httpOnly` + `Secure` cookies**, never `localStorage`. Refresh cookie on path `/api/auth`, not just `/api/auth/refresh`: logout must receive it to revoke it, and one cookie is simpler than two. Access cookie on path `/` during the migration (the Jinja pages will read it too) and narrowed to `/api` in phase 6. |
| CSRF | Same-origin deployment + `SameSite=Strict` cookies, and every state-changing API request must send a custom header (`X-Requested-With`), which a cross-site form cannot set. Flask-WTF goes away once no Jinja form is left. |
| Passwords | Unchanged: werkzeug pbkdf2 hashes in `users.password_hash`. Nobody resets a password. |
| File storage | Uploaded files (declaration PDFs, signatures, ownership-card photos) move from the server disk (`/srv/uploads`) to an **S3-compatible bucket served by MinIO in its own container on the same VPS** (phase 5). The bucket is private; downloads go through the API's permission check. |
| Deployment | Same domain behind the existing nginx: `/api/*` → Flask, everything else → Next.js. No CORS. |
| API contract | OpenAPI generated from the Flask code with `flask-openapi3` (Pydantic request/response models, Swagger UI). The contract file is committed so diffs show API changes. |
| Front-end client | Types and client generated from the contract: `openapi-typescript` + `openapi-fetch`, with **TanStack Query** for caching, loading/error states and refetching. A middleware handles `401` → `POST /api/auth/refresh` → retry. |
| Server-rendered Next pages | Forward the user's cookies to the API when a page renders on the Next server. |

## Target layout

```
app/                         Flask API (back end)
  shared/                    db connection, config, JWT, permission decorator, normalize
  auth/                      login, refresh, logout, me; users and roles admin
    routes.py                blueprint (controller)
    schemas.py               Pydantic request/response models (DTOs)
    service.py               business logic
    repository.py            SQL
  customers/
  pricing/
  inventory/
  production/
  orders/                    point of sale (/ingest), records, review
  cash_summary/
  declarations/              staff screens + the public form
  siigo/                     Siigo client, transfers and billing (docs/SIIGO_INTEGRATION.md)
  portal/                    customer portal: customer pedidos (docs/CUSTOMER_PORTAL.md)
  migrations/                *.sql, unchanged mechanism
frontend/                    Next.js (App Router, TypeScript)
  src/api/                   generated client (do not edit by hand)
  src/app/<domain>/          one route folder per domain
```

Rules:

- A domain does not query another domain's tables; it calls that domain's
  `service`. Code that several domains need goes to `shared/`.
- Dependencies between domains point one way (`orders` → `customers`,
  `orders` → `siigo`, `portal` → `orders`; never back). A cycle means the shared piece belongs in `shared/`.
- Routes stay thin: read input, call a service, return a schema.

## API design (resources)

Every endpoint is a **resource** (a noun) plus an HTTP verb, instead of
today's action URLs (`/produccion/<id>/advance`, `/records/delete`). This is
the list the phase 4 work implements; the exact request/response fields are
defined in each domain's `schemas.py` and end up in the OpenAPI contract.
API paths are English (code), whatever the Spanish screen URLs in the front
end are.

| Domain | Endpoint | Replaces |
|---|---|---|
| auth | `POST /api/auth/login`, `POST /api/auth/refresh`, `POST /api/auth/logout`, `GET /api/auth/me` | `/login`, `/logout` |
| auth | `GET /api/users`, `POST /api/users`, `GET /api/users/{id}`, `PATCH /api/users/{id}`, `PUT /api/users/{id}/password` | `/usuarios`, `/usuarios/nuevo`, `/usuarios/<id>`, `/usuarios/<id>/password` |
| auth | `GET /api/roles`, `POST /api/roles`, `GET /api/roles/{name}`, `PATCH /api/roles/{name}`, `DELETE /api/roles/{name}`, `GET /api/permissions` | `/usuarios/roles/*` |
| customers | `GET /api/customers?q=`, `GET /api/customers/{id}`, `PATCH /api/customers/{id}`, `GET /api/customers/{id}/ownership-cards` | `/customers`, `/customers/<id>` |
| pricing | `GET /api/plate-type-prices`, `PUT /api/plate-type-prices/{plate_type}` | `/precios`, `/precios/<plate_type>` |
| inventory | `GET /api/inventory`, `POST /api/blank-preparations` | `/inventory`, `/inventory/alistado` |
| orders | `GET /api/orders?q=`, `POST /api/orders`, `GET /api/orders/{id}`, `PUT /api/orders/{id}`, `DELETE /api/orders/{id}` | `/records`, `/ingest` (POST), `/records/<id>`, `/records/<id>/save`, `/records/delete` |
| orders | `POST /api/order-drafts` (upload a pedido PDF/Excel, returns the parsed draft, saves nothing) | `/ingest/pdf` |
| orders | `POST /api/plate-lookups` (upload a pedido, returns which orders hold each plate, saves nothing) | `/records/search-pdf` |
| orders | `GET /api/plates?possible_duplicate=true`, `PATCH /api/plates/{id}` (edit fields, or `{"is_possible_duplicate": false}` to resolve), `DELETE /api/plates/{id}` | `/review`, `/review/update`, `/review/resolve`, `/review/delete` |
| production | `GET /api/production-orders?status=`, `GET /api/production-orders/{id}`, `POST /api/production-orders/{id}/events` (`{"type": "advance"}` or `{"type": "undo"}`, stored in `production_order_events`); `POST /api/production-orders/{id}/delivery` (multipart: recipient name, optional document, signature PNG; the only way to reach *Entregada*, see "Delivery signature"), `GET /api/production-orders/{id}/delivery/signature` | `/produccion`, `/produccion/<id>`, `/produccion/<id>/advance`, `/produccion/<id>/undo` |
| cash_summary | `GET /api/cash-summary?from=&to=`, `GET /api/cash-summary/pdf?from=&to=` | `/cuadre`, `/cuadre/pdf` |
| declarations | `GET /api/declarations`, `GET /api/declarations/{id}`, `PATCH /api/declarations/{id}` (`status`: approved/rejected; `notarized`: true/false) | `/declaraciones`, `/declaraciones/<id>`, `.../approve`, `.../reject`, `.../notarized`, `.../notarized/undo` |
| declarations | `GET /api/declarations/{id}/pdf`, `GET /api/declarations/{id}/files/{kind}`, `GET /api/declarations/{id}/cards/{card_id}/photos/{side}`, `GET /api/declarations/{id}/photos.zip` | the matching `/declaraciones/...` and `/produccion/<id>/declarations/...` downloads (one endpoint; the permission check accepts production staff for a declaration linked to an order they can see) |
| declarations (public) | `GET /api/public/declaration-form` (form token + options), `POST /api/public/declarations` (multipart) | `/declaracion` |
| siigo | `POST /api/orders/{id}/siigo-transfer` (retry), `GET /api/siigo-transfers?status=pending`, `GET /api/orders/{id}/siigo-document-preview`, `POST /api/orders/{id}/siigo-documents` | new, see [`SIIGO_INTEGRATION.md`](SIIGO_INTEGRATION.md) §7 |
| portal | `/api/portal/pedidos/...` (customer) and `/api/customer-pedidos/...` (staff) | new, see [`CUSTOMER_PORTAL.md`](CUSTOMER_PORTAL.md) |

Conventions:

- Lists accept filters as query parameters and return `{"items": [...]}`
  so pagination can be added later without breaking clients.
- Status codes: `200` read/update, `201` create (with the new resource),
  `204` delete, `400` invalid body, `401` missing/expired token, `403`
  missing permission, `404` not found, `409` conflict (e.g. duplicate
  order number), `429` rate limited.
- Errors share one shape: `{"error": {"code": "...", "message": "..."}}`.
  `message` is Spanish because the front end shows it to staff; `code` is an
  English identifier the front end can branch on.

## Front end

Architecture, component rules, UX patterns per screen and the visual
identity (colors and typography taken from https://www.ingse.co/) are in
[`FRONTEND.md`](FRONTEND.md).

## Where today's code goes

| Today | Domain |
|---|---|
| `web/blueprints/auth_routes.py`, `users.py`, `web/auth.py`, `core/user_admin.py`, `core/permissions.py`, `core/db/users.py`, `core/db/roles.py` | `auth/` |
| `web/blueprints/customers.py`, `core/db/customers.py`, `core/db/ownership_cards.py` | `customers/` |
| `web/blueprints/pricing.py`, `core/db/pricing.py` | `pricing/` |
| `web/blueprints/inventory.py`, `core/db/inventory.py` | `inventory/` |
| `web/blueprints/production.py`, `core/production.py`, `core/db/production.py` | `production/` |
| `web/blueprints/ingest.py`, `records.py`, `review.py`, `core/ingestion.py`, `core/pedido_upload.py`, `core/pdf_extract.py`, `core/xlsx_extract.py`, `core/db/orders.py`, `core/db/plates.py` | `orders/` |
| `web/blueprints/cash_summary.py`, `core/cash_summary.py` | `cash_summary/` |
| `web/blueprints/declarations.py`, `public_declaration.py`, `web/antispam.py`, `web/uploads.py`, `core/declarations.py`, `core/declaration_pdf.py`, `core/db/declarations.py` | `declarations/` |
| `core/normalize.py`, `core/timezones.py`, `core/db/__init__.py` (connection pool) | `shared/` (customer- and plate-specific constants move to their domain) |

## Delivery signature (new requirement)

An order is closed (*Entregada*) only when the person who receives the
plates signs. The order is opened on a tablet at the counter and the
recipient signs on the screen with a finger or stylus.

- **Flow:** *Entregar* (board card, order panel or point of sale) opens a
  panel with the plates, *Nombre de quien recibe* (prefilled from the
  order's `received_by` when it has one), *Documento* (optional) and a
  signature box. *Confirmar entrega* stays disabled until there is a name
  and a signature. The advance event `ready → delivered` is no longer
  accepted without it: `POST /api/production-orders/{id}/events` with
  `advance` from `ready` answers `409` and points to the delivery
  endpoint.
- **Data:** new table `order_deliveries`: `id`, `production_order_id`,
  `received_by_name` (`TEXT NOT NULL`), `received_by_document` (nullable),
  `signature_key` (the PNG in storage), `delivered_by` (user),
  `delivered_at`, `voided_at`, `voided_by`; a partial unique index allows
  one non-voided delivery per order. Undoing a delivery (the existing undo,
  `delivered → ready`) voids the row instead of deleting it, and keeps the
  signature file, so every signature ever given stays auditable.
- **Storage:** the signature PNG goes through the same storage module as
  declaration photos: the uploads directory until phase 5, the MinIO bucket
  after it (phase 5 moves existing signatures with the rest).
- **Where it shows:** the order panel (*Entrega*: who received, document,
  date, who delivered, the signature), the customer portal (*Entregado*,
  with who received and when), and the order's PDF when there is one.
- **Orders delivered before this exists** have no signature; the panel
  says *Entregado sin firma (antes de la firma digital)*.
- **When:** built with the `production` domain in phase 4 (API, the
  `order_deliveries` migration and the Next screens), so the point of
  sale and the board share one delivery component.

## Coordination with the Siigo integration

[`SIIGO_INTEGRATION.md`](SIIGO_INTEGRATION.md) is a separate plan (10 PRs).
**It is implemented last, after every phase of this plan (phase 8).** So:

- Siigo is built directly in the final architecture: the `app/siigo/`
  domain, JSON endpoints and Next screens. No Jinja routes or templates are
  added for it, and its references to today's Jinja files (`ingest.py`,
  `records.py`, `review.py`) become references to the `orders` service and
  the Next point of sale.
- Until then the point of sale keeps today's behavior: the order number is
  typed by hand (from the NT made in Siigo), and the payment methods and
  receipt fields stay as they are. Phases 4 and 7 build the screens with
  that behavior; phase 8 changes them once.
- The `orders` service built in phase 4 keeps order creation in one
  function, so phase 8 can add the number counter and the transfer there.
- **Permissions:** the portal's `customer_pedidos.review` is added before
  Siigo's `siigo.bill` and `siigo.manage`, which take the next
  `sort_order` values when phase 8 is built.
- **Customer portal:** a portal pedido becomes an order only through the
  point of sale, so once phase 8 is live it gets the assigned number, the
  automatic transfer and billing like any other order. Billing looks the
  customer up in Siigo by document number, so the portal already requires a
  document type and number before giving a customer access.

## Phases

### Phase 1: reorganize by domain (no behavior change)

Status: done (#117 moved the last domain, declarations, and removed the old core package).

Move files into the domain packages above and fix imports. Each Jinja
blueprint keeps its URLs and templates. One PR per domain (or a few small
domains per PR), plus one for `shared/`. The existing test suite is the
safety net: it must pass unchanged apart from import paths.

### Phase 2: JWT authentication

- Migration: `refresh_tokens` (id, user_id, token_hash, family_id,
  expires_at, revoked_at, created_at) + `docs/DATABASE.md` + `init.sql`
  version row.
- `POST /api/auth/login`, `POST /api/auth/refresh`, `POST /api/auth/logout`,
  `GET /api/auth/me` (user + permissions, for the front end to build its
  menu).
- `shared/` decorator `@permission_required(code)` that reads the access
  token cookie. **The existing Jinja pages switch to it too**, so during the
  whole migration there is a single login shared by Flask pages and Next
  pages. The Flask session cookie is retired here.
- User deactivation, role change and password change revoke refresh tokens.
- Login keeps the current behavior: unknown user, wrong password and
  inactive account are indistinguishable to the caller.

### Phase 3: Next.js skeleton and deployment

- `frontend/` project, Dockerfile, `plates-front` container in
  `docker-compose.yml`, nginx routing, CI build + type check.
- Contract generation: `flask-openapi3` set up, `openapi.json` committed,
  `npm run gen:api` producing `src/api/`. CI fails if the committed contract
  or the generated client is out of date.
- Login page, layout with the navigation menu (built from
  `/api/auth/me` permissions), refresh middleware.
- nginx sends a path to Next only once that screen is migrated; everything
  else keeps going to Flask.

### Phase 4: API + screen, one domain at a time

For each domain: JSON endpoints with Pydantic schemas, API tests, the Next
screens, nginx switch, then delete the Jinja template and HTML route.
Suggested order, simplest first:

1. `customers` (pilot: validates the whole pattern before going on)
2. `pricing`
3. `inventory`
4. `auth` admin screens (users and roles)
5. `production`, including the delivery signature (see "Delivery signature")
6. `orders` (point of sale, records, review): the largest and most critical.
   Same behavior as today; the Siigo changes come in phase 8.
7. `cash_summary` (including the PDF download)
8. `declarations` (staff screens and the public form, with Turnstile, the
   anti-spam form token and file uploads)

File downloads (PDFs, photos, the photos zip) stay plain `GET` links under
`/api`: the browser sends the cookie on same-origin requests, so no extra
work is needed.

### Phase 5: S3-compatible file storage

Today `app/web/uploads.py` writes every uploaded file to `UPLOADS_DIR` on the
server's disk. That is the one piece of server-side state left after phase
2: two API instances (or, later, several Go services) would each see a
different set of files.

- A `shared/storage.py` interface (`save`, `open`, `delete`) with an S3
  implementation (`boto3`) and a local-disk implementation for tests and
  local development. Provider, bucket and credentials come from environment
  variables, so changing provider is configuration only.
- The bucket is **private**. Downloads either stream through the API after
  the permission check, or the API answers with a short-lived pre-signed
  URL (a few minutes).
- Stored file names (already random, see `uploads.py`) become the object
  keys, so no database row changes.
- A one-off script copies the existing `/srv/uploads` files to the bucket
  and verifies every key referenced in the database exists there.
- MinIO runs as a `plates-minio` container on the `internal` network only
  (never exposed to the internet), with its data in a Docker volume.
- `scripts/backup_plates.sh` and `docs/BACKUPS.md` are updated for the new
  location, and the backup copies the bucket **off the VPS**: with MinIO on
  the same machine, a lost VPS would otherwise lose the files too.

### Phase 6: cleanup

Remove Jinja templates, `navigation.py`, Flask-WTF, the session
configuration and any HTML-only helper. Narrow the `access_token` cookie's
path from `/` to `/api`.

### Phase 7: customer portal (new feature)

Recurring customers (gestores, companies, transit offices) get users. They
build an open pedido over the week, send it, and it reaches INGSE like a
sworn declaration: reviewed, then turned into an order through the point of
sale. Details and open questions in [`CUSTOMER_PORTAL.md`](CUSTOMER_PORTAL.md).

### Phase 8: Siigo integration (last)

Orders are typed only in plates; plates creates the inventory transfer
(replacing the NT) on save and, from a button with a preview, the cash
receipt or electronic invoice. Built in the final architecture as the
`siigo` domain. Full plan, tasks and open questions in
[`SIIGO_INTEGRATION.md`](SIIGO_INTEGRATION.md).

## Testing

- Phases 1 and 2: the existing pytest suite, plus new tests for token
  issuance, expiry, rotation, reuse detection and revocation.
- Phase 4: API tests with the Flask test client for every endpoint
  (permissions included); front-end type check against the generated
  client; a few Playwright end-to-end tests for the critical flows (login,
  point-of-sale order, production state change).

## Open questions

- **Refresh token lifetime.** 30 days is the default here; staff on shared
  computers may need something shorter.
- **Public declaration form.** It has no login; confirm it stays on the
  same domain and keeps Turnstile + the form token as its only protection.
- **Offline / slow connections at the point of sale.** Not handled today
  either; out of scope unless it becomes a requirement.
