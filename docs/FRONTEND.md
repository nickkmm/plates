# Front end: architecture, UX and visual identity

Status: **proposal**, part of [`MIGRATION_PLAN.md`](MIGRATION_PLAN.md).
Nothing here is implemented yet.

Today every screen is a Jinja template with its own inline `<style>` block,
so there is no shared component, the same table or notice looks slightly
different on each page, and every validation error needs a full page round
trip. The Next.js front end replaces that with one component library and
one set of interaction patterns used by every screen.

## Stack

| Concern | Choice | Why |
|---|---|---|
| Framework | Next.js (App Router), TypeScript `strict` | Server Components for read-heavy pages, file-based routing, layouts |
| Styling | Tailwind CSS with the design tokens below | Tokens as utility classes; no per-page CSS |
| Base components | shadcn/ui (Radix primitives), copied into `src/components/ui/` | Accessible (focus, keyboard, ARIA) out of the box, and the code is ours to theme |
| Server state | TanStack Query + the client generated from the OpenAPI contract | Caching, refetch, optimistic updates, typed end to end |
| Tables | TanStack Table | Sorting, filtering, column visibility, row selection |
| Forms | React Hook Form + Zod | Field-level validation before submit; schemas generated from the contract where possible |
| URL state | `nuqs` | Filters, tabs and search live in the URL: shareable, survive refresh, back button works |
| Command palette | `cmdk` (shadcn `Command`) | Global `Ctrl+K` search and actions |
| Toasts | `sonner` | Success messages with **Undo**, non-blocking errors |
| Drag and drop | `dnd-kit` | Production board |
| Component catalog | Storybook | Every shared component documented and reviewed in isolation |
| Tests | Vitest + Testing Library (components), Playwright (end to end), `axe` (accessibility) | |

## Folder structure

Organized by domain, mirroring the back end:

```
frontend/src/
  app/                          routes only: thin pages that compose features
    (auth)/login/page.tsx
    (app)/layout.tsx            app shell: sidebar, top bar, command palette
    (app)/orders/new/page.tsx   point of sale
    (app)/orders/page.tsx
    (app)/production/page.tsx
    ...
  features/                     one folder per domain
    orders/
      api/                      query keys + hooks (useOrders, useCreateOrder)
      components/               OrderForm, PlateLinesEditor, OrderSummary, ...
      hooks/
      schemas.ts                Zod schemas
      index.ts                  the feature's public API
    production/
    customers/
    ...
  components/
    ui/                         shadcn primitives: Button, Input, Dialog, Sheet, ...
    (shared composites)         DataTable, PageHeader, EmptyState, StatusBadge,
                                MoneyInput, PlateInput, DateRangePicker,
                                ConfirmDialog, FileDropzone, KeyboardHint
  lib/
    api/                        generated client (never edited by hand) + fetch middleware
    auth/                       session from /api/auth/me, permission helpers
    format.ts                   COP currency, dates (dd/mm/yyyy, America/Bogota)
  styles/globals.css            design tokens
```

Directory names (and therefore URLs) are English, per the repo's naming rule;
everything the user reads on screen is Spanish.

### Rules

1. **Dependency direction:** `app` → `features` → `components` → `components/ui`.
   Never the other way. A feature imports another feature only through its
   `index.ts`. Enforced with `eslint-plugin-boundaries`, so a violation fails CI.
2. **`components/ui` knows nothing about the business.** No API calls, no
   Spanish copy, no domain types.
3. **Presentational vs. connected components.** A component either fetches
   data (through a feature hook) or renders props, not both. Presentational
   components are the ones in Storybook.
4. **One component per file, under ~150 lines.** Bigger means it should be
   split.
5. **No inline styles and no one-off colors.** Only tokens.
6. **Query keys from a factory** per feature (`orderKeys.list(filters)`,
   `orderKeys.detail(id)`), so invalidation after a mutation is precise.
7. **Every route has `loading.tsx` (skeleton) and `error.tsx`.** No blank
   screens, no raw stack traces.
8. **Permissions in one place:** `<Can permission="orders.create">` and a
   `usePermission` hook read the permissions from `/api/auth/me`. The
   sidebar and the actions hide what the user can't do; the API still
   enforces it.
