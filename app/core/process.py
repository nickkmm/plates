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


def _is_alistado(row: ExtractedRow) -> bool:
    """ALISTAMIENTO rows record blanks prepared, not plates produced.

    In the data the marker is in SOLICITADO POR (requested_by); a few rows also
    carry it in ALISTADO POR (prepared_by). We check both so it's never missed.
    """
    return "ALISTAM" in row.requested_by.upper() or "ALISTAM" in (row.prepared_by or "").upper()


def find_duplicate_plate_numbers(rows: list[ExtractedRow]) -> set[str]:
    """Read-only: which plate_numbers in this batch would be flagged
    is_possible_duplicate if inserted right now (already in the DB, or
    repeated within the batch itself). Mirrors process_rows' own detection
    logic without writing anything — used by the /ingest form to warn
    before saving, not just after.
    """
    normalized = [normalize_row(r) for r in rows]
    plate_rows = [r for r in normalized if not _is_alistado(r)]
    live_candidates = {
        r.plate_number for r in plate_rows if is_real_plate(r.plate_number) and not r.is_voided
    }
    batch_counts = Counter(
        r.plate_number for r in plate_rows if is_real_plate(r.plate_number) and not r.is_voided
    )
    with db.get_conn() as conn:
        already_present = db.existing_plate_numbers(conn, live_candidates)
    return {pn for pn in live_candidates if pn in already_present or batch_counts[pn] > 1}


def process_rows(rows: list[ExtractedRow]) -> Summary:
    """Normalize, dedup, insert, and recalc inventory for a batch of rows.

    Runs in a single transaction: either the whole batch lands or none of it.
    """
    normalized = [normalize_row(r) for r in rows]

    # Split ALISTAMIENTO (blanks prepared) from plate rows.
    alistado_rows = [r for r in normalized if _is_alistado(r)]
    plate_rows = [r for r in normalized if not _is_alistado(r)]

    # Plate numbers appearing more than once within this same batch are also
    # suspicious, not just ones already in the DB. Only real plate identifiers
    # count — samples/serials/INGSE repeat legitimately and are not duplicates.
    batch_counts = Counter(
        r.plate_number for r in plate_rows if is_real_plate(r.plate_number) and not r.is_voided
    )

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

        live_candidates = {
            r.plate_number for r in plate_rows if is_real_plate(r.plate_number) and not r.is_voided
        }
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
                city=head.city,
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
                summary.plates_inserted += 1
                if is_dup:
                    summary.possible_duplicates += 1
                if r.is_voided:
                    summary.voided += 1
                if r.is_plate_annulled:
                    summary.plate_annulled += 1
                if r.is_batch_replacement:
                    summary.batch_replacements += 1

        db.recalc_inventory(conn)
        conn.commit()

    return summary
