# Customer portal

Status: **proposal**, decisions taken with the owner in November 2026 (see
"Decisions"). Not implemented.

## Problem

Recurring customers (*tramitadores* / gestores, companies, transit offices)
send their plates to INGSE by email, on INGSE's fixed-format *pedido* form
(PDF or Excel). That form is the reason the point of sale can read pedido
files: `app/core/pdf_extract.py` and `app/core/xlsx_extract.py` exist only
to turn those emails into an order. Staff still download each attachment,
upload it, check it and save it, and the customer has no way to see where
an order is.

## The idea in one paragraph

Each recurring customer gets a user in the platform. Instead of filling the
pedido form and emailing it, the customer keeps an **open pedido** in the
portal and **adds plates to it whenever they come in** during the week (or
whatever period they choose). When the batch is complete, they press
**Enviar a INGSE**. The sent pedido arrives to INGSE **the same way a
sworn declaration does**: it shows up in a review list, staff check it and
either press **Crear pedido**, which opens the point of sale prefilled with
those plates (saving the order marks the customer's pedido as attended and
links it to the production order), or **return it to the customer** with a
comment so they fix it and send it again. From then on the customer sees its
progress.

The public sworn-declaration form (`/declaracion`) does not change: it is
for the walk-in, one-to-one customer.

## Customer side

### Accounts

- A portal account belongs to one `customers` row (types `GESTOR`,
  `EMPRESA`, `TRÁNSITO`). A customer can have several users. Each user
  builds their own open pedido; the sent pedidos of every user of the
  customer are visible to all of them.
- Accounts are created by INGSE staff from the customer screen, in either
  of two ways: **an email invitation** (single-use link, expires, the person
  sets their password) or **directly** (staff set a username and a temporary
  password and hand them over; the user must change it on first login). No
  public sign-up. A customer needs a document type and number before
  getting access, because billing finds the customer in Siigo by document
  number. Invitations and password resets mean the platform must
  **send email**, which it does not do today.

### Open pedido (the batch)

- There is **at most one open pedido per user**, visible only to that user
  until sent. It is created on the first plate added and saved on every
  change; there is no "save" button.
- A line is what a row of the paper form holds: **plate or range**
  (from-to), **plate type**, **quantity**, **city**. The plate format is
  validated as the point of sale does.
- Each line records when it was added.
- Lines can be edited or removed until the pedido is sent.
- **Importar Excel**: the customer can still upload the Excel they use
  today; its lines are added to the open pedido (reusing
  `xlsx_extract`).
- **Duplicate warnings only within the customer's own data**: "Ya la
  enviaste en el P-0231". The portal never says that a plate exists in
  another customer's orders; that check happens on the staff side.
- Of the paper form's header, the customer fills **only the payment
  method** (the same list as the point of sale: `EFECTIVO`, `TRANSFERENCIA`,
  `CRÉDITO`, `TARJETA DÉBITO`, `TARJETA CRÉDITO`), asked for when sending. Everything else in the header (order
  number, delivery, tracking number, who dispatches and receives, payment
  date) is filled by INGSE in the point of sale.

### Sending