9. **Formatting in one place:** currency, dates and plate numbers always go
   through `lib/format.ts`. A plate number is always shown with the `<Plate>`
   component, which takes the plate type and looks like the real plate:
   yellow for `PARTICULAR` types, a slightly gray white (`#E4E4E0`, so it
   stands apart from the white of the interface) for `PÚBLICO` types, green with white
   text for `REMOLQUE`. The color is derived from the type in one function,
   never chosen per screen, and the type is also given as text (tooltip and
   accessible name), so the color is never the only cue.
10. **No prop drilling: React Context for shared client state.** A value
    that many components at different depths need is provided once and read
    with a hook, never passed down through components that don't use it:
    - App-wide providers in `src/app/providers.tsx`: `SessionProvider`
      (current user and permissions from `/api/auth/me`, behind
      `useSession` / `usePermission` / `<Can>`), `QueryClientProvider`, the
      toast and command-palette providers.
    - Feature-scoped providers for a screen's own state: e.g.
      `OrderDraftProvider` (lines, totals, payment of the point-of-sale
      order being typed, read by the line editor, the summary and the
      shortcuts), `PortalPedidoProvider` (the customer's open pedido) and
      the production board's filters and selection.
    - Each context lives in its feature (`features/<domain>/context/`),
      exports a `Provider` and a `useX()` hook that throws when used outside
      it, and is never imported directly as `XContext`.
    - Server data is **not** put in Context: it stays in TanStack Query
      (already shared through its cache) and URL state stays in `nuqs`.
      Context holds client state only, so it doesn't duplicate or go stale
      against the server.
    - Split a context when parts change at different rates (state vs.
      dispatch, or one context per concern) and memoize the provider value,
      so a keystroke in one field doesn't re-render the whole screen.
    - If shared client state outgrows Context (many independent slices,
      frequent updates), the next step is a small store (`zustand`), decided
      per case.

## UX principles

Drawn from point-of-sale and operations software that solves the same
problems: Shopify POS and Square (order entry, order manager with
New / In progress / Ready views), Odoo Manufacturing and Shop Floor (production
kanban cards), Linear (keyboard-first, command palette), and common
data-table guidance (persistent filter bar, saved filters, confirmed bulk
actions).

1. **Fewer steps for the frequent task.** The point of sale and the
   production board are used all day; every click saved there matters more
   than anywhere else.
2. **Keyboard-first, touch-friendly.** Every frequent action has a shortcut,
   shown in tooltips and in the command palette so people learn them. Touch
   targets are at least 44px for tablets in the workshop.
3. **Validate while typing, not after saving.** Plate format, duplicate
   plates and duplicate order numbers are checked as the field is filled,
   with the message next to the field.
4. **Undo instead of "are you sure?"** for reversible actions (advancing an
   order, resolving a duplicate). Confirmation dialogs only for destructive
   ones (deleting an order), and they say exactly what will be lost.
5. **Stay in context.** Details open in a side panel over the list instead
   of a new page, so the list position and filters are kept.
6. **Every state designed:** loading (skeleton), empty (with the next
   action), error (what happened and what to do), success (toast).
7. **The screen tells the truth now.** Production and declaration lists
   refresh on window focus and every 30 seconds, so a screen left open in
   the workshop is current.
8. **Accessibility: WCAG 2.2 AA.** Visible focus ring, labels on every field,
   contrast checked against the tokens.

## Screens

### App shell

- **Sidebar** grouped by work area instead of today's flat row of tabs:
  *Ventas* (Punto de venta, Pedidos, Clientes), *Producción* (Tablero,
  Inventario), *Declaraciones*, *Administración* (Precios, Cuadre de caja,
  Usuarios). Collapsible; shows only what the user's permissions allow.
- **Top bar:** global search, user menu, logout.
- **Command palette (`Ctrl+K`):** search a plate, order number or customer
  from anywhere and jump to it; run actions ("Nuevo pedido", "Cuadre de hoy").

### Point of sale (`/ingest` today)

Reference: Shopify POS and Square checkout.

- One screen, two columns: the order form and plate lines on the left, a
  sticky **summary** on the right (customer, lines, total, payment method,
  Save button).
- **Customer combobox** with search against the API and "Crear cliente
  nuevo" inline (today the whole customer list is embedded in the page).
- **Plate lines editor:** `Enter` adds a line, plate input normalizes as you
  type (`abc 123` → `ABC123`), price prefilled from the tariff and editable.
  Duplicate plate or order number shows an inline warning with a link to
  the existing order, before saving.
- **Upload pedido (PDF/Excel)** as a drop zone on the same screen; it fills
  the form instead of being a separate page.
- **Draft kept locally** so a refresh or a closed tab doesn't lose the order.
- Shortcuts: `Ctrl+Enter` save, `Alt+N` new line, `/` focus customer.

- **Siigo** (phase 8, last; [`SIIGO_INTEGRATION.md`](SIIGO_INTEGRATION.md)):
  the order number becomes read-only ("Se asigna al guardar"); payment method includes
  cards, with the receiving account for transfers and a due date for
  credit; the receipt-type and receipt-number fields go away because
  the receipt or invoice is created in Siigo from the order.

### Production (`/produccion` today)

Reference: Odoo Manufacturing kanban and Shop Floor, Square's order views.

- **Board** with one column per state (*Por realizar*, *En realización*,
  *Lista para entregar*, *Entregada* limited to today), with a count in each
  column header.
- **Card:** order number, customer, plate numbers, delivery date badge
  (red overdue, amber today), declaration indicator.
- Advance with one button on the card or by dragging it; a toast offers
  **Undo** (today: open the detail page, then advance).
- Toggle to a **list view** for searching; filters by customer and
  delivery date; large-screen mode for a wall display in the workshop.
- **Delivering requires the recipient's signature** (see
  [`MIGRATION_PLAN.md`](MIGRATION_PLAN.md), "Delivery signature").
  *Entregar* opens a panel with the plates, *Nombre de quien recibe*,
  *Documento* (optional) and a large signature box; *Confirmar entrega* is
  enabled only with a name and a signature. Built tablet-first: the box is
  at least 200px high and full width, uses pointer events (finger, stylus
  and mouse) with `touch-action: none` so drawing never scrolls the page,
  scales for high-density screens, and has *Borrar firma*. One
  `<SignaturePad>` component (in `components/`, presentational, exports a
  PNG) is shared by the board, the order panel and the point of sale. The
  toast after delivering offers *Deshacer*, which voids the delivery.

