"""Unit tests for app.core.db that don't need a real Postgres connection.

get_conn() reads DB_HOST/DB_PORT/POSTGRES_* straight from os.environ on every
call (see module docstring in app/core/db.py) and passes them through to
psycopg.connect(). These tests mock psycopg.connect to verify exactly which
arguments get_conn() builds, including its DB_HOST/DB_PORT fallback defaults
-- which tests/conftest.py always overrides for safety, so nothing else in
this suite exercises that fallback path.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from app.core import db
from app.core.models import ExtractedRow


class TestGetConn:
    def test_passes_through_all_env_vars(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "myhost")
        monkeypatch.setenv("DB_PORT", "1234")
        monkeypatch.setenv("POSTGRES_DB", "mydb")
        monkeypatch.setenv("POSTGRES_USER", "myuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "mypassword")

        with patch("app.core.db.psycopg.connect") as mock_connect:
            db.get_conn()

        mock_connect.assert_called_once_with(
            host="myhost", port="1234", dbname="mydb",
            user="myuser", password="mypassword",
        )

    def test_host_defaults_to_db_when_unset(self, monkeypatch):
        monkeypatch.delenv("DB_HOST", raising=False)
        monkeypatch.setenv("POSTGRES_DB", "mydb")
        monkeypatch.setenv("POSTGRES_USER", "myuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "mypassword")

        with patch("app.core.db.psycopg.connect") as mock_connect:
            db.get_conn()

        assert mock_connect.call_args.kwargs["host"] == "db"

    def test_port_defaults_to_5432_when_unset(self, monkeypatch):
        monkeypatch.delenv("DB_PORT", raising=False)
        monkeypatch.setenv("POSTGRES_DB", "mydb")
        monkeypatch.setenv("POSTGRES_USER", "myuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "mypassword")

        with patch("app.core.db.psycopg.connect") as mock_connect:
            db.get_conn()

        assert mock_connect.call_args.kwargs["port"] == "5432"

    def test_missing_required_postgres_db_raises(self, monkeypatch):
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        monkeypatch.setenv("POSTGRES_USER", "myuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "mypassword")

        with patch("app.core.db.psycopg.connect"), pytest.raises(KeyError):
            db.get_conn()


# ----------------------------------------------------------------------------
# Everything below needs a real Postgres connection (see tests/conftest.py:
# `db_conn` truncates all 4 tables via `clean_db`, then hands back a live
# connection). These call db.py's own insert_order/insert_plate/
# insert_blank_preparation for setup -- deliberately bypassing app.core.process
# and app.core.normalize -- so a failure here points at db.py itself, not at
# normalization or the ingestion pipeline (already covered elsewhere).
# ----------------------------------------------------------------------------


def make_row(**overrides: object) -> ExtractedRow:
    defaults: dict[str, object] = {
        "order_number": "1",
        "order_date": date(2026, 1, 1),
        "requested_by": "PEDRO",
        "plate_type": "CARRO PARTICULAR",
    }
    defaults.update(overrides)
    return ExtractedRow(**defaults)


def _insert_plate_get_id(conn, order_id: int, **row_kwargs: object) -> int:
    """Insert a plate via db.insert_plate and return its new id (insert_plate
    itself returns None -- there's no RETURNING id in its INSERT)."""
    is_dup = bool(row_kwargs.pop("is_possible_duplicate", False))
    row = make_row(**row_kwargs)
    db.insert_plate(conn, order_id=order_id, row=row, is_possible_duplicate=is_dup)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM plates WHERE order_id = %s ORDER BY id DESC LIMIT 1", (order_id,))
        return cur.fetchone()[0]


class TestInsertPlate:
    """City is stored per PLATE (plates.city), not per order."""

    def test_stores_the_rows_own_city(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="10", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(city="MEDELLIN"),
                        is_possible_duplicate=False)
        with db_conn.cursor() as cur:
            cur.execute("SELECT city FROM plates WHERE order_id = %s", (order_id,))
            assert cur.fetchone()[0] == "MEDELLIN"

    def test_city_may_be_null(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="11", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(city=None),
                        is_possible_duplicate=False)
        with db_conn.cursor() as cur:
            cur.execute("SELECT city FROM plates WHERE order_id = %s", (order_id,))
            assert cur.fetchone()[0] is None

    def test_two_plates_in_one_order_keep_different_cities(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="12", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        for city in ("CALI", "PASTO"):
            db.insert_plate(db_conn, order_id=order_id, row=make_row(city=city),
                            is_possible_duplicate=False)
        with db_conn.cursor() as cur:
            cur.execute("SELECT city FROM plates WHERE order_id = %s ORDER BY id", (order_id,))
            assert [r[0] for r in cur.fetchall()] == ["CALI", "PASTO"]


class TestInventoryView:
    def test_computes_available_from_prepared_and_consumed(self, db_conn):
        db.insert_blank_preparation(db_conn, prep_date=date(2026, 1, 1), category="CARRO PARTICULAR",
                                     quantity=100, prepared_by="GILMER")
        order_id = db.insert_order(db_conn, order_number="1", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_type="CARRO PARTICULAR", quantity=30),
                         is_possible_duplicate=False)
        db.recalc_inventory(db_conn)

        rows = {r["category"]: r for r in db.inventory_view(db_conn)}
        assert rows["CARRO PARTICULAR"]["prepared"] == 100
        assert rows["CARRO PARTICULAR"]["consumed"] == 30
        assert rows["CARRO PARTICULAR"]["available"] == 70
        assert rows["CARRO PARTICULAR"]["wasted"] == 0

    def test_wasted_counts_only_plate_annulled_rows_but_they_still_count_as_consumed(self, db_conn):
        db.insert_blank_preparation(db_conn, prep_date=date(2026, 1, 1), category="MOTO PARTICULAR",
                                     quantity=50, prepared_by=None)
        order_id = db.insert_order(db_conn, order_number="2", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="MOTO PARTICULAR", quantity=5, is_plate_annulled=True),
                         is_possible_duplicate=False)
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="MOTO PARTICULAR", quantity=10, is_plate_annulled=False),
                         is_possible_duplicate=False)
        db.recalc_inventory(db_conn)

        rows = {r["category"]: r for r in db.inventory_view(db_conn)}
        assert rows["MOTO PARTICULAR"]["consumed"] == 15  # both count toward consumed
        assert rows["MOTO PARTICULAR"]["wasted"] == 5  # only the annulled one

    def test_category_with_no_annulled_plates_defaults_wasted_to_zero(self, db_conn):
        db.insert_blank_preparation(db_conn, prep_date=date(2026, 1, 1), category="PÚBLICO",
                                     quantity=20, prepared_by=None)
        db.recalc_inventory(db_conn)
        rows = {r["category"]: r for r in db.inventory_view(db_conn)}
        assert rows["PÚBLICO"]["wasted"] == 0

    def test_no_inventory_categories_rows_returns_empty_list(self, db_conn):
        assert db.inventory_view(db_conn) == []


