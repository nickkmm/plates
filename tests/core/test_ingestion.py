"""Tests for app.core.ingestion.process_rows -- the single ingestion backend.

Needs a real Postgres connection (see tests/conftest.py: `db_conn` truncates
all 4 tables first via `clean_db`, then hands back a connection for setup/
assertions). Since Fase 1d, process_rows/replace_order/find_duplicate_
plate_numbers all take that same `db_conn` as their first argument (they no
longer open their own connection internally -- see app/core/ingestion.py's
module docstring), so these tests both drive the call under test AND read
back its effects through the identical session/transaction.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from psycopg.rows import tuple_row

from app.core import db
from app.core.ingestion import find_duplicate_plate_numbers, process_rows, replace_order
from app.core.models import ExtractedRow


def make_row(**overrides: object) -> ExtractedRow:
    defaults: dict[str, object] = {
        "order_number": "1",
        "order_date": date(2026, 1, 1),
        "requested_by": "PEDRO",
        "plate_type": "CARRO PARTICULAR",
    }
    defaults.update(overrides)
    return ExtractedRow(**defaults)


def _insert_order(conn, *, order_number: str, order_date, requested_by: str) -> int:
    """Back-compat shim for this file's pre-existing "seed an order to
    replace/fuse against" setup calls -- see the identical helper (and its
    full rationale) in tests/core/db/_helpers.py."""
    return db.insert_order(
        conn,
        row=ExtractedRow(order_number=order_number, order_date=order_date, requested_by=requested_by,
                          plate_type="MOTO PARTICULAR"),
        customer_id=None,
        created_by=None,
    )


class TestNormalOrder:
    def test_inserts_order_and_plate(self, db_conn):
        rows = [
            make_row(
                order_number="100", requested_by="PEDRO",
                plate_type="CARRO PARTICULAR", plate_number="HMF922",
            )
        ]
        summary = process_rows(db_conn, rows)

        assert summary.orders_inserted == 1
        assert summary.plates_inserted == 1
        assert summary.blank_preparations == 0
        assert summary.possible_duplicates == 0

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT order_number, requested_by FROM production_orders")
            assert cur.fetchall() == [("100", "PEDRO")]

            cur.execute("SELECT plate_number, plate_type, quantity, is_possible_duplicate FROM plates")
            assert cur.fetchall() == [("HMF922", "CARRO PARTICULAR", 1, False)]

    def test_multiple_plates_same_order(self, db_conn):
        rows = [
            make_row(order_number="101", plate_number="AAA111", plate_type="CARRO PARTICULAR"),
            make_row(order_number="101", plate_number="AAA112", plate_type="CARRO PARTICULAR"),
        ]
        summary = process_rows(db_conn, rows)
        assert summary.orders_inserted == 1  # one order_number -> one production_orders row
        assert summary.plates_inserted == 2

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM production_orders")
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT count(*) FROM plates")
            assert cur.fetchone()[0] == 2

    def test_orders_inserted_counter_accumulates_across_distinct_orders(self, db_conn):
        rows = [
            make_row(order_number="102", plate_number="BBB111", plate_type="CARRO PARTICULAR"),
            make_row(order_number="103", plate_number="BBB112", plate_type="CARRO PARTICULAR"),
        ]
        summary = process_rows(db_conn, rows)
        assert summary.orders_inserted == 2


class TestCustomerResolution:
    """process_rows/replace_order resolve/create a customer per order group,
    keyed off the group's head row's requested_by (the same field/value
    already used for order_date) -- see db.resolve_or_create_customer."""

    def test_new_order_resolves_and_links_a_customer(self, db_conn):
        rows = [make_row(order_number="110", requested_by="PTO.V", plate_number="CUS100")]
        process_rows(db_conn, rows)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT c.name FROM production_orders o JOIN customers c ON c.id = o.customer_id "
                "WHERE o.order_number = '110'"
            )
            assert cur.fetchone() == ("PTO.V",)

    def test_same_requested_by_across_orders_reuses_the_same_customer(self, db_conn):
        rows = [
            make_row(order_number="111", requested_by="MUVA", plate_number="CUS101"),
            make_row(order_number="112", requested_by="MUVA", plate_number="CUS102"),
        ]
        process_rows(db_conn, rows)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT DISTINCT customer_id FROM production_orders WHERE order_number IN ('111', '112')")
            assert len(cur.fetchall()) == 1
            cur.execute("SELECT count(*) FROM customers WHERE name = 'MUVA'")
            assert cur.fetchone()[0] == 1

    def test_spelling_variant_resolves_to_an_existing_customer(self, db_conn):
        """PTOV is a curated spelling variant of PTO.V (normalize._REQUESTED_BY_MAP_RAW)."""
        first = process_rows(
            db_conn, [make_row(order_number="113", requested_by="PTOV", plate_number="CUS103")]
        )
        assert first.orders_inserted == 1
        process_rows(db_conn, [make_row(order_number="114", requested_by="PTO.V", plate_number="CUS104")])

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM customers WHERE name = 'PTO.V'")
            assert cur.fetchone()[0] == 1


class TestCreatedBy:
    def test_defaults_to_null_when_omitted(self, db_conn):
        process_rows(db_conn, [make_row(order_number="120", plate_number="CRE100")])
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT created_by FROM production_orders WHERE order_number = '120'")
            assert cur.fetchone() == (None,)

    def test_is_stored_when_given(self, db_conn):
        user_id = db.create_user(db_conn, username="alice", password_hash="h", role="admin")
        process_rows(db_conn, [make_row(order_number="121", plate_number="CRE101")], created_by=user_id)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT created_by FROM production_orders WHERE order_number = '121'")
            assert cur.fetchone() == (user_id,)


class TestUnitPriceSnapshot:
    """Case 1 of the re-snapshot rule (see db/plates.py for cases 2/3, which
    apply on edit): a brand-new plate with no unit_price of its own takes
    the CURRENT plate_type_prices tariff at insert time."""

    def test_row_with_no_unit_price_gets_the_current_tariff(self, db_conn):
        db.set_price(db_conn, "CARRO PARTICULAR", Decimal("150000"), "admin")
        process_rows(
            db_conn, [make_row(order_number="130", plate_number="PRC100", plate_type="CARRO PARTICULAR")]
        )
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT unit_price FROM plates WHERE plate_number = 'PRC100'")
            assert cur.fetchone() == (Decimal("150000"),)

    def test_row_with_an_explicit_unit_price_is_kept_verbatim(self, db_conn):
        """A negotiated/edited price on the row must NOT be overridden by the
        suggested tariff, even if one is configured."""
        db.set_price(db_conn, "CARRO PARTICULAR", Decimal("150000"), "admin")
        process_rows(db_conn, [
            make_row(order_number="131", plate_number="PRC101", plate_type="CARRO PARTICULAR",
                      unit_price=Decimal("120000")),
        ])
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT unit_price FROM plates WHERE plate_number = 'PRC101'")
            assert cur.fetchone() == (Decimal("120000"),)

    def test_no_tariff_configured_leaves_unit_price_null(self, db_conn):
        process_rows(
            db_conn, [make_row(order_number="132", plate_number="PRC102", plate_type="ORNAMENTAL")]
        )
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT unit_price FROM plates WHERE plate_number = 'PRC102'")
            assert cur.fetchone() == (None,)

    def test_price_snapshot_is_per_plate_type(self, db_conn):
        db.set_price(db_conn, "CARRO PARTICULAR", Decimal("150000"), "admin")
        db.set_price(db_conn, "MOTO PARTICULAR", Decimal("90000"), "admin")
        process_rows(db_conn, [
            make_row(order_number="133", plate_number="PRC103", plate_type="CARRO PARTICULAR"),
            make_row(order_number="133", plate_number="PRC104", plate_type="MOTO PARTICULAR"),
        ])
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT plate_number, unit_price FROM plates ORDER BY plate_number")
            assert cur.fetchall() == [("PRC103", Decimal("150000")), ("PRC104", Decimal("90000"))]


class TestInventoryRecalc:
    """process_rows() always calls db.recalc_inventory() before committing;
    these check its actual effect on inventory_categories.consumed rather
    than just that it runs without error."""

    def test_consumed_reflects_inserted_plate_quantities(self, db_conn):
        rows = [
            make_row(order_number="900", plate_number="AAA100", plate_type="CARRO PARTICULAR", quantity=3),
            make_row(order_number="900", plate_number="AAA101", plate_type="CARRO PARTICULAR", quantity=5),
        ]
        process_rows(db_conn, rows)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            assert cur.fetchone() == (8,)

    def test_ornamental_consumes_no_material(self, db_conn):
        """ORNAMENTAL plates are a counter only -- they must not add to any
        inventory_categories.consumed row (inventory_category() -> None)."""
        rows = [make_row(order_number="901", plate_number="AAA200", plate_type="ORNAMENTAL", quantity=7)]
        process_rows(db_conn, rows)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT COALESCE(SUM(consumed), 0) FROM inventory_categories")
            assert cur.fetchone() == (0,)

    def test_voided_order_not_counted_as_consumed(self, db_conn):
        rows = [
            make_row(order_number="902", plate_number="ANULADA ORDEN", plate_type="CARRO PARTICULAR",
                      quantity=9),
        ]
        process_rows(db_conn, rows)

        with db_conn.cursor(row_factory=tuple_row) as cur:
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
        summary = process_rows(db_conn, rows)

        assert summary.blank_preparations == 1
        assert summary.plates_inserted == 0
        assert summary.orders_inserted == 0  # alistado rows never create an order

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT category, quantity, prepared_by FROM blank_preparations")
            assert cur.fetchall() == [("MOTO PARTICULAR", 500, "GILMER")]
            cur.execute("SELECT count(*) FROM plates")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM production_orders")
            assert cur.fetchone()[0] == 0

    def test_prepared_by_alistam_also_detected(self, db_conn):
        """Belt-and-suspenders: the marker can be in ALISTADO POR (prepared_by)
        instead of SOLICITADO POR (requested_by).

        plate_type is deliberately "CARRO PARTICULAR", not "PÚBLICO" --
        normalize_plate_type() expands a bare "PÚBLICO" into "CARRO PÚBLICO"
        (the 7-value plate TYPE namespace), which is not one of the 3
        inventory_categories.category materials this ends up written to
        (see docs/DATABASE.md rarezas on the two namespaces) and would trip
        the Fase 1b blank_preparations FK -- irrelevant to what this test
        actually verifies (the prepared_by marker detection)."""
        rows = [
            make_row(
                order_number="201", requested_by="PEDRO", plate_type="CARRO PARTICULAR",
                quantity=100, prepared_by="ALISTAMIENTO JUNIO",
            )
        ]
        summary = process_rows(db_conn, rows)
        assert summary.blank_preparations == 1
        assert summary.plates_inserted == 0

    def test_blank_preparations_counter_accumulates_across_rows(self, db_conn):
        rows = [
            make_row(order_number="202", requested_by="ALISTAMIENTO", plate_type="MOTO PARTICULAR",
                      quantity=100),
            make_row(order_number="203", requested_by="ALISTAMIENTO", plate_type="CARRO PARTICULAR",
                      quantity=200),
        ]
        summary = process_rows(db_conn, rows)
        assert summary.blank_preparations == 2

    def test_granular_publico_type_maps_to_the_publico_inventory_material(self, db_conn):
        """Bug fix: this branch used to write row.plate_type straight into
        `category` instead of mapping it through normalize.inventory_category()
        -- a granular TYPE like 'CARRO PÚBLICO' is not itself a valid
        inventory_categories.category (only 'PÚBLICO' is), so this used to be
        able to trip the Fase 1b blank_preparations FK (or write the wrong
        value) instead of correctly landing on the shared 'PÚBLICO' material."""
        rows = [
            make_row(order_number="204", requested_by="ALISTAMIENTO", plate_type="CARRO PÚBLICO",
                      quantity=50),
        ]
        summary = process_rows(db_conn, rows)
        assert summary.blank_preparations == 1
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT category, quantity FROM blank_preparations")
            assert cur.fetchall() == [("PÚBLICO", 50)]

    def test_ornamental_type_raises_instead_of_writing_a_null_category(self, db_conn):
        """ORNAMENTAL has no inventory material (inventory_category ->
        None) and genuinely can't be prepared as a blank -- this must raise
        a clear error rather than attempt a NULL insert into a NOT NULL
        column."""
        rows = [make_row(order_number="205", requested_by="ALISTAMIENTO", plate_type="ORNAMENTAL",
                          quantity=1)]
        with pytest.raises(ValueError, match="alistamiento"):
            process_rows(db_conn, rows)


class TestDuplicateDetection:
    def test_plate_repeated_within_same_batch_flags_both_occurrences(self, db_conn):
        rows = [
            make_row(order_number="300", plate_number="HMF922", plate_type="CARRO PARTICULAR"),
            make_row(order_number="301", plate_number="HMF922", plate_type="CARRO PARTICULAR"),
        ]
        summary = process_rows(db_conn, rows)
        # Both rows share the plate, so BOTH are flagged (not just the 2nd) --
        # batch_counts[...] > 1 applies uniformly, there's no "first seen" pass.
        assert summary.possible_duplicates == 2

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT is_possible_duplicate FROM plates ORDER BY id")
            assert [r[0] for r in cur.fetchall()] == [True, True]

    def test_plate_already_in_db_flags_the_new_one_only(self, db_conn):
        first = [make_row(order_number="400", plate_number="ABC123", plate_type="CARRO PARTICULAR")]
        summary1 = process_rows(db_conn, first)
        assert summary1.possible_duplicates == 0

        second = [make_row(order_number="401", plate_number="ABC123", plate_type="CARRO PARTICULAR")]
        summary2 = process_rows(db_conn, second)
        assert summary2.possible_duplicates == 1

        with db_conn.cursor(row_factory=tuple_row) as cur:
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
        summary = process_rows(db_conn, rows)
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
        summary = process_rows(db_conn, rows)
        assert summary.possible_duplicates == 0


class TestFindDuplicatePlateNumbers:
    """Read-only pre-check used by /ingest to warn before saving -- must agree
    with process_rows' own is_possible_duplicate logic without writing anything."""

    def test_empty_when_nothing_conflicts(self, db_conn):
        rows = [make_row(order_number="900", plate_number="FRESH1", plate_type="CARRO PARTICULAR")]
        assert find_duplicate_plate_numbers(db_conn, rows) == set()

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM plates")
            assert cur.fetchone() == (0,)  # read-only: nothing got inserted

    def test_flags_plate_already_in_db(self, db_conn):
        process_rows(
            db_conn, [make_row(order_number="901", plate_number="OLD001", plate_type="CARRO PARTICULAR")]
        )
        rows = [make_row(order_number="902", plate_number="OLD001", plate_type="CARRO PARTICULAR")]
        assert find_duplicate_plate_numbers(db_conn, rows) == {"OLD001"}

    def test_flags_plate_repeated_within_the_same_batch(self, db_conn):
        rows = [
            make_row(order_number="903", plate_number="NEW777", plate_type="CARRO PARTICULAR"),
            make_row(order_number="904", plate_number="NEW777", plate_type="CARRO PARTICULAR"),
        ]
        assert find_duplicate_plate_numbers(db_conn, rows) == {"NEW777"}

    def test_sample_tokens_never_flagged(self, db_conn):
        rows = [
            make_row(order_number="905", plate_number="MUESTRA", plate_type="CARRO PARTICULAR"),
            make_row(order_number="906", plate_number="MUESTRA", plate_type="CARRO PARTICULAR"),
        ]
        assert find_duplicate_plate_numbers(db_conn, rows) == set()

    def test_voided_row_not_flagged_and_does_not_taint_a_live_match(self, db_conn):
        rows = [
            make_row(order_number="907", plate_number="VOID001", plate_type="CARRO PARTICULAR",
                      is_voided=True),
            make_row(order_number="908", plate_number="VOID001", plate_type="CARRO PARTICULAR"),
        ]
        assert find_duplicate_plate_numbers(db_conn, rows) == set()