### Orders (`/records` today)

Reference: Square Order Manager, data-table guidance.

- Data table with a **persistent filter bar** (date range, customer,
  payment method, production state) and search by plate or order number;
  filters live in the URL.
- Row click opens the order in a **side panel**: header, plates, payments,
  (from phase 8) a **Siigo** block (transfer `CC-100-…` state with *Reintentar*, and
  *Facturar en Siigo* with a preview of the receipt or invoice), and a
  **timeline** of its production events.
- From phase 8, an *Envíos a Siigo* list (admin) shows transfers that are pending or
  failed, with *Reintentar*.
- "Buscar por archivo" stays, as a drop zone that shows each plate's
  status in a result table.
- Export the current view to Excel.

### Duplicates review and declarations

Reference: inbox-style triage (list on the left, item on the right).

- **Duplicates:** the flagged plate and the existing one side by side,
  differences highlighted; actions *Editar*, *No es duplicado*, *Eliminar*;
  the next item opens automatically.
- **Declarations:** photos with zoom, approve or reject with `A` / `R`,
  auto-advance to the next pending one.

### Cash summary (`/cuadre` today)

- KPI cards: total and total per payment method for the range.
- Date range presets: *Hoy*, *Ayer*, *Esta semana*, *Este mes*, custom.
- Detail table and PDF download.

### Public declaration form (`/declaracion` today)

Used by customers on their phones.

- **Mobile-first, step by step** with a progress indicator (datos,
  vehículos, fotos, firma, confirmación).
- Camera capture for the ownership-card photos, with a preview before
  upload; per-field errors; progress kept if the connection drops.

### Users, roles, prices, inventory, customers

Same data-table + side-panel pattern; edits inline where the field is
simple (a price), in the panel otherwise.

## Writing the interface (UX copy)

Today's screens explain too much, in the wrong place, in the voice of
someone answering a question instead of an app. A few real examples and
what replaces them:

