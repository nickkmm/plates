# Production

The **Producción** screen (`/produccion`) is where the production team sees
all the orders created by the point of sale and takes them through to
delivery. The business rules are in `app/production/service.py` (no Flask) and
the data layer in `app/production/repository.py`; the screen is in
`app/production/routes.py` with the templates `production.html` and
`production_detail.html`.

## The full flow

1. **Declaration.** The customer fills in the public form `/declaracion`
   (declarant, ownership cards, photos and signature). It starts as *Por revisar* (to review).
2. **Point-of-sale review.** In `/declaraciones` the point of sale reviews the
   declaration and approves or rejects it. If it has cards reported lost, it
   must also arrive notarized (authenticated by the notary's office).
3. **Order.** With the declaration approved, the point of sale creates the
   order in `/ingest` («Crear pedido»). The order starts in the state *Por realizar* (to be made) and
   the declaration becomes *Atendida* (handled).
4. **Production.** Production opens the order at `/produccion/<order_id>`, sees
   the order data, the plates and the linked declarations (with the photos of
   the ID card and of the ownership cards) and advances it: *En realización* (in progress) and then
   *Lista para entregar* (ready for delivery).
5. **Delivery.** When the customer picks up the plates, production or the
   point of sale marks it *Entregada* (delivered).

The production screen **does not show** prices, payment method, payment date,
receipt, the declarant's signature or the notarized PDF.

## States

```mermaid
stateDiagram-v2
    [*] --> pending: the point of sale creates the order
    pending --> in_progress: Empezar realización
    in_progress --> ready: Marcar lista para entregar
    ready --> delivered: Marcar entregada
    delivered --> ready: Deshacer último cambio
    ready --> in_progress: Deshacer último cambio
    in_progress --> pending: Deshacer último cambio
```

| Stored value | Label on screen |
|---|---|
| `pending` | Por realizar |
| `in_progress` | En realización |
| `ready` | Lista para entregar |
| `delivered` | Entregada |

Each change saves a row in `production_order_events` (from, to, user and
time), which the order shows in its **Historial** (history). Orders that have been fully
voided do not appear on the board.

The state is also shown, as a badge, in the list and detail of
`/declaraciones` (handled declarations) and in the order detail at
`/records/<order_id>`, which also links to Production («Ver en Producción»).

## Who can do each step

| Permission | Roles | What it allows |
|---|---|---|
| `production.view` | admin, production, point_of_sale | View the board, the detail and the photos |
| `production.manage` | admin, production | Perform any step, forward or backward |
| `production.deliver` | admin, production, point_of_sale | Mark a ready order as delivered and undo that delivery |

The point of sale only has `production.deliver`: on an order that is *Lista para
entregar* it sees «Marcar entregada», and on an *Entregada* order it sees «Deshacer último
cambio» (goes back to *Lista para entregar*). Any other step returns 403 and
changes nothing.

## Stale state

Each button sends the state the person was looking at (`expected_status`).
The change is only made if the order is still in that state; if someone moved
it in the meantime, the page is shown again with the message «El estado de la
orden cambió mientras tanto. Revisa el estado actual e inténtalo de nuevo.»
(the order's state changed in the meantime; check the current state and try again)
(400) and nothing is saved. This way two people clicking at the same time
never move the order two steps.

## Routes

| Method | Route | Permission | What it does |
|---|---|---|---|
| GET | `/produccion` | `production.view` | Board: tabs by state (`?status=`), search by order number or plate (`?q=`) |
| GET | `/produccion/<order_id>` | `production.view` | Order detail: data, plates, linked declarations, history and actions |
| GET | `/produccion/<order_id>/declarations/<declaration_id>/file/<kind>` | `production.view` | ID card photo (`id_front`, `id_back`) of a declaration linked to the order |
| GET | `/produccion/<order_id>/declarations/<declaration_id>/cards/<card_id>/photo/<side>` | `production.view` | Photo of an ownership card (`front`, `back`, `used_plates`) of a declaration linked to the order |
| POST | `/produccion/<order_id>/advance` | `production.manage` or `production.deliver` | Advances the order one step (with `expected_status`) |
| POST | `/produccion/<order_id>/undo` | `production.manage` or `production.deliver` | Undoes the last step (with `expected_status`) |
