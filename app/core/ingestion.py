"""The single ingestion backend: process_rows().

Every door (paste-JSON, image->API, Excel migration) converges here. Given a
list of ExtractedRow, this normalizes them, detects duplicates, groups by
order_number, inserts production_orders + plates, recomputes
inventory_categories, and returns a Summary.

process_rows/replace_order/find_duplicate_plate_numbers all take `conn` as
their first parameter (Fase 1d) -- they read/write through an already-open
connection the CALLER owns, exactly like every function in app/core/db/, and
never commit/rollback it themselves. Before Fase 1d each of the three opened
its OWN `db.get_conn()` internally, which meant a single logical operation
(e.g. POST /ingest: check duplicates, then insert) actually ran across
multiple separate transactions -- not atomic, and the kind of bug that gets
more expensive to keep doing wrong once real money (unit_price) is on the
line. See tests/core/test_ingestion.py's atomicity test for the specific
failure mode this closes.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal

import psycopg
from psycopg.rows import DictRow
from pydantic import BaseModel

from app.core import db
from app.core.models import ExtractedRow
from app.core.normalize import inventory_category, is_real_plate, normalize_row


class Summary(BaseModel):
    orders_inserted: int = 0
    plates_inserted: int = 0
    blank_preparations: int = 0
    possible_duplicates: int = 0
    voided: int = 0
    plate_annulled: int = 0
    batch_replacements: int = 0
    review_notes: list[str] = []
    # Only set by replace_order() (process_rows never deletes anything):
    # how many pre-existing production_orders rows sharing the submitted
    # order_number got fused away, and how many of their plates went with them.
    orders_replaced: int = 0
    plates_deleted: int = 0


def is_alistado(row: ExtractedRow) -> bool:
    """ALISTAMIENTO rows record blanks prepared, not plates produced.

    In the data the marker is in SOLICITADO POR (requested_by); a few rows also
    carry it in ALISTADO POR (prepared_by). We check both so it's never missed.
    """
    return "ALISTAM" in row.requested_by.upper() or "ALISTAM" in (row.prepared_by or "").upper()


def _dedup_candidates(rows: list[ExtractedRow]) -> tuple[set[str], Counter[str]]:
    """Real (non-voided) plate_numbers among `rows`, as both a set (candidates
    to check against the DB via db.existing_plate_numbers) and a Counter (to
    catch a number repeated more than once within the same batch, which is
    suspicious even if it's never been seen before).

    "Real" means is_real_plate(r.plate_number) and not r.is_voided --
    samples/serials/INGSE placeholders repeat legitimately and are never
    duplicates, and a voided line was never actually produced. This exact
    filter used to be copied across 6 separate comprehensions in this module
    (find_duplicate_plate_numbers/process_rows/replace_order each computed
    both a live_candidates set AND a batch_counts Counter from it
    separately); the two remaining copies are each function's own per-row
    `is_dup` check just below, which needs the same filter combined with
    already_present/batch_counts and so isn't naturally expressed here.
    """
    real_numbers = [r.plate_number for r in rows if is_real_plate(r.plate_number) and not r.is_voided]
    return set(real_numbers), Counter(real_numbers)


def _count_row(summary: Summary, row: ExtractedRow, is_dup: bool) -> None:
    """Update summary's per-inserted-row counters -- this whole block used to
    be duplicated between process_rows and replace_order."""
    summary.plates_inserted += 1
    if is_dup:
        summary.possible_duplicates += 1
    if row.is_voided:
        summary.voided += 1
    if row.is_plate_annulled:
        summary.plate_annulled += 1
    if row.is_batch_replacement:
        summary.batch_replacements += 1


# Fase 2 (POS): fields meant to carry ONE real value per order/pedido, even
# though every row in the group repeats them (same as requested_by always
# has). If a group's rows disagree on one of these it's a data smell worth a
# human's second look -- generalizes the review-note check that used to
# exist for requested_by alone. `requested_by` doubles as the "resolved to
# the same customer" check: customer_id comes from
# db.resolve_or_create_customer(head.requested_by), keyed off this exact
# field, so rows agreeing here also resolve to one customer.
# reason/vehicle_brand/unit_price are deliberately EXCLUDED -- those are
# genuinely per-line (a vehicle brand belongs to one plate/vehicle, not the
# whole order), so per-row differences there are normal, not a smell.
_ORDER_LEVEL_FIELDS: tuple[str, ...] = (
    "requested_by",
    "receipt_type",
    "receipt_number",
    "payment_method",
    "tracking_number",
    "client_order_number",
    "delivery_date",
    "payment_date",
    "dispatched_by",
    "received_by",
    "notes",
)


def _order_level_mismatches(order_number: str, group: list[ExtractedRow]) -> list[str]:
    """One review_notes message per field in _ORDER_LEVEL_FIELDS that
    disagrees across `group`."""
    return [
        f"Order {order_number}: multiple {field} values in the same order."
        for field in _ORDER_LEVEL_FIELDS
        if len({getattr(r, field) for r in group}) > 1
    ]


def _snapshot_unit_price(row: ExtractedRow, prices: dict[str, Decimal]) -> ExtractedRow:
    """New plate, insert-time unit_price fallback (the first case of the
    re-snapshot rule -- see docs/DATABASE.md / app/core/db/plates.py's
    _resnapshot_unit_price for the other two, which apply on EDIT instead).

    If `row` already carries a unit_price (the /ingest form always sends one,
    pre-filled via JS from the current tariff and left editable -- real
    prices vary by negotiated discount), it's kept verbatim. Only a row with
    no unit_price at all (e.g. the historical Excel migration door, which
    never sets one) falls back to the CURRENT suggested tariff for its
    plate_type, taken from `prices` (a single db.current_prices(conn)
    snapshot shared by the whole batch, not re-queried per row). Stays None
    if that type has no configured tariff yet -- never a silent 0.
    """
    if row.unit_price is not None:
        return row
    return row.model_copy(update={"unit_price": prices.get(row.plate_type)})


def find_duplicate_plate_numbers(
    conn: psycopg.Connection[DictRow], rows: list[ExtractedRow]
) -> set[str]:
    """Read-only: which plate_numbers in this batch would be flagged
    is_possible_duplicate if inserted right now (already in the DB, or
    repeated within the batch itself). Mirrors process_rows' own detection
    logic without writing anything — used by the /ingest form to warn
    before saving, not just after.
    """
    normalized = [normalize_row(r) for r in rows]
    plate_rows = [r for r in normalized if not is_alistado(r)]
    live_candidates, batch_counts = _dedup_candidates(plate_rows)
    already_present = db.existing_plate_numbers(conn, live_candidates)
    return {pn for pn in live_candidates if pn in already_present or batch_counts[pn] > 1}


def process_rows(
    conn: psycopg.Connection[DictRow], rows: list[ExtractedRow], created_by: int | None = None
) -> Summary:
    """Normalize, dedup, insert, and recalc inventory for a batch of rows.

    Runs entirely through `conn`, which the caller owns: it neither commits
    nor rolls back, so `with db.get_conn() as conn: process_rows(conn, rows)`
    lands the whole batch atomically -- either everything below commits
    together on a clean exit, or nothing does.

    created_by (Fase 2) is the id of the logged-in user who triggered this
    save (session["user_id"]) -> production_orders.created_by. Defaults to
    None so the historical Excel migration (app/migrate.py, which has no
    notion of a logged-in user) and any other caller not yet updated keep
    working unchanged.
    """
    normalized = [normalize_row(r) for r in rows]

    # Split ALISTAMIENTO (blanks prepared) from plate rows.
    alistado_rows = [r for r in normalized if is_alistado(r)]
    plate_rows = [r for r in normalized if not is_alistado(r)]

    # Plate numbers appearing more than once within this same batch are also
    # suspicious, not just ones already in the DB. Only real plate identifiers
    # count — samples/serials/INGSE repeat legitimately and are not duplicates.
    live_candidates, batch_counts = _dedup_candidates(plate_rows)

    summary = Summary()

    # ALISTAMIENTO -> blank_preparations (feed inventory.prepared). category
    # is the inventory MATERIAL (3 values), NOT the granular plate TYPE (7
    # values) this row's plate_type actually holds -- must go through
    # normalize.inventory_category() to map between the two namespaces (see
    # docs/DATABASE.md); passing plate_type straight through was a bug (dead
    # code in practice, since real ALISTAMIENTO rows never reach here with a
    # mismatched value, but fixed while this block was already being
    # touched). ORNAMENTAL has no material (inventory_category ->None) and
    # genuinely can't be prepared as a blank -- raise instead of writing a
    # NULL into a NOT NULL column.
    for r in alistado_rows:
        category = inventory_category(r.plate_type)
        if category is None:
            raise ValueError(
                f"No se puede registrar alistamiento para el tipo '{r.plate_type}' "
                "(no tiene material de inventario asociado)."
            )
        db.insert_blank_preparation(
            conn,
            prep_date=r.order_date,
            category=category,
            quantity=r.quantity,
            prepared_by=r.prepared_by,
        )
        summary.blank_preparations += 1

    already_present = db.existing_plate_numbers(conn, live_candidates)

    # One current-tariff snapshot for the whole batch (Fase 2) -- applied
    # per row below as a fallback only for rows with no unit_price of their
    # own (see _snapshot_unit_price).
    prices = db.current_prices(conn)

    # Group plate rows by order_number, preserving order.
    groups: dict[str, list[ExtractedRow]] = defaultdict(list)
    for r in plate_rows:
        groups[r.order_number].append(r)

    for order_number, group in groups.items():
        head = group[0]
        customer_id = db.resolve_or_create_customer(conn, head.requested_by)
        order_id = db.insert_order(conn, row=head, customer_id=customer_id, created_by=created_by)
        summary.orders_inserted += 1

        summary.review_notes.extend(_order_level_mismatches(order_number, group))

        for r in group:
            is_dup = bool(
                is_real_plate(r.plate_number)
                and not r.is_voided
                and (r.plate_number in already_present or batch_counts[r.plate_number] > 1)
            )
            priced = _snapshot_unit_price(r, prices)
            db.insert_plate(conn, order_id=order_id, row=priced, is_possible_duplicate=is_dup)
            _count_row(summary, priced, is_dup)

    db.recalc_inventory(conn)

    return summary


def replace_order(
    conn: psycopg.Connection[DictRow], order_number: str, rows: list[ExtractedRow],
    created_by: int | None = None,
) -> Summary:
    """Fuse-replace every existing order under `order_number` with one fresh
    order built from `rows`.

    An explicit, opt-in action (see the /ingest "Reemplazar" button) for the
    case where a big historical reposición batch (one placeholder plates row,
    no plate identities -- see is_batch_replacement) gets its real plate
    numbers recovered later, e.g. by re-uploading the original pedido PDF.
    Simply re-running process_rows() on that PDF would silently create a
    SECOND production_orders row with the same order_number (there's no
    unique constraint on it, and the only duplicate check process_rows makes
    is on plate_number, which a reposición row never has) -- this is exactly
    what happened for real with WCY449 / pedido 1030 (see
    data/duplicados_revision_2026-08-15.md), fixed by hand at the time.

    Unlike process_rows (which groups an arbitrary batch by order_number and
    can insert several orders at once), this is deliberately scoped to ONE
    order_number: every row in `rows` must share it, or this raises --
    replacing doesn't make sense spread across multiple orders in one call.

    "Fuse" means every production_orders row that currently exists under
    order_number gets deleted (there can be more than one already -- that's
    the bug this exists to clean up), along with all of their plates, and
    exactly one new order is inserted in their place. There is no picking
    which existing row to keep: after this runs, order_number exists exactly
    once, full stop.

    Fase 2: the new order's plates take the tariff (plate_type_prices)
    VIGENTE AT THE MOMENT OF THE REPLACE, not whatever was in effect on the
    order's original date -- same insert-time snapshot rule as any other new
    plate (see _snapshot_unit_price). If prices changed since the original
    order, the replacement's prices will differ; this is documented
    behavior, not a bug. created_by likewise records who triggered THIS
    replace, not the original order's creator.

    Runs entirely through `conn`, which the caller owns: it neither commits
    nor rolls back, so `with db.get_conn() as conn: replace_order(conn, ...)`
    lands the whole replace atomically -- either everything below commits
    together on a clean exit, or nothing does.
    """
    if not rows:
        raise ValueError("replace_order needs at least one row.")
    if any(r.order_number != order_number for r in rows):
        raise ValueError(
            f"replace_order got rows for a different order_number than {order_number!r} -- "
            "replacing must stay within a single order."
        )

    normalized = [normalize_row(r) for r in rows]

    # Same within-batch duplicate check process_rows makes (only real plate
    # identifiers count -- samples/serials/INGSE repeat legitimately).
    live_candidates, batch_counts = _dedup_candidates(normalized)

    summary = Summary()

    # Fuse: delete every existing order under this order_number (and its
    # plates) before inserting the replacement -- regardless of whether
    # that's 0, 1, or 2+ rows (WCY449/pedido 1030 shows 2+ really happens).
    existing = db.find_orders_by_number(conn, order_number)
    for match in existing:
        db.delete_order(conn, match.id)
        summary.plates_deleted += match.n_plates
    summary.orders_replaced = len(existing)

    already_present = db.existing_plate_numbers(conn, live_candidates)
    prices = db.current_prices(conn)

    head = normalized[0]
    customer_id = db.resolve_or_create_customer(conn, head.requested_by)
    order_id = db.insert_order(conn, row=head, customer_id=customer_id, created_by=created_by)
    summary.orders_inserted = 1

    summary.review_notes.extend(_order_level_mismatches(order_number, normalized))

    for r in normalized:
        is_dup = bool(
            is_real_plate(r.plate_number)
            and not r.is_voided
            and (r.plate_number in already_present or batch_counts[r.plate_number] > 1)
        )
        priced = _snapshot_unit_price(r, prices)
        db.insert_plate(conn, order_id=order_id, row=priced, is_possible_duplicate=is_dup)
        _count_row(summary, priced, is_dup)

    db.recalc_inventory(conn)

    return summary
