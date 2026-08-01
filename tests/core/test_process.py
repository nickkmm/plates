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

from app.core.models import ExtractedRow
from app.core.process import process_rows


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