class TestOrnamentalCount:
    def test_returns_zero_when_no_ornamental_plates(self, db_conn):
        assert db.ornamental_count(db_conn) == 0

    def test_sums_quantity_across_plates(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="1", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_type="ORNAMENTAL", quantity=4),
                         is_possible_duplicate=False)
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_type="ORNAMENTAL", quantity=6),
                         is_possible_duplicate=False)
        assert db.ornamental_count(db_conn) == 10

    def test_excludes_voided_plates(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="1", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="ORNAMENTAL", quantity=4, is_voided=True),
                         is_possible_duplicate=False)
        assert db.ornamental_count(db_conn) == 0

    def test_non_ornamental_plates_not_counted(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="1", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_type="CARRO PARTICULAR", quantity=9),
                         is_possible_duplicate=False)
        assert db.ornamental_count(db_conn) == 0


class TestDistinctRequestedBy:
    def test_orders_by_frequency_then_alphabetically(self, db_conn):
        db.insert_order(db_conn, order_number="1", order_date=date(2026, 1, 1), requested_by="PEDRO")
        db.insert_order(db_conn, order_number="2", order_date=date(2026, 1, 1), requested_by="PEDRO")
        db.insert_order(db_conn, order_number="3", order_date=date(2026, 1, 1), requested_by="JUAN")
        assert db.distinct_requested_by(db_conn) == ["PEDRO", "JUAN"]

    def test_drops_punctuation_only_junk(self, db_conn):
        db.insert_order(db_conn, order_number="1", order_date=date(2026, 1, 1), requested_by="//")
        db.insert_order(db_conn, order_number="2", order_date=date(2026, 1, 1), requested_by="PEDRO")
        assert db.distinct_requested_by(db_conn) == ["PEDRO"]

    def test_empty_db_returns_empty_list(self, db_conn):
        assert db.distinct_requested_by(db_conn) == []