| Today | Replacement |
|---|---|
| "Revisá esto antes de guardar — los datos vienen del archivo, todavía no se guardó nada." | Banner: "Datos cargados del archivo. Revísalos antes de guardar." |
| "Ojo: esta(s) placa(s) ya existen — no se guardó nada todavía." plus a paragraph about replacing orders | Next to the field: "Ya está en el pedido 4127" (link) |
| Production intro: "Todas las órdenes creadas por el punto de venta. Cada orden pasa por Por realizar → …" | Removed: the board columns already say it |
| Cash summary intro: "Agrupado por fecha de la orden y forma de pago. No incluye placas anuladas — nunca se cobraron." | Removed; a tooltip on the total: "No incluye placas anuladas" |
| "Estas placas no tienen un precio asignado — asignale uno desde el pedido para que entren al cuadre." | "2 placas sin precio no están en el total. Ver pedidos" |
| "Subí el pedido (PDF o Excel) para revisar si esas placas ya existen — no se guarda nada." | Button "Buscar por archivo" |

Rules:

1. **Address the user as "tú", always.** Today the app mixes Argentine
   *voseo* ("Revisá", "elegí", "subí", "guardala") with "tú" (the public
   form). Colombian users expect "tú" (or "usted"); never *voseo*.
2. **Labels, not sentences.** A field is labeled with a noun ("Forma de
   pago"), a button with a verb and its object ("Guardar pedido",
   "Aprobar"). No "Haz clic aquí".
3. **One line of help at most**, and only where a person would otherwise
   get it wrong (a plate format, a file type). If a screen needs a
   paragraph to be understood, the screen is wrong.
4. **Never explain how the system works inside** (re-snapshotting prices,
   which table is updated). Say what happens to the user's work.
5. **Messages next to their cause.** A field error goes under the field; a
   page-level problem goes in one banner at the top; success is a toast.
6. **Errors say what happened and what to do**: "No pudimos leer el
   archivo. Sube un PDF o un Excel (.xlsx)." Never "Error" alone, never a
   stack trace, never blame ("Ingresaste mal…").
