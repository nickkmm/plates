"""Tests for app.core.process.process_rows -- the single ingestion backend.

Needs a real Postgres connection (see tests/conftest.py: `db_conn` truncates
all 4 tables first via `clean_db`, then hands back a connection for setup/
assertions). process_rows() itself always opens its OWN connection via
db.get_conn() -- there is no injection point -- so these tests verify
results by querying the DB afterwards rather than inspecting a passed-in
session.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core import db
from app.core.models import ExtractedRow
from app.core.process import find_duplicate_plate_numbers, process_rows, replace_order


def make_row(**overrides: object) -> ExtractedRow:
    defaults: dict[str, object] = {
        "order_number": "1",
        "order_date": date(2026, 1, 1),
        "requested_by": "PEDRO",
        "plate_type": "CARRO PARTICULAR",
    }
    defaults.update(overrides)
    return ExtractedRow(**defaults)


class TestNormalOrder:
    def test_inserts_order_and_plate(self, db_conn):
        rows = [
            make_row(
                order_number="100", requested_by="PEDRO",
                plate_type="CARRO PARTICULAR", plate_number="HMF922",
            )
        ]
        summary = process_rows(rows)

        assert summary.orders_inserted == 1
        assert summary.plates_inserted == 1
        assert summary.blank_preparations == 0
        assert summary.possible_duplicates == 0

        with db_conn.cursor() as cur:
            cur.execute("SELECT order_number, requested_by FROM production_orders")
            assert cur.fetchall() == [("100", "PEDRO")]

            cur.execute("SELECT plate_number, plate_type, quantity, is_possible_duplicate FROM plates")
            assert cur.fetchall() == [("HMF922", "CARRO PARTICULAR", 1, False)]

    def test_multiple_plates_same_order(self, db_conn):
        rows = [
            make_row(order_number="101", plate_number="AAA111", plate_type="CARRO PARTICULAR"),
            make_row(order_number="101", plate_number="AAA112", plate_type="CARRO PARTICULAR"),
        ]
        summary = process_rows(rows)
        assert summary.orders_inserted == 1  # one order_number -> one production_orders row
        assert summary.plates_inserted == 2

        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM production_orders")
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT count(*) FROM plates")
            assert cur.fetchone()[0] == 2

    def test_orders_inserted_counter_accumulates_across_distinct_orders(self, db_conn):
        rows = [
            make_row(order_number="102", plate_number="BBB111", plate_type="CARRO PARTICULAR"),
            make_row(order_number="103", plate_number="BBB112", plate_type="CARRO PARTICULAR"),
        ]
        summary = process_rows(rows)
        assert summary.orders_inserted == 2


class TestInventoryRecalc:
    """process_rows() always calls db.recalc_inventory() before committing;
    these check its actual effect on inventory_categories.consumed rather
    than just that it runs without error."""

    def test_consumed_reflects_inserted_plate_quantities(self, db_conn):
        rows = [
            make_row(order_number="900", plate_number="AAA100", plate_type="CARRO PARTICULAR", quantity=3),
            make_row(order_number="900", plate_number="AAA101", plate_type="CARRO PARTICULAR", quantity=5),
        ]
        process_rows(rows)

        with db_conn.cursor() as cur:
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            assert cur.fetchone() == (8,)

    def test_ornamental_consumes_no_material(self, db_conn):
        """ORNAMENTAL plates are a counter only -- they must not add to any
        inventory_categories.consumed row (inventory_category() -> None)."""
        rows = [make_row(order_number="901", plate_number="AAA200", plate_type="ORNAMENTAL", quantity=7)]
        process_rows(rows)

        with db_conn.cursor() as cur:
            cur.execute("SELECT COALESCE(SUM(consumed), 0) FROM inventory_categories")
            assert cur.fetchone() == (0,)

    def test_voided_order_not_counted_as_consumed(self, db_conn):
        rows = [
            make_row(order_number="902", plate_number="ANULADA ORDEN", plate_type="CARRO PARTICULAR",
                      quantity=9),
        ]
        process_rows(rows)

        with db_conn.cursor() as cur:
            cur.execute("SELECT COALESCE(consumed, 0) FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            row = cur.fetchone()
            assert (row[0] if row else 0) == 0


class TestAlistamiento:
    def test_requested_by_alistam_goes_to_blank_preparations_not_plates(self, db_conn):
        rows = [
            make_row(
                order_number="200", requested_by="ALISTAMIENTO", plate_type="MOTO PARTICULAR",
                quantity=500, prepared_by="GILMER",
            )
        ]
        summary = process_rows(rows)

        assert summary.blank_preparations == 1
        assert summary.plates_inserted == 0
        assert summary.orders_inserted == 0  # alistado rows never create an order

        with db_conn.cursor() as cur:
            cur.execute("SELECT category, quantity, prepared_by FROM blank_preparations")
            assert cur.fetchall() == [("MOTO PARTICULAR", 500, "GILMER")]
            cur.execute("SELECT count(*) FROM plates")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM production_orders")
            assert cur.fetchone()[0] == 0

    def test_prepared_by_alistam_also_detected(self, db_conn):
        """Belt-and-suspenders: the marker can be in ALISTADO POR (prepared_by)
        instead of SOLICITADO POR (requested_by)."""
        rows = [
            make_row(
                order_number="201", requested_by="PEDRO", plate_type="PÚBLICO",
                quantity=100, prepared_by="ALISTAMIENTO JUNIO",
            )
        ]
        summary = process_rows(rows)
        assert summary.blank_preparations == 1
        assert summary.plates_inserted == 0

    def test_blank_preparations_counter_accumulates_across_rows(self, db_conn):
        rows = [
            make_row(order_number="202", requested_by="ALISTAMIENTO", plate_type="MOTO PARTICULAR",
                      quantity=100),
            make_row(order_number="203", requested_by="ALISTAMIENTO", plate_type="CARRO PARTICULAR",
                      quantity=200),
        ]
        summary = process_rows(rows)
        assert summary.blank_preparations == 2


class TestDuplicateDetection:
    def test_plate_repeated_within_same_batch_flags_both_occurrences(self, db_conn):
        rows = [
            make_row(order_number="300", plate_number="HMF922", plate_type="CARRO PARTICULAR"),
            make_row(order_number="301", plate_number="HMF922", plate_type="CARRO PARTICULAR"),
        ]
        summary = process_rows(rows)
        # Both rows share the plate, so BOTH are flagged (not just the 2nd) --
        # batch_counts[...] > 1 applies uniformly, there's no "first seen" pass.
        assert summary.possible_duplicates == 2

        with db_conn.cursor() as cur:
            cur.execute("SELECT is_possible_duplicate FROM plates ORDER BY id")
            assert [r[0] for r in cur.fetchall()] == [True, True]

    def test_plate_already_in_db_flags_the_new_one_only(self, db_conn):
        first = [make_row(order_number="400", plate_number="ABC123", plate_type="CARRO PARTICULAR")]
        summary1 = process_rows(first)
        assert summary1.possible_duplicates == 0

        second = [make_row(order_number="401", plate_number="ABC123", plate_type="CARRO PARTICULAR")]
        summary2 = process_rows(second)
        assert summary2.possible_duplicates == 1

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT o.order_number, p.is_possible_duplicate FROM plates p "
                "JOIN production_orders o ON o.id = p.order_id ORDER BY p.id"
            )
            assert cur.fetchall() == [("400", False), ("401", True)]

    def test_sample_tokens_never_flagged_even_when_repeated(self, db_conn):
        rows = [
            make_row(order_number="500", plate_number="MUESTRA", plate_type="CARRO PARTICULAR"),
            make_row(order_number="500", plate_number="MUESTRA", plate_type="CARRO PARTICULAR"),
            make_row(order_number="500", plate_number="MUESTRA", plate_type="CARRO PARTICULAR"),
        ]
        summary = process_rows(rows)
        assert summary.possible_duplicates == 0

    def test_voided_row_does_not_count_toward_duplicate_flag(self, db_conn):
        """A pre-voided row sharing a plate_number with a live row must not
        make the live row look duplicated (and the voided row itself is
        never flagged either, since is_dup requires `not r.is_voided`)."""
        rows = [
            make_row(order_number="600", plate_number="DUP001", plate_type="CARRO PARTICULAR",
                      is_voided=True),
            make_row(order_number="601", plate_number="DUP001", plate_type="CARRO PARTICULAR"),
        ]
        summary = process_rows(rows)
        assert summary.possible_duplicates == 0


class TestFindDuplicatePlateNumbers:
    """Read-only pre-check used by /ingest to warn before saving -- must agree
    with process_rows' own is_possible_duplicate logic without writing anything."""

    def test_empty_when_nothing_conflicts(self, db_conn):
        rows = [make_row(order_number="900", plate_number="FRESH1", plate_type="CARRO PARTICULAR")]
        assert find_duplicate_plate_numbers(rows) == set()

        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM plates")
            assert cur.fetchone() == (0,)  # read-only: nothing got inserted

    def test_flags_plate_already_in_db(self, db_conn):
        process_rows([make_row(order_number="901", plate_number="OLD001", plate_type="CARRO PARTICULAR")])
        rows = [make_row(order_number="902", plate_number="OLD001", plate_type="CARRO PARTICULAR")]
        assert find_duplicate_plate_numbers(rows) == {"OLD001"}

    def test_flags_plate_repeated_within_the_same_batch(self, db_conn):
        rows = [
            make_row(order_number="903", plate_number="NEW777", plate_type="CARRO PARTICULAR"),
            make_row(order_number="904", plate_number="NEW777", plate_type="CARRO PARTICULAR"),
        ]
        assert find_duplicate_plate_numbers(rows) == {"NEW777"}

    def test_sample_tokens_never_flagged(self, db_conn):
        rows = [
            make_row(order_number="905", plate_number="MUESTRA", plate_type="CARRO PARTICULAR"),
            make_row(order_number="906", plate_number="MUESTRA", plate_type="CARRO PARTICULAR"),
        ]
        assert find_duplicate_plate_numbers(rows) == set()

    def test_voided_row_not_flagged_and_does_not_taint_a_live_match(self, db_conn):
        rows = [
            make_row(order_number="907", plate_number="VOID001", plate_type="CARRO PARTICULAR",
                      is_voided=True),
            make_row(order_number="908", plate_number="VOID001", plate_type="CARRO PARTICULAR"),
        ]
        assert find_duplicate_plate_numbers(rows) == set()