class TestDistinctCities:
    """Cities come from PLATES now (production_orders has no city column)."""

    @staticmethod
    def _order_with_cities(db_conn, order_number: str, *cities: str | None) -> int:
        order_id = db.insert_order(db_conn, order_number=order_number,
                                   order_date=date(2026, 1, 1), requested_by="PEDRO")
        for c in cities:
            db.insert_plate(db_conn, order_id=order_id, row=make_row(city=c),
                            is_possible_duplicate=False)
        return order_id

    def test_orders_by_frequency_then_alphabetically(self, db_conn):
        self._order_with_cities(db_conn, "1", "BOGOTA")
        self._order_with_cities(db_conn, "2", "BOGOTA")
        self._order_with_cities(db_conn, "3", "CALI")
        assert db.distinct_cities(db_conn) == ["BOGOTA", "CALI"]

    def test_reads_from_plates_not_orders(self, db_conn):
        """Two cities under ONE order both show up -- the old order-level column
        could only ever have held one of them."""
        self._order_with_cities(db_conn, "1", "CALI", "MEDELLIN")
        assert db.distinct_cities(db_conn) == ["CALI", "MEDELLIN"]

    def test_drops_anulad_marker_entries(self, db_conn):
        self._order_with_cities(db_conn, "1", "ANULADO")
        self._order_with_cities(db_conn, "2", "BOGOTA")
        assert db.distinct_cities(db_conn) == ["BOGOTA"]

    def test_drops_punctuation_only_junk(self, db_conn):
        self._order_with_cities(db_conn, "1", "--")
        self._order_with_cities(db_conn, "2", "CALI")
        assert db.distinct_cities(db_conn) == ["CALI"]

    def test_null_or_blank_city_excluded(self, db_conn):
        self._order_with_cities(db_conn, "1", None)
        assert db.distinct_cities(db_conn) == []