class TestSummaryCounters:
    def test_batch_replacement_counted(self, db_conn):
        rows = [make_row(order_number="800", plate_number="REPOSICION", plate_type="CARRO PARTICULAR")]
        summary = process_rows(db_conn, rows)
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
        summary = process_rows(db_conn, rows)
        assert summary.batch_replacements == 2

    def test_plate_annulled_counted(self, db_conn):
        """is_plate_annulled is set upstream (e.g. by the web form's
        `placa_anulada` line type), never inferred by normalize_row -- set it
        directly here to exercise process_rows' own counting of it."""
        rows = [
            make_row(order_number="801", plate_number="HMF930", plate_type="CARRO PARTICULAR",
                      is_plate_annulled=True)
        ]
        summary = process_rows(db_conn, rows)
        assert summary.plate_annulled == 1

    def test_plate_annulled_counter_accumulates_across_rows(self, db_conn):
        rows = [
            make_row(order_number="803", plate_number="HMF931", plate_type="CARRO PARTICULAR",
                      is_plate_annulled=True),
            make_row(order_number="804", plate_number="HMF932", plate_type="CARRO PARTICULAR",
                      is_plate_annulled=True),
        ]
        summary = process_rows(db_conn, rows)
        assert summary.plate_annulled == 2

    def test_voided_counted(self, db_conn):
        rows = [make_row(order_number="802", plate_number="ANULADA ORDEN", plate_type="CARRO PARTICULAR")]
        summary = process_rows(db_conn, rows)
        assert summary.voided == 1

    def test_voided_counter_accumulates_across_rows(self, db_conn):
        rows = [
            make_row(order_number="805", plate_number="ANULADA ORDEN", plate_type="CARRO PARTICULAR"),
            make_row(order_number="806", plate_number="ANULADA ORDEN", plate_type="MOTO PARTICULAR"),
        ]
        summary = process_rows(db_conn, rows)
        assert summary.voided == 2