class TestSummaryCounters:
    def test_batch_replacement_counted(self, db_conn):
        rows = [make_row(order_number="800", plate_number="REPOSICION", plate_type="CARRO PARTICULAR")]
        summary = process_rows(rows)
        assert summary.batch_replacements == 1
        assert summary.plates_inserted == 1

    def test_batch_replacement_counter_accumulates_across_rows(self, db_conn):
        """Two REPOSICION rows in one call must add up to 2, not just latch
        to 1 -- distinguishes a real `+= 1` accumulator from a counter that
        gets reset/overwritten on every matching row."""
        rows = [
            make_row(order_number="810", plate_number="REPOSICION", plate_type="CARRO PARTICULAR"),
            make_row(order_number="811", plate_number="REPOSICION", plate_type="MOTO PARTICULAR"),
        ]
        summary = process_rows(rows)
        assert summary.batch_replacements == 2

    def test_plate_annulled_counted(self, db_conn):
        """is_plate_annulled is set upstream (e.g. by the web form's
        `placa_anulada` line type), never inferred by normalize_row -- set it
        directly here to exercise process_rows' own counting of it."""
        rows = [
            make_row(order_number="801", plate_number="HMF930", plate_type="CARRO PARTICULAR",
                      is_plate_annulled=True)
        ]
        summary = process_rows(rows)
        assert summary.plate_annulled == 1

    def test_plate_annulled_counter_accumulates_across_rows(self, db_conn):
        rows = [
            make_row(order_number="803", plate_number="HMF931", plate_type="CARRO PARTICULAR",
                      is_plate_annulled=True),
            make_row(order_number="804", plate_number="HMF932", plate_type="CARRO PARTICULAR",
                      is_plate_annulled=True),
        ]
        summary = process_rows(rows)
        assert summary.plate_annulled == 2

    def test_voided_counted(self, db_conn):
        rows = [make_row(order_number="802", plate_number="ANULADA ORDEN", plate_type="CARRO PARTICULAR")]
        summary = process_rows(rows)
        assert summary.voided == 1

    def test_voided_counter_accumulates_across_rows(self, db_conn):
        rows = [
            make_row(order_number="805", plate_number="ANULADA ORDEN", plate_type="CARRO PARTICULAR"),
            make_row(order_number="806", plate_number="ANULADA ORDEN", plate_type="MOTO PARTICULAR"),
        ]
        summary = process_rows(rows)
        assert summary.voided == 2