class TestRecentOrders:
    def test_summarizes_plate_labels_per_row_kind(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="500", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="AAA111"),
                         is_possible_duplicate=False)
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", is_batch_replacement=True),
                         is_possible_duplicate=False)
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", is_voided=True),
                         is_possible_duplicate=False)
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_type="CARRO PARTICULAR"),
                         is_possible_duplicate=False)  # no plate_number, not batch/voided -> 'sin placa'

        rows = db.recent_orders(db_conn)
        assert len(rows) == 1
        row = rows[0]
        assert row["order_number"] == "500"
        assert row["n_plates"] == 4
        assert row["total_qty"] == 4
        assert row["plates"] == "AAA111, reposición, anulada, sin placa"

    def test_order_with_no_plates_shows_zero_counts_and_no_plates_label(self, db_conn):
        """A LEFT JOIN with zero matching plates still produces exactly one
        (all-NULL) joined row. The CASE's first branch (p.id IS NULL) catches
        that phantom row and yields NULL instead of falling through to a real
        plate-describing branch, so string_agg has nothing to aggregate and
        'plates' reads NULL -- distinct from a real 'sin placa' row (a plate
        line that was recorded but has no plate_number typed in, covered by
        test_summarizes_plate_labels_per_row_kind below)."""
        db.insert_order(db_conn, order_number="501", order_date=date(2026, 1, 1), requested_by="PEDRO")
        rows = db.recent_orders(db_conn)
        assert rows[0]["n_plates"] == 0
        assert rows[0]["total_qty"] == 0
        assert rows[0]["plates"] is None

    def test_cities_aggregates_the_orders_distinct_plate_cities(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="510", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        for city in ("CALI", "COLOMBIA", "CALI"):  # repeat -> deduped
            db.insert_plate(db_conn, order_id=order_id, row=make_row(city=city),
                            is_possible_duplicate=False)
        assert sorted(db.recent_orders(db_conn)[0]["cities"]) == ["CALI", "COLOMBIA"]

    def test_sorted_by_order_date_not_by_when_it_was_inserted(self, db_conn):
        """A correction entered today for a months-old order (e.g. fixing a
        duplicate found during a review pass) must not jump to the top and
        bury today's real activity -- insertion order (created_at/id) is
        only a tiebreaker for orders sharing the same order_date."""
        db.insert_order(db_conn, order_number="520", order_date=date(2026, 1, 1), requested_by="PEDRO")
        db.insert_order(db_conn, order_number="521", order_date=date(2026, 6, 15), requested_by="PEDRO")
        assert [r["order_number"] for r in db.recent_orders(db_conn)] == ["521", "520"]


class TestSearchPlatesByNumber:
    """Finds a plate anywhere in the DB, not just the 50 most recent orders --
    the point is checking whether a plate was ever made before."""

    def test_exact_match(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="600", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="ARN911"),
                         is_possible_duplicate=False)
        results = db.search_plates_by_number(db_conn, "ARN911")
        assert len(results) == 1
        assert results[0]["plate_number"] == "ARN911"
        assert results[0]["order_number"] == "600"

    def test_partial_match(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="601", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="ARN911"),
                         is_possible_duplicate=False)
        assert [r["plate_number"] for r in db.search_plates_by_number(db_conn, "RN91")] == ["ARN911"]

    def test_query_is_cleaned_like_a_stored_plate_number(self, db_conn):
        """'arn 911' (lowercase, stray space) must still find 'ARN911'."""
        order_id = db.insert_order(db_conn, order_number="602", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="ARN911"),
                         is_possible_duplicate=False)
        assert [r["plate_number"] for r in db.search_plates_by_number(db_conn, "arn 911")] == ["ARN911"]

    def test_matches_plate_to_for_ranges(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="603", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(
            db_conn, order_id=order_id,
            row=make_row(plate_number="HMF900", plate_to="HMF930"),
            is_possible_duplicate=False,
        )
        assert [r["plate_number"] for r in db.search_plates_by_number(db_conn, "HMF930")] == ["HMF900"]

    def test_not_limited_to_the_50_most_recent_orders(self, db_conn):
        """recent_orders() caps at 50; search must not inherit that limit --
        it exists specifically to reach plates recent_orders can't show."""
        for i in range(55):
            order_id = db.insert_order(db_conn, order_number=str(700 + i), order_date=date(2026, 1, 1),
                                        requested_by="PEDRO")
            db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number=f"OLD{i:03d}"),
                             is_possible_duplicate=False)
        assert len(db.search_plates_by_number(db_conn, "OLD")) == 55

    def test_no_match_returns_empty_list(self, db_conn):
        assert db.search_plates_by_number(db_conn, "ZZZ999") == []

    def test_most_recent_order_first(self, db_conn):
        older = db.insert_order(db_conn, order_number="610", order_date=date(2026, 1, 1),
                                 requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=older, row=make_row(plate_number="DUP001"),
                         is_possible_duplicate=False)
        newer = db.insert_order(db_conn, order_number="611", order_date=date(2026, 2, 1),
                                 requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=newer, row=make_row(plate_number="DUP001"),
                         is_possible_duplicate=False)
        results = db.search_plates_by_number(db_conn, "DUP001")
        assert [r["order_number"] for r in results] == ["611", "610"]

    def test_cities_is_a_single_element_list_for_a_one_city_order(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="511", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(city="PASTO"),
                        is_possible_duplicate=False)
        assert db.recent_orders(db_conn)[0]["cities"] == ["PASTO"]

    def test_cities_is_empty_when_no_plate_has_one(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="512", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(city=None),
                        is_possible_duplicate=False)
        assert db.recent_orders(db_conn)[0]["cities"] == []

    def test_cities_is_empty_for_an_order_with_no_plates_at_all(self, db_conn):
        db.insert_order(db_conn, order_number="513", order_date=date(2026, 1, 1),
                        requested_by="PEDRO")
        assert db.recent_orders(db_conn)[0]["cities"] == []

    def test_most_recently_inserted_order_first(self, db_conn):
        db.insert_order(db_conn, order_number="600", order_date=date(2026, 1, 1), requested_by="PEDRO")
        db.insert_order(db_conn, order_number="601", order_date=date(2026, 1, 1), requested_by="PEDRO")
        rows = db.recent_orders(db_conn)
        assert [r["order_number"] for r in rows] == ["601", "600"]

    def test_limit_caps_number_of_rows_returned(self, db_conn):
        for i in range(5):
            db.insert_order(db_conn, order_number=str(700 + i), order_date=date(2026, 1, 1),
                             requested_by="PEDRO")
        rows = db.recent_orders(db_conn, limit=2)
        assert len(rows) == 2