class TestReviewNotes:
    def test_multiple_requested_by_in_one_order_flags_review_note(self, db_conn):
        rows = [
            make_row(order_number="700", requested_by="PEDRO", plate_number="ZZZ111",
                      plate_type="CARRO PARTICULAR"),
            make_row(order_number="700", requested_by="JUAN", plate_number="ZZZ112",
                      plate_type="CARRO PARTICULAR"),
        ]
        summary = process_rows(db_conn, rows)
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
        summary = process_rows(db_conn, rows)
        assert summary.review_notes == []

    def test_generalizes_to_every_order_level_field_not_just_requested_by(self, db_conn):
        """The check used to only exist for requested_by -- now every field in
        ingestion._ORDER_LEVEL_FIELDS gets its own mismatch check."""
        rows = [
            make_row(order_number="702", plate_number="MISM01", payment_method="EFECTIVO"),
            make_row(order_number="702", plate_number="MISM02", payment_method="TRANSFERENCIA"),
        ]
        summary = process_rows(db_conn, rows)
        assert len(summary.review_notes) == 1
        assert "payment_method" in summary.review_notes[0]
        assert "702" in summary.review_notes[0]

    def test_multiple_mismatched_fields_produce_one_note_each(self, db_conn):
        rows = [
            make_row(order_number="703", plate_number="MISM03", receipt_number="R-1",
                      tracking_number="G-1"),
            make_row(order_number="703", plate_number="MISM04", receipt_number="R-2",
                      tracking_number="G-2"),
        ]
        summary = process_rows(db_conn, rows)
        assert len(summary.review_notes) == 2
        joined = " ".join(summary.review_notes)
        assert "receipt_number" in joined
        assert "tracking_number" in joined

    def test_agreeing_new_pos_fields_produce_no_review_note(self, db_conn):
        rows = [
            make_row(order_number="704", plate_number="MISM05", payment_method="EFECTIVO",
                      notes="Igual"),
            make_row(order_number="704", plate_number="MISM06", payment_method="EFECTIVO",
                      notes="Igual"),
        ]
        summary = process_rows(db_conn, rows)
        assert summary.review_notes == []

    def test_per_line_fields_are_excluded_from_the_check(self, db_conn):
        """reason/vehicle_brand/unit_price are genuinely per-line -- differing
        across rows in the same order is normal, not a smell."""
        rows = [
            make_row(order_number="705", plate_number="MISM07", reason="DETERIORO",
                      vehicle_brand="CHEVROLET", unit_price=Decimal("100000")),
            make_row(order_number="705", plate_number="MISM08", reason="PÉRDIDA",
                      vehicle_brand="MAZDA", unit_price=Decimal("200000")),
        ]
        summary = process_rows(db_conn, rows)
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
            replace_order(db_conn, "1000", rows)

    def test_rejects_an_empty_row_list(self, db_conn):
        with pytest.raises(ValueError):
            replace_order(db_conn, "1000", [])

    def test_replaces_a_single_existing_order_leaving_only_the_new_one(self, db_conn):
        old_id = _insert_order(db_conn, order_number="1010", order_date=date(2026, 1, 1),
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
        summary = replace_order(db_conn, "1010", new_rows)

        assert summary.orders_replaced == 1
        assert summary.plates_deleted == 1
        assert summary.orders_inserted == 1
        assert summary.plates_inserted == 1

        with db_conn.cursor(row_factory=tuple_row) as cur:
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
        first_id = _insert_order(db_conn, order_number="1020", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=first_id,
                         row=make_row(order_number="1020", plate_type="CARRO PARTICULAR",
                                      is_batch_replacement=True, quantity=30),
                         is_possible_duplicate=False)
        second_id = _insert_order(db_conn, order_number="1020", order_date=date(2026, 1, 10),
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
        summary = replace_order(db_conn, "1020", new_rows)

        assert summary.orders_replaced == 2
        assert summary.plates_deleted == 2
        assert summary.orders_inserted == 1
        assert summary.plates_inserted == 2

        with db_conn.cursor(row_factory=tuple_row) as cur:
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
        summary = replace_order(db_conn, "1030", rows)
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
        old_id = _insert_order(db_conn, order_number="1040", order_date=date(2026, 1, 1),
                                  requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=old_id,
                         row=make_row(order_number="1040", plate_type="CARRO PARTICULAR",
                                      is_batch_replacement=True, quantity=40),
                         is_possible_duplicate=False)
        db.recalc_inventory(db_conn)
        db_conn.commit()
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            assert cur.fetchone() == (40,)  # sanity check before replacing

        new_rows = [
            make_row(order_number="1040", plate_type="CARRO PARTICULAR", plate_number="INV100", quantity=20),
            make_row(order_number="1040", plate_type="CARRO PARTICULAR", plate_number="INV101", quantity=20),
        ]
        replace_order(db_conn, "1040", new_rows)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT consumed FROM inventory_categories WHERE category = 'CARRO PARTICULAR'")
            # 40 total across the two new rows -- NOT 80 (40 old + 40 new)
            assert cur.fetchone() == (40,)

    def test_is_possible_duplicate_set_exactly_as_process_rows_would(self, db_conn):
        """A plate number that's still live elsewhere in the DB (NOT part of
        the order being replaced) must still get flagged on the new row --
        replacing one order must not blind the normal cross-order dup check."""
        process_rows(
            db_conn, [make_row(order_number="1050", plate_type="CARRO PARTICULAR", plate_number="LIVE001")]
        )

        old_id = _insert_order(db_conn, order_number="1051", order_date=date(2026, 1, 1),
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
        summary = replace_order(db_conn, "1051", new_rows)
        assert summary.possible_duplicates == 1

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT p.plate_number, p.is_possible_duplicate FROM plates p "
                "JOIN production_orders o ON o.id = p.order_id "
                "WHERE o.order_number = '1051' ORDER BY p.plate_number"
            )
            assert cur.fetchall() == [("FRESH2", False), ("LIVE001", True)]

    def test_within_batch_repeat_is_flagged_on_both_new_rows(self, db_conn):
        old_id = _insert_order(db_conn, order_number="1060", order_date=date(2026, 1, 1),
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
        summary = replace_order(db_conn, "1060", new_rows)
        assert summary.possible_duplicates == 2

    def test_resolves_a_customer_for_the_new_order(self, db_conn):
        new_rows = [make_row(order_number="1070", plate_type="CARRO PARTICULAR", plate_number="REP100",
                              requested_by="PTO.V")]
        replace_order(db_conn, "1070", new_rows)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT c.name FROM production_orders o JOIN customers c ON c.id = o.customer_id "
                "WHERE o.order_number = '1070'"
            )
            assert cur.fetchone() == ("PTO.V",)

    def test_created_by_records_who_triggered_the_replace(self, db_conn):
        user_id = db.create_user(db_conn, username="alice", password_hash="h", role="admin")
        new_rows = [make_row(order_number="1071", plate_type="CARRO PARTICULAR", plate_number="REP101")]
        replace_order(db_conn, "1071", new_rows, created_by=user_id)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT created_by FROM production_orders WHERE order_number = '1071'")
            assert cur.fetchone() == (user_id,)

    def test_new_plates_take_the_tariff_vigente_at_replace_time_not_the_original_order_date(self, db_conn):
        """Documented behavior (see replace_order's docstring), not a bug:
        the replacement always re-snapshots the CURRENT tariff, regardless
        of when the order was originally placed."""
        old_id = _insert_order(db_conn, order_number="1072", order_date=date(2026, 1, 1),
                                  requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=old_id,
                         row=make_row(order_number="1072", plate_type="CARRO PARTICULAR",
                                      is_batch_replacement=True, quantity=1),
                         is_possible_duplicate=False)
        db_conn.commit()

        db.set_price(db_conn, "CARRO PARTICULAR", Decimal("175000"), "admin")
        new_rows = [make_row(order_number="1072", plate_type="CARRO PARTICULAR", plate_number="REP102")]
        replace_order(db_conn, "1072", new_rows)

        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT unit_price FROM plates WHERE plate_number = 'REP102'")
            assert cur.fetchone() == (Decimal("175000"),)

    def test_order_level_mismatch_is_flagged_same_as_process_rows(self, db_conn):
        new_rows = [
            make_row(order_number="1073", plate_type="CARRO PARTICULAR", plate_number="REP103",
                      payment_method="EFECTIVO"),
            make_row(order_number="1073", plate_type="CARRO PARTICULAR", plate_number="REP104",
                      payment_method="TRANSFERENCIA"),
        ]
        summary = replace_order(db_conn, "1073", new_rows)
        assert len(summary.review_notes) == 1
        assert "payment_method" in summary.review_notes[0]


class TestAtomicity:
    """Fase 1d's whole point for threading `conn` explicitly instead of each
    function opening its own: `with db.get_conn() as conn: process_rows(conn,
    rows)` must be all-or-nothing. Before Fase 1d, process_rows opened (and
    committed) its own connection, so this exact scenario -- the first
    order's insert_plate genuinely succeeding before the second one blows up
    -- would have left the first order committed and orphaned instead of
    rolled back with everything else.
    """

    def test_exception_partway_through_process_rows_rolls_back_the_whole_batch(
        self, db_conn, monkeypatch
    ):
        rows = [
            make_row(order_number="9000", plate_number="ATOM001", plate_type="CARRO PARTICULAR"),
            make_row(order_number="9001", plate_number="ATOM002", plate_type="CARRO PARTICULAR"),
        ]

        # order "9000" has exactly one plate row, so this is the SECOND
        # insert_plate call overall -- it's for order "9001", by which point
        # "9000"'s order + plate have already been (uncommitted-ly) inserted
        # in this same transaction.
        real_insert_plate = db.insert_plate
        calls = {"n": 0}

        def _boom(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated failure partway through process_rows")
            return real_insert_plate(*args, **kwargs)

        monkeypatch.setattr(db, "insert_plate", _boom)

        with pytest.raises(RuntimeError, match="simulated failure"), db.get_conn() as conn:
            process_rows(conn, rows)

        # A fresh read (via the unrelated db_conn fixture connection) must see
        # NEITHER order -- not even "9000", whose own insert_plate call never
        # raised anything.
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT count(*) FROM production_orders WHERE order_number IN ('9000', '9001')"
            )
            assert cur.fetchone() == (0,)
            cur.execute("SELECT count(*) FROM plates WHERE plate_number IN ('ATOM001', 'ATOM002')")
            assert cur.fetchone() == (0,)


class TestOwnershipCardLink:
    """process_rows / replace_order link fabricated plates to an existing
    ownership card, per the order's customer type."""

    def _card(self, db_conn, plate="LNK123"):
        owner = db.resolve_or_create_customer(db_conn, "TITULAR")
        return db.create_ownership_card(db_conn, owner, transit_license="LIC-LNK", plate_number=plate)

    def _link(self, db_conn, plate):
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT ownership_card_id FROM plates WHERE plate_number = %s", (plate,))
            return cur.fetchone()[0]

    def _classify(self, db_conn, name, customer_type):
        with db_conn.cursor() as cur:
            cur.execute("UPDATE customers SET type = %s WHERE name = %s", (customer_type, name))

    def test_process_rows_links_plate_of_a_cliente_order(self, db_conn):
        card_id = self._card(db_conn)
        db.resolve_or_create_customer(db_conn, "JUAN")
        self._classify(db_conn, "JUAN", "CLIENTE")
        process_rows(db_conn, [make_row(order_number="700", requested_by="JUAN", plate_number="LNK123")])
        assert self._link(db_conn, "LNK123") == card_id

    def test_process_rows_does_not_link_an_empresa_order(self, db_conn):
        self._card(db_conn)
        db.resolve_or_create_customer(db_conn, "ACME")
        self._classify(db_conn, "ACME", "EMPRESA")
        process_rows(db_conn, [make_row(order_number="701", requested_by="ACME", plate_number="LNK123")])
        assert self._link(db_conn, "LNK123") is None

    def test_process_rows_without_card_leaves_null(self, db_conn):
        process_rows(db_conn, [make_row(order_number="702", requested_by="JUAN", plate_number="NOC123")])
        assert self._link(db_conn, "NOC123") is None

    def test_replace_order_links_the_recovered_plate(self, db_conn):
        card_id = self._card(db_conn)
        db.resolve_or_create_customer(db_conn, "JUAN")
        self._classify(db_conn, "JUAN", "GESTOR")
        old_id = _insert_order(db_conn, order_number="710", order_date=date(2026, 1, 1),
                               requested_by="JUAN")
        db.insert_plate(db_conn, order_id=old_id, row=make_row(order_number="710", plate_number=None),
                        is_possible_duplicate=False)
        replace_order(db_conn, "710", [make_row(order_number="710", requested_by="JUAN",
                                                 plate_number="LNK123")])
        assert self._link(db_conn, "LNK123") == card_id