class TestReviewNotes:
    def test_multiple_requested_by_in_one_order_flags_review_note(self, db_conn):
        rows = [
            make_row(order_number="700", requested_by="PEDRO", plate_number="ZZZ111",
                      plate_type="CARRO PARTICULAR"),
            make_row(order_number="700", requested_by="JUAN", plate_number="ZZZ112",
                      plate_type="CARRO PARTICULAR"),
        ]
        summary = process_rows(rows)
        assert len(summary.review_notes) == 1
        assert "700" in summary.review_notes[0]
        assert summary.orders_inserted == 1  # still a single order row
        assert summary.plates_inserted == 2

    def test_same_requested_by_in_one_order_no_review_note(self, db_conn):
        rows = [
            make_row(order_number="701", requested_by="PEDRO", plate_number="YYY111",
                      plate_type="CARRO PARTICULAR"),
            make_row(order_number="701", requested_by="PEDRO", plate_number="YYY112",
                      plate_type="CARRO PARTICULAR"),
        ]
        summary = process_rows(rows)
        assert summary.review_notes == []


class TestReplaceOrder:
    """replace_order() fuses every existing production_orders row sharing an
    order_number into exactly one new one -- the opt-in fix for a reposición
    placeholder order later getting its real plates recovered from a
    re-uploaded PDF (see the WCY449 / pedido 1030 case in
    data/duplicados_revision_2026-08-15.md)."""

    def test_rejects_rows_spanning_more_than_one_order_number(self, db_conn):
        rows = [
            make_row(order_number="1000", plate_number="AAA111", plate_type="CARRO PARTICULAR"),
            make_row(order_number="1001", plate_number="AAA112", plate_type="CARRO PARTICULAR"),
        ]
        with pytest.raises(ValueError, match="order_number"):
            replace_order("1000", rows)

    def test_rejects_an_empty_row_list(self, db_conn):
        with pytest.raises(ValueError):
            replace_order("1000", [])

    def test_replaces_a_single_existing_order_leaving_only_the_new_one(self, db_conn):
        old_id = db.insert_order(db_conn, order_number="1010", order_date=date(2026, 1, 1),
                                  requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=old_id,
                         row=make_row(order_number="1010", plate_type="CARRO PARTICULAR",
                                      is_batch_replacement=True, quantity=50),
                         is_possible_duplicate=False)
        db_conn.commit()

        new_rows = [
            make_row(order_number="1010", order_date=date(2026, 1, 5), requested_by="MARIA",
                      plate_type="CARRO PARTICULAR", plate_number="WCY449"),
        ]
        summary = replace_order("1010", new_rows)

        assert summary.orders_replaced == 1
        assert summary.plates_deleted == 1
        assert summary.orders_inserted == 1
        assert summary.plates_inserted == 1

        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '1010'")
            assert cur.fetchone()[0] == 1  # the old placeholder order is gone, replaced by one new one
            cur.execute(
                "SELECT o.id, o.requested_by, p.plate_number, p.is_batch_replacement "
                "FROM production_orders o JOIN plates p ON p.order_id = o.id "
                "WHERE o.order_number = '1010'"
            )
            rows = cur.fetchall()
            assert len(rows) == 1
            new_id, requested_by, plate_number, is_batch = rows[0]
            assert new_id != old_id
            assert (requested_by, plate_number, is_batch) == ("MARIA", "WCY449", False)

    def test_fuses_two_or_more_existing_orders_sharing_the_number_into_one(self, db_conn):
        """The real bug this exists for: order_number has no unique constraint,
        so two production_orders rows already sharing one can exist. Replacing
        must delete ALL of them (and ALL their plates), not just one."""
        first_id = db.insert_order(db_conn, order_number="1020", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=first_id,
                         row=make_row(order_number="1020", plate_type="CARRO PARTICULAR",
                                      is_batch_replacement=True, quantity=30),
                         is_possible_duplicate=False)
        second_id = db.insert_order(db_conn, order_number="1020", order_date=date(2026, 1, 10),
                                     requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=second_id,
                         row=make_row(order_number="1020", plate_type="CARRO PARTICULAR",
                                      plate_number="OLD999"),
                         is_possible_duplicate=False)
        db_conn.commit()

        new_rows = [
            make_row(order_number="1020", plate_type="CARRO PARTICULAR", plate_number="NEW100"),
            make_row(order_number="1020", plate_type="CARRO PARTICULAR", plate_number="NEW101"),
        ]
        summary = replace_order("1020", new_rows)

        assert summary.orders_replaced == 2
        assert summary.plates_deleted == 2
        assert summary.orders_inserted == 1
        assert summary.plates_inserted == 2

        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE order_number = '1020'")
            assert cur.fetchone()[0] == 1
            cur.execute(
                "SELECT p.plate_number FROM plates p JOIN production_orders o ON o.id = p.order_id "
                "WHERE o.order_number = '1020' ORDER BY p.plate_number"
            )
            assert [r[0] for r in cur.fetchall()] == ["NEW100", "NEW101"]
            # both old plates (from BOTH fused orders) are gone
            cur.execute("SELECT count(*) FROM plates WHERE plate_number IN ('OLD999')")
            assert cur.fetchone()[0] == 0

    def test_replacing_a_number_with_no_existing_order_just_inserts(self, db_conn):
        rows = [make_row(order_number="1030", plate_type="CARRO PARTICULAR", plate_number="FRESH1")]
        summary = replace_order("1030", rows)
        assert summary.orders_replaced == 0
        assert summary.plates_deleted == 0
        assert summary.orders_inserted == 1
        assert summary.plates_inserted == 1

    def test_inventory_is_recalculated_without_double_counting(self, db_conn):
        """The exact scenario the whole feature exists to fix: the old
        reposición row's quantity must NOT still be counted alongside the new
        rows' quantities after the replace."""
        db.insert_blank_preparation(db_conn, prep_date=date(2026, 1, 1), category="CARRO PARTICULAR",
                                     quantity=100, prepared_by=None)
        old_id = db.insert_order(db_conn, order_number="1040", order_date=date(2026, 1, 1),
                                  requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=old_id,
                         row=make_row(order_number="1040", plate_type="CARRO PARTICULAR",
                                      is_batch_replacement=True, quantity=40),
                         is_possible_duplicate=False)
        db.recalc_inventory(db_conn)
        db_conn.commit()
        with db_conn.cursor() as cur:
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            assert cur.fetchone() == (40,)  # sanity check before replacing

        new_rows = [
            make_row(order_number="1040", plate_type="CARRO PARTICULAR", plate_number="INV100", quantity=20),
            make_row(order_number="1040", plate_type="CARRO PARTICULAR", plate_number="INV101", quantity=20),
        ]
        replace_order("1040", new_rows)

        with db_conn.cursor() as cur:
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            # 40 total across the two new rows -- NOT 80 (40 old + 40 new)
            assert cur.fetchone() == (40,)

    def test_is_possible_duplicate_set_exactly_as_process_rows_would(self, db_conn):
        """A plate number that's still live elsewhere in the DB (NOT part of
        the order being replaced) must still get flagged on the new row --
        replacing one order must not blind the normal cross-order dup check."""
        process_rows([make_row(order_number="1050", plate_type="CARRO PARTICULAR", plate_number="LIVE001")])

        old_id = db.insert_order(db_conn, order_number="1051", order_date=date(2026, 1, 1),
                                  requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=old_id,
                         row=make_row(order_number="1051", plate_type="CARRO PARTICULAR",
                                      is_batch_replacement=True, quantity=1),
                         is_possible_duplicate=False)
        db_conn.commit()

        new_rows = [
            make_row(order_number="1051", plate_type="CARRO PARTICULAR", plate_number="LIVE001"),
            make_row(order_number="1051", plate_type="CARRO PARTICULAR", plate_number="FRESH2"),
        ]
        summary = replace_order("1051", new_rows)
        assert summary.possible_duplicates == 1

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT p.plate_number, p.is_possible_duplicate FROM plates p "
                "JOIN production_orders o ON o.id = p.order_id "
                "WHERE o.order_number = '1051' ORDER BY p.plate_number"
            )
            assert cur.fetchall() == [("FRESH2", False), ("LIVE001", True)]

    def test_within_batch_repeat_is_flagged_on_both_new_rows(self, db_conn):
        old_id = db.insert_order(db_conn, order_number="1060", order_date=date(2026, 1, 1),
                                  requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=old_id,
                         row=make_row(order_number="1060", plate_type="CARRO PARTICULAR",
                                      is_batch_replacement=True, quantity=1),
                         is_possible_duplicate=False)
        db_conn.commit()

        new_rows = [
            make_row(order_number="1060", plate_type="CARRO PARTICULAR", plate_number="REPT01"),
            make_row(order_number="1060", plate_type="CARRO PARTICULAR", plate_number="REPT01"),
        ]
        summary = replace_order("1060", new_rows)
        assert summary.possible_duplicates == 2