class TestOrderDetail:
    def test_returns_order_and_its_plates(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="900", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_type="CARRO PARTICULAR", plate_number="ZZZ111", quantity=2,
                                      city="BOGOTA"),
                         is_possible_duplicate=False)

        order, plates = db.order_detail(db_conn, order_id)
        assert order["order_number"] == "900"
        assert order["requested_by"] == "PEDRO"
        assert "city" not in order  # city lives on the plate, not the order
        assert len(plates) == 1
        assert plates[0]["plate_number"] == "ZZZ111"
        assert plates[0]["quantity"] == 2
        assert plates[0]["city"] == "BOGOTA"

    def test_plates_keep_their_own_distinct_cities(self, db_conn):
        """The original bug: two plates in ONE order kept only the first city."""
        order_id = db.insert_order(db_conn, order_number="901", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        for plate, city in (("AAA111", "MEDELLIN"), ("BBB222", "PASTO")):
            db.insert_plate(db_conn, order_id=order_id,
                             row=make_row(plate_number=plate, city=city),
                             is_possible_duplicate=False)

        _order, plates = db.order_detail(db_conn, order_id)
        assert [(p["plate_number"], p["city"]) for p in plates] == [
            ("AAA111", "MEDELLIN"), ("BBB222", "PASTO"),
        ]

    def test_nonexistent_order_id_returns_none_and_empty_list(self, db_conn):
        order, plates = db.order_detail(db_conn, 999999)
        assert order is None
        assert plates == []

    def test_plates_ordered_by_id(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="901", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="A1"), is_possible_duplicate=False)
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="A2"), is_possible_duplicate=False)
        _, plates = db.order_detail(db_conn, order_id)
        assert [p["plate_number"] for p in plates] == ["A1", "A2"]


class TestDeleteOrder:
    def test_removes_order_and_its_plates(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="1000", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="B1"), is_possible_duplicate=False)

        db.delete_order(db_conn, order_id)

        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE id = %s", (order_id,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM plates WHERE order_id = %s", (order_id,))
            assert cur.fetchone()[0] == 0

    def test_does_not_affect_other_orders(self, db_conn):
        keep_id = db.insert_order(db_conn, order_number="1001", order_date=date(2026, 1, 1),
                                   requested_by="PEDRO")
        delete_id = db.insert_order(db_conn, order_number="1002", order_date=date(2026, 1, 1),
                                     requested_by="PEDRO")
        db.delete_order(db_conn, delete_id)
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM production_orders WHERE id = %s", (keep_id,))
            assert cur.fetchone()[0] == 1

    def test_nonexistent_order_id_is_a_silent_noop(self, db_conn):
        db.delete_order(db_conn, 999999)  # must not raise


class TestReviewRows:
    def test_returns_only_flagged_rows(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="1100", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="FLAG1"),
                         is_possible_duplicate=True)
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="CLEAN1"),
                         is_possible_duplicate=False)

        rows = db.review_rows(db_conn)
        assert [r["plate_number"] for r in rows] == ["FLAG1"]
        assert rows[0]["order_number"] == "1100"

    def test_includes_the_plates_city(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="1103", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id,
                         row=make_row(plate_number="FLAG2", city="NEIVA"),
                         is_possible_duplicate=True)
        assert db.review_rows(db_conn)[0]["city"] == "NEIVA"

    def test_ordered_by_plate_number_then_order_date(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="1101", order_date=date(2026, 1, 5),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="ZZZ"),
                         is_possible_duplicate=True)
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="AAA"),
                         is_possible_duplicate=True)
        rows = db.review_rows(db_conn)
        assert [r["plate_number"] for r in rows] == ["AAA", "ZZZ"]

    def test_empty_when_nothing_flagged(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="1102", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number="CLEAN2"),
                         is_possible_duplicate=False)
        assert db.review_rows(db_conn) == []