- **Enviar a INGSE** asks for confirmation ("Vas a enviar 11 placas.
  Después de enviarlo ya no puedes cambiarlo") and freezes the pedido. It
  gets a number (`P-0232`). The next plate added starts a new open pedido.

### History and tracking

- "Enviados": every sent pedido with its state:
  *Por revisar* → *Recibido · pedido 4123* → *En producción* → *Listo* →
  *Entregado*, or *Devuelto para corregir* with INGSE's comment.
- **Returned pedidos** are editable again (lines and payment method) and
  are resent with the same number. They are separate from the user's open
  pedido, so a returned pedido never mixes with plates added since.
- **No notifications** at first: the customer sees changes in the portal.
  A returned pedido is shown as a banner on the portal home.
- The states after *Recibido* come from the linked production order
  (`production_orders` and `production_order_events`), so the customer sees
  exactly what the production board shows.
- Search by plate across the customer's own pedidos.

## INGSE side

- A new list, **Pedidos de clientes**, built like the declarations list:
  tabs *Por atender*, *Devueltos*, *Atendidos*, with a count in the
  sidebar. Point-of-sale staff and admins use it: a new permission
  `customer_pedidos.review`, seeded for `admin` and `point_of_sale` and
  assignable to any role.
- The detail shows the header data and the lines, each with the staff-side
  checks (plate already in another order, format), and the value estimated
  from the tariff.
- **Crear pedido** opens the point of sale prefilled with the lines and
  header, exactly as uploading a pedido PDF does today
  (`ingest._form_from_pedido`), carrying the portal pedido's id the way
  `declaration_id` is carried today. Saving the order sets the portal
  pedido's `production_order_id`: it becomes *Atendido*.
- **Devolver para corregir** asks what to fix; the pedido goes back to the
  customer as editable, and comes back to *Por atender* when resent.
- **One portal pedido always becomes one production order**, never split.
- Everything after that (prices, receipts, payment, production, Siigo) is
  the normal order flow; the portal adds no second path into production.

## Data model (sketch)

| Table | Purpose | Key columns |
|---|---|---|
| `users` (existing) | Add `customer_id` (nullable FK). Null = staff, set = portal user | |
| `customer_pedidos` | One batch | `id`, `customer_id`, `created_by` (user), `status` (`open`, `submitted`, `returned`; *attended* = submitted with `production_order_id` set, as for declarations), `number`, `payment_method`, `submitted_at`, `returned_at`, `returned_by`, `return_note`, `production_order_id` |
| `customer_pedido_lines` | Its lines | `customer_pedido_id`, `line_type` (`placa`/`rango`), `plate_type`, `plate_number`, `plate_to`, `quantity`, `city`, `added_at` |

A partial unique index enforces one `open` pedido per user (`created_by`). Line columns
match `ParsedLine` in `pdf_extract.py`, so the Excel import and the
point-of-sale prefill reuse the same shape. New role `customer` with
`portal.*` permissions.

## API (sketch)

- Customer: `GET /api/portal/pedidos/open`, `POST /api/portal/pedidos/open/lines`,
  `PATCH|DELETE /api/portal/pedidos/open/lines/{id}`,
  `POST /api/portal/pedidos/open/import` (Excel), `POST /api/portal/pedidos/open/submit`,
  `GET /api/portal/pedidos`, `GET /api/portal/pedidos/{id}`
- Staff: `GET /api/customer-pedidos?status=`, `GET /api/customer-pedidos/{id}`,
  `PATCH /api/customer-pedidos/{id}` (`{"status": "returned", "return_note": "..."}`);
  the order is created by `POST /api/orders` with `customer_pedido_id`.
- Customer, returned pedidos: `PATCH /api/portal/pedidos/{id}` and its lines,
  `POST /api/portal/pedidos/{id}/submit`.
- Accounts: `POST /api/customers/{id}/users` (direct), `POST /api/customers/{id}/invitations`,
  `POST /api/auth/invitations/{token}`, `POST /api/auth/password-reset`.

## Security

- Every portal query filters by the `customer_id` in the access token,
  never by an id from the request; tests check that customer A gets `404`
  for customer B's pedidos.
- Portal tokens are rejected by every staff endpoint.
- No cross-customer information leaks through duplicate checks (see above).
- Rate limits on login, invitations and line creation; Excel upload size limits.
- Audit: who sent, returned and attended each pedido.

## Dependencies

JWT auth (phase 2), JSON API and front end (phases 3 and 4) from
[`MIGRATION_PLAN.md`](MIGRATION_PLAN.md). Unlike the previous draft it needs
no document uploads, so it does **not** depend on object storage. Planned
as phase 7. Siigo ([`SIIGO_INTEGRATION.md`](SIIGO_INTEGRATION.md)): nothing
portal-specific, and it comes after the portal (phase 8). *Crear pedido*
goes through the point of sale, so once Siigo is live the order gets the assigned number ("Recibido · pedido 4123" shows it), the automatic
transfer and the billing button like any other order. Showing the customer
their invoice (FV-3) in the portal is a later addition.

## Decisions

| Question | Decision |
|---|---|
| How accounts are created | Both: email invitation, or staff create the user directly |
| Open pedidos | One per user (not shared by the customer's users) |
| Who handles portal pedidos | Point of sale and admin (new permission `customer_pedidos.review`) |
| A pedido with problems | Returned to the customer to fix and resend (no partial acceptance) |
| Portal pedido → production orders | Always exactly one |
| Header fields the customer fills | Only the payment method |
| Notifications | None at first; the portal shows the state |
| Sent pedidos | Shared: every user of the customer sees all of the customer's sent pedidos (open pedidos stay personal) |
| Rejecting | No final reject: a pedido with problems is only returned to fix; one sent by mistake is returned and the customer removes its lines |
| Sending email | SMTP through INGSE's current mail hosting (a sender account such as `no-responder@ingse.co`) |

## Sending email

The portal sends very few emails: the invitation (once per user) and the
password reset. They go through **INGSE's existing mail hosting**, the same
one that serves the `@ingse.co` mailboxes today (its MX is `ingse.co` and
its SPF already authorizes the hosting's relay), from a dedicated account
such as `no-responder@ingse.co`.

- The code sends through plain SMTP (`smtplib`, TLS) behind a small
  `EmailSender` interface in `shared/`, configured by environment variables
  (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `MAIL_FROM`).
  Moving later to a dedicated service (Resend, Amazon SES) is a
  configuration change, since they also accept SMTP.
- Tests use a fake sender that records messages; nothing is sent in CI.
- Known limits, accepted given the volume: hosting send-rate limits and a
  higher chance of landing in spam. The invitation screen shows the link to
  staff too, so they can send it by WhatsApp if the email does not arrive.
- To check at deploy time: the VPS can reach the hosting's SMTP port (587
  or 465), and the account's credentials go in the `ENV_FILE_B64` secret.