7. **Empty states:** one line and the next action ("Sin pedidos para hoy ·
   Nuevo pedido").
8. **Real plurals,** not "placa(s)", "orden(es)", "existe(n)".
9. **No "Ojo:", no em dashes, no exclamation marks** in interface text.
10. **Sentence case** ("Nuevo pedido", not "Nuevo Pedido"); business values
    stored in capitals (`CARRO PARTICULAR`, `EFECTIVO`) are shown as stored.
11. **One word per concept** (glossary below), used on every screen.

### Glossary (to confirm)

| Concept | Word on screen | Notes |
|---|---|---|
| `production_orders` row | **Pedido** | Today the UI mixes "orden" and "pedido". `client_order_number` ("PEDIDO #" on the paper form) then shows as "Referencia del cliente". |
| `production_orders.order_number` | **N.º de pedido** | |
| Order sent from the portal, not yet accepted | **Solicitud** | |
| `requested_by` / `customers` | **Cliente** | |
| Blank metal sheet in inventory | **Lámina** | |
| `blank_preparations` | **Alistamiento** | |

## Visual identity

The front end follows the look of the public site,
https://www.ingse.co/ (WordPress + Elementor, "a13" theme). Values below were
read from that site's CSS and checked against screenshots (November 2026).

| Token | Value | Where the site uses it |
|---|---|---|
| `brand-yellow` | `#FFCC00` | Links, "LOGIN", cart icon, page titles on dark bars, primary call-to-action buttons |
| `ink-900` | `#141414` | Elementor "primary" global color; darkest text |
| `ink-800` | `#1E1E1E` / `#212121` | Footer and dark header bars |
| `ink-700` | `#222222` | Headings on light backgrounds; dark pill buttons ("Acceso", "Añadir al carrito") |
| `ink-600` | `#353535` | Page title bar background |
| `gray-600` | `#54595F` | Secondary text (Elementor "secondary") |
| `gray-500` | `#7A7A7A` | Body text (Elementor "text") |
| `gray-200` | `#E5E5E5` / `#EEEEEE` | Borders, dividers |
| `surface` | `#FFFFFF`, `#F9F9F9` | Content background, subtle panels |

- **Typography:** the system font stack, no web font:
  `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif`.
  Headings weight 600; large page titles (about 60px) in `brand-yellow` on a
  dark bar.
- **Layout:** dark top bar with the shield logo on the left and a light,
  thin navigation on the right; light content area; dark footer with the
  logo centered, address and phone on the right.
- **Buttons:** pill shaped (`border-radius: 9999px`). Default: `ink-700`
  fill, white text. Primary: `brand-yellow` fill.
- **Logo:** the INGSE shield, `https://www.ingse.co/wp-content/uploads/2021/06/logo_ingse.png`
  (114×143). Ask for the original vector file before shipping, and commit
  it to `frontend/public/`.
- **Accessibility adjustments** (deliberate differences from the site):
  white text on `brand-yellow` (the site's cookie button) is about 1.6:1
  contrast, so primary buttons use `ink-900` text instead; yellow text is
  only used on dark backgrounds, never on white.
- The tokens live in one place (the Tailwind theme or CSS variables in
  `frontend/src/app/globals.css`) so every screen uses them by name.

### Dark mode

Light is the default; dark mode follows the operating system until the user
picks one with the toggle in the top bar (remembered per browser). It is
built from the same tokens: the theme only redefines the CSS variables
(`--surface`, `--canvas`, grays, text inks and the status tints) under a
`dark` class, so components never hard-code a color for one theme. Things
that represent physical objects keep their real colors in both themes:
plates, and the dark text on the yellow brand color. Every screen is
checked in both themes during the review (contrast included).

### Login screen

The brand panel carries the slogan **"Una buena señal para ti"** in red
(`--brand-red`, `#E53935`, large bold text on the dark panel).

## Prototypes

> **These are prototypes, not specifications.** They were built quickly to
> discuss direction (layout, flows, tone, visual identity) and the owner has
> already found errors and missing pieces in them. Nothing in `prototypes/`
> is a source of truth for behavior, validation, data or permissions: the
> source of truth is this document, [`MIGRATION_PLAN.md`](MIGRATION_PLAN.md),
> [`CUSTOMER_PORTAL.md`](CUSTOMER_PORTAL.md),
> [`SIIGO_INTEGRATION.md`](SIIGO_INTEGRATION.md) and today's behavior of the
> app.

Clickable, static prototypes of every screen (fake data, no backend) are in
`prototypes/`: open `prototypes/index.html` in a browser. The moon button in
the top bar switches to dark mode.

### Mandatory review before and while building each screen

Every screen built in phase 4 (and the portal in phase 7) goes through an
**exhaustive review** before its PR is approved. The prototype is only a
starting point; the review checks the real screen against the real
requirements:

1. **Parity with today.** List every field, action, validation, message,
   permission check and edge case of the current Jinja screen and of its
   route in `app/web/blueprints/`; each one is present in the new screen or
   its removal is a documented decision. Nothing is dropped because the
   prototype did not show it.
2. **Every state of every component:** empty, loading, error, long text,
   many rows, zero and one item (singular/plural), disabled, read-only,
   without permission, on a narrow screen and on a tablet.
3. **Every flow end to end** against the API, including failures (network
   error, `409`, `403`, expired session).
4. **Keyboard and accessibility:** tab order, focus visible, shortcuts, labels,
   contrast (`axe` clean).
5. **Writing rules** of this document (voice, plurals, glossary).
6. **Components in Storybook** with each of their states, reviewed on their
   own before they are used in a screen.

The issue of each screen carries this checklist, and the PR description
states how each point was verified.

### A base, not a ceiling

The prototypes, and the designs in this document, are a **starting point**.
When a screen is actually built and an improvement shows up (a clearer
flow, fewer steps, a better component, a pattern that fits the real data
better, an accessibility or performance gain), it **must be made**, not
skipped because "the prototype did it this way". Record the change and the
reason in the PR description, and update this document when the change
affects other screens or the shared rules.

## Process

1. Build the tokens and the shared components in Storybook first, and
   review them before any screen.
2. Clickable prototypes of the point of sale and the production board,
   reviewed with the people who use them, before building those screens.
3. Then the screens, in the domain order of the migration plan.