class TestUpdatePlate:
    def test_edits_fields_and_clears_review_flag(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="1200", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        plate_id = _insert_plate_get_id(db_conn, order_id, plate_number="OLD111",
                                         quantity=1, is_possible_duplicate=True)

        db.update_plate(db_conn, plate_id, plate_type="MOTO PARTICULAR", plate_number="NEW222",
                         quantity=9, is_voided=True)

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT plate_type, plate_number, quantity, is_voided, is_possible_duplicate "
                "FROM plates WHERE id = %s",
                (plate_id,),
            )
            assert cur.fetchone() == ("MOTO PARTICULAR", "NEW222", 9, True, False)

    def test_plate_number_can_be_cleared_to_null(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="1201", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        plate_id = _insert_plate_get_id(db_conn, order_id, plate_number="HAS111")

        db.update_plate(db_conn, plate_id, plate_type="CARRO PARTICULAR", plate_number=None,
                         quantity=1, is_voided=False)

        with db_conn.cursor() as cur:
            cur.execute("SELECT plate_number FROM plates WHERE id = %s", (plate_id,))
            assert cur.fetchone() == (None,)


class TestResolvePlate:
    def test_clears_flag_without_touching_other_fields(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="1300", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        plate_id = _insert_plate_get_id(db_conn, order_id, plate_number="RES111",
                                         quantity=3, is_possible_duplicate=True)

        db.resolve_plate(db_conn, plate_id)

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT plate_number, quantity, is_possible_duplicate FROM plates WHERE id = %s",
                (plate_id,),
            )
            assert cur.fetchone() == ("RES111", 3, False)

    def test_nonexistent_plate_id_is_a_silent_noop(self, db_conn):
        db.resolve_plate(db_conn, 999999)  # must not raise


class TestDeletePlate:
    """delete_plate also auto-clears a surviving sibling's review flag when
    exactly one other flagged row shares its plate_number -- see
    app/core/db.py's docstring. This was a real bug found and fixed during
    manual duplicate-review cleanup; these tests pin down the exact
    threshold (1 remaining -> cleared, 2+ remaining -> left alone)."""

    def test_deletes_the_row(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="1400", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        plate_id = _insert_plate_get_id(db_conn, order_id, plate_number="DEL111")

        db.delete_plate(db_conn, plate_id)

        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM plates WHERE id = %s", (plate_id,))
            assert cur.fetchone()[0] == 0

    def test_clears_sibling_flag_when_exactly_one_remains(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="1401", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        deleted_id = _insert_plate_get_id(db_conn, order_id, plate_number="SIB111",
                                           is_possible_duplicate=True)
        sibling_id = _insert_plate_get_id(db_conn, order_id, plate_number="SIB111",
                                           is_possible_duplicate=True)

        db.delete_plate(db_conn, deleted_id)

        with db_conn.cursor() as cur:
            cur.execute("SELECT is_possible_duplicate FROM plates WHERE id = %s", (sibling_id,))
            assert cur.fetchone() == (False,)

    def test_leaves_flag_when_two_or_more_siblings_remain(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="1402", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        deleted_id = _insert_plate_get_id(db_conn, order_id, plate_number="SIB222",
                                           is_possible_duplicate=True)
        s1 = _insert_plate_get_id(db_conn, order_id, plate_number="SIB222", is_possible_duplicate=True)
        s2 = _insert_plate_get_id(db_conn, order_id, plate_number="SIB222", is_possible_duplicate=True)

        db.delete_plate(db_conn, deleted_id)

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT is_possible_duplicate FROM plates WHERE id = ANY(%s) ORDER BY id",
                ([s1, s2],),
            )
            assert [r[0] for r in cur.fetchall()] == [True, True]

    def test_does_not_touch_an_already_unflagged_row_with_same_plate_number(self, db_conn):
        order_id = db.insert_order(db_conn, order_number="1403", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        deleted_id = _insert_plate_get_id(db_conn, order_id, plate_number="SIB333",
                                           is_possible_duplicate=True)
        clean_id = _insert_plate_get_id(db_conn, order_id, plate_number="SIB333",
                                         is_possible_duplicate=False)

        db.delete_plate(db_conn, deleted_id)

        with db_conn.cursor() as cur:
            cur.execute("SELECT is_possible_duplicate FROM plates WHERE id = %s", (clean_id,))
            assert cur.fetchone() == (False,)

    def test_deleted_row_without_a_plate_number_does_not_touch_others(self, db_conn):
        """plate_number is None for e.g. reposición rows -- the `if plate_number:`
        guard must skip the sibling lookup entirely rather than matching other
        NULL-plate_number rows against each other."""
        order_id = db.insert_order(db_conn, order_number="1404", order_date=date(2026, 1, 1),
                                    requested_by="PEDRO")
        deleted_id = _insert_plate_get_id(db_conn, order_id, is_batch_replacement=True)
        other_id = _insert_plate_get_id(db_conn, order_id, is_batch_replacement=True,
                                         is_possible_duplicate=True)

        db.delete_plate(db_conn, deleted_id)

        with db_conn.cursor() as cur:
            cur.execute("SELECT is_possible_duplicate FROM plates WHERE id = %s", (other_id,))
            assert cur.fetchone() == (True,)  # untouched -- no plate_number to match on

    def test_nonexistent_plate_id_is_a_silent_noop(self, db_conn):
        db.delete_plate(db_conn, 999999)  # must not raise
