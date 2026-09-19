"""The single ingestion backend: process_rows().

Every door (paste-JSON, image->API, Excel migration) converges here. Given a
list of ExtractedRow, this normalizes them, detects duplicates, groups by
order_number, inserts production_orders + plates in one transaction, recomputes
inventory_categories, and returns a Summary.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from pydantic import BaseModel

from app.core import db
from app.core.models import ExtractedRow
from app.core.normalize import is_real_plate, normalize_row


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


def find_duplicate_plate_numbers(rows: list[ExtractedRow]) -> set[str]:
    """Read-only: which plate_numbers in this batch would be flagged
    is_possible_duplicate if inserted right now (already in the DB, or
    repeated within the batch itself). Mirrors process_rows' own detection
    logic without writing anything — used by the /ingest form to warn
    before saving, not just after.
    """
    normalized = [normalize_row(r) for r in rows]
    plate_rows = [r for r in normalized if not is_alistado(r)]
    live_candidates, batch_counts = _dedup_candidates(plate_rows)
    with db.get_conn() as conn:
        already_present = db.existing_plate_numbers(conn, live_candidates)
    return {pn for pn in live_candidates if pn in already_present or batch_counts[pn] > 1}


def process_rows(rows: list[ExtractedRow]) -> Summary:
    """Normalize, dedup, insert, and recalc inventory for a batch of rows.

    Runs in a single transaction: either the whole batch lands or none of it.
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

    with db.get_conn() as conn:
        # ALISTAMIENTO -> blank_preparations (feed inventory.prepared)
        for r in alistado_rows:
            db.insert_blank_preparation(
                conn,
                prep_date=r.order_date,
                category=r.plate_type,
                quantity=r.quantity,
                prepared_by=r.prepared_by,
            )
            summary.blank_preparations += 1

        already_present = db.existing_plate_numbers(conn, live_candidates)

        # Group plate rows by order_number, preserving order.
        groups: dict[str, list[ExtractedRow]] = defaultdict(list)
        for r in plate_rows:
            groups[r.order_number].append(r)

        for order_number, group in groups.items():
            head = group[0]
            order_id = db.insert_order(
                conn,
                order_number=order_number,
                order_date=head.order_date,
                requested_by=head.requested_by,
            )
            summary.orders_inserted += 1

            # Flag if order-level fields disagree within the group (data smell).
            if len({r.requested_by for r in group}) > 1:
                summary.review_notes.append(
                    f"Order {order_number}: multiple requested_by values in the same order."
                )

            for r in group:
                is_dup = bool(
                    is_real_plate(r.plate_number)
                    and not r.is_voided
                    and (r.plate_number in already_present or batch_counts[r.plate_number] > 1)
                )
                db.insert_plate(conn, order_id=order_id, row=r, is_possible_duplicate=is_dup)
                _count_row(summary, r, is_dup)

        db.recalc_inventory(conn)
        conn.commit()

    return summary


def replace_order(order_number: str, rows: list[ExtractedRow]) -> Summary:
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

    Runs in a single transaction: either the whole replace lands or none of it.
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

    with db.get_conn() as conn:
        # Fuse: delete every existing order under this order_number (and its
        # plates) before inserting the replacement -- regardless of whether
        # that's 0, 1, or 2+ rows (WCY449/pedido 1030 shows 2+ really happens).
        existing = db.find_orders_by_number(conn, order_number)
        for match in existing:
            db.delete_order(conn, match.id)
            summary.plates_deleted += match.n_plates
        summary.orders_replaced = len(existing)

        already_present = db.existing_plate_numbers(conn, live_candidates)

        head = normalized[0]
        order_id = db.insert_order(
            conn,
            order_number=order_number,
            order_date=head.order_date,
            requested_by=head.requested_by,
        )
        summary.orders_inserted = 1

        for r in normalized:
            is_dup = bool(
                is_real_plate(r.plate_number)
                and not r.is_voided
                and (r.plate_number in already_present or batch_counts[r.plate_number] > 1)
            )
            db.insert_plate(conn, order_id=order_id, row=r, is_possible_duplicate=is_dup)
            _count_row(summary, r, is_dup)

        db.recalc_inventory(conn)
        conn.commit()

    return summary
