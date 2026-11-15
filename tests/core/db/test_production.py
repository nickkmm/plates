"""Integration tests for app/core/db/production.py -- the production status
data layer (set_production_status, list_production_events,
production_statuses_for_orders, list_production_orders,
count_by_production_status). Needs a real Postgres connection (see
tests/conftest.py's `db_conn`/`clean_db`)."""

from __future__ import annotations

from datetime import date, datetime

from psycopg.rows import tuple_row

from app.core import db
from tests.conftest import TEST_USERNAME
from tests.core.db._helpers import _insert_order, make_row


def _order(db_conn, order_number: str, order_date: date = date(2026, 1, 1), *, status: str | None = None,
           plates: tuple[dict, ...] = ()) -> int:
    """Insert an order (pending by default) with the given plates (make_row
    kwargs), then force its production_status when one is given."""
    order_id = _insert_order(db_conn, order_number=order_number, order_date=order_date, requested_by="PEDRO")
    for plate in plates:
        db.insert_plate(db_conn, order_id=order_id, row=make_row(**plate), is_possible_duplicate=False)
    if status is not None:
        with db_conn.cursor() as cur:
            cur.execute("UPDATE production_orders SET production_status = %s WHERE id = %s", (status, order_id))
    return order_id


def _user_id(db_conn, username: str = TEST_USERNAME) -> int:
    user = db.get_user_by_username(db_conn, username)
    assert user is not None
    return user.id


def _event_count(db_conn, order_id: int) -> int:
    with db_conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT count(*) FROM production_order_events WHERE order_id = %s", (order_id,))
        return cur.fetchone()[0]


class TestConstants:
    def test_statuses_in_order(self):
        assert db.PRODUCTION_STATUSES == ("pending", "in_progress", "ready", "delivered")
        assert db.ACTIVE_STATUSES == ("pending", "in_progress", "ready")


class TestGetProductionStatus:
    def test_new_order_is_pending(self, db_conn):
        assert db.get_production_status(db_conn, _order(db_conn, "1")) == "pending"

    def test_unknown_order_is_none(self, db_conn):
        assert db.get_production_status(db_conn, 999999) is None


class TestSetProductionStatus:
    def test_right_from_status_updates_and_writes_one_event(self, db_conn):
        order_id = _order(db_conn, "1")
        user_id = _user_id(db_conn)
        assert db.set_production_status(
            db_conn, order_id, from_status="pending", to_status="in_progress", user_id=user_id,
        ) is True
        assert db.get_production_status(db_conn, order_id) == "in_progress"
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT from_status, to_status, user_id FROM production_order_events "
                        "WHERE order_id = %s", (order_id,))
            assert cur.fetchall() == [("pending", "in_progress", user_id)]

    def test_wrong_from_status_changes_nothing_and_writes_no_event(self, db_conn):
        order_id = _order(db_conn, "1")
        assert db.set_production_status(
            db_conn, order_id, from_status="ready", to_status="delivered", user_id=None,
        ) is False
        assert db.get_production_status(db_conn, order_id) == "pending"
        assert _event_count(db_conn, order_id) == 0

    def test_unknown_order_returns_false(self, db_conn):
        assert db.set_production_status(
            db_conn, 999999, from_status="pending", to_status="in_progress", user_id=None,
        ) is False


class TestListProductionEvents:
    def test_oldest_first_with_username_and_null_user(self, db_conn):
        order_id = _order(db_conn, "1")
        db.set_production_status(db_conn, order_id, from_status="pending", to_status="in_progress",
                                 user_id=_user_id(db_conn))
        db.set_production_status(db_conn, order_id, from_status="in_progress", to_status="ready",
                                 user_id=None)
        events = db.list_production_events(db_conn, order_id)
        assert [(e.from_status, e.to_status, e.username) for e in events] == [
            ("pending", "in_progress", TEST_USERNAME),
            ("in_progress", "ready", None),
        ]
        assert all(isinstance(e.created_at, datetime) for e in events)

    def test_only_that_orders_events(self, db_conn):
        order_id = _order(db_conn, "1")
        other_id = _order(db_conn, "2")
        db.set_production_status(db_conn, other_id, from_status="pending", to_status="in_progress",
                                 user_id=None)
        assert db.list_production_events(db_conn, order_id) == []


class TestProductionStatusesForOrders:
    def test_maps_several_ids_in_one_call(self, db_conn):
        a = _order(db_conn, "1")
        b = _order(db_conn, "2", status="ready")
        c = _order(db_conn, "3", status="delivered")
        assert db.production_statuses_for_orders(db_conn, [a, b, c, 999999]) == {
            a: "pending", b: "ready", c: "delivered",
        }

    def test_empty_input_returns_empty_dict(self, db_conn):
        assert db.production_statuses_for_orders(db_conn, []) == {}


class TestListProductionOrders:
    def test_filters_by_status_and_none_returns_all(self, db_conn):
        pending = _order(db_conn, "1")
        ready = _order(db_conn, "2", status="ready")
        delivered = _order(db_conn, "3", status="delivered")
        assert [o.id for o in db.list_production_orders(db_conn, status="pending")] == [pending]
        assert [o.id for o in db.list_production_orders(db_conn, status="ready")] == [ready]
        assert {o.id for o in db.list_production_orders(db_conn, status=None)} == {pending, ready, delivered}

    def test_query_matches_partial_order_number(self, db_conn):
        match = _order(db_conn, "NT 895")
        _order(db_conn, "1200")
        assert [o.id for o in db.list_production_orders(db_conn, status=None, query=" nt 89 ")] == [match]

    def test_query_matches_plate_number_cleaned(self, db_conn):
        match = _order(db_conn, "1", plates=({"plate_number": "ABC123"},))
        _order(db_conn, "2", plates=({"plate_number": "XYZ999"},))
        assert [o.id for o in db.list_production_orders(db_conn, status=None, query="abc 123")] == [match]

    def test_blank_query_is_ignored(self, db_conn):
        a = _order(db_conn, "1")
        b = _order(db_conn, "2")
        assert {o.id for o in db.list_production_orders(db_conn, status=None, query="   ")} == {a, b}

    def test_fully_voided_order_is_excluded_but_partly_voided_and_empty_are_kept(self, db_conn):
        _order(db_conn, "1", plates=({"plate_number": "AAA111", "is_voided": True},
                                     {"plate_number": "AAA112", "is_voided": True}))
        partly = _order(db_conn, "2", plates=({"plate_number": "BBB111", "is_voided": True},
                                              {"plate_number": "BBB112"}))
        empty = _order(db_conn, "3")
        assert {o.id for o in db.list_production_orders(db_conn, status=None)} == {partly, empty}

    def test_active_statuses_oldest_first(self, db_conn):
        newer = _order(db_conn, "1", date(2026, 3, 1))
        older = _order(db_conn, "2", date(2026, 1, 1))
        same_day_later_id = _order(db_conn, "3", date(2026, 1, 1))
        assert [o.id for o in db.list_production_orders(db_conn, status="pending")] == [
            older, same_day_later_id, newer,
        ]

    def test_delivered_and_all_newest_first(self, db_conn):
        older = _order(db_conn, "1", date(2026, 1, 1), status="delivered")
        newer = _order(db_conn, "2", date(2026, 3, 1), status="delivered")
        middle = _order(db_conn, "3", date(2026, 2, 1))
        assert [o.id for o in db.list_production_orders(db_conn, status="delivered")] == [newer, older]
        assert [o.id for o in db.list_production_orders(db_conn, status=None)] == [newer, middle, older]

    def test_limit(self, db_conn):
        for n in range(3):
            _order(db_conn, str(n))
        assert len(db.list_production_orders(db_conn, status=None, limit=2)) == 2

    def test_summary_fields(self, db_conn):
        order_id = _order(db_conn, "1", plates=(
            {"plate_number": "AAA111", "quantity": 2},
            {"plate_number": "AAA112", "is_voided": True, "quantity": 5},
            {"is_batch_replacement": True, "quantity": 3},
        ))
        (summary,) = db.list_production_orders(db_conn, status=None)
        assert summary.id == order_id
        assert (summary.order_number, summary.order_date, summary.requested_by) == (
            "1", date(2026, 1, 1), "PEDRO",
        )
        assert summary.delivery_date is None
        assert summary.production_status == "pending"
        assert summary.plate_count == 5  # 2 + 3, the voided plate's 5 ignored
        assert summary.plates == "AAA111, AAA112, reposición"
        assert summary.declaration_ids == []

    def test_order_with_no_plates(self, db_conn):
        _order(db_conn, "1")
        (summary,) = db.list_production_orders(db_conn, status=None)
        assert (summary.plate_count, summary.plates) == (0, None)

    def test_declaration_ids_lists_linked_declarations(self, db_conn):
        order_id = _order(db_conn, "1")
        ids = []
        for _ in range(2):
            declaration_id = db.insert_declaration(
                db_conn, acting_as="PROPIETARIO", status="approved", submitted_data={},
            )
            db.set_declaration_production_order(db_conn, declaration_id, order_id)
            ids.append(declaration_id)
        db.insert_declaration(db_conn, acting_as="PROPIETARIO", status="approved", submitted_data={})
        (summary,) = db.list_production_orders(db_conn, status=None)
        assert summary.declaration_ids == sorted(ids)

    def test_status_changed_at_is_created_at_then_latest_event(self, db_conn):
        order_id = _order(db_conn, "1")
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("UPDATE production_orders SET created_at = %s WHERE id = %s",
                        (datetime(2026, 1, 1, 8, 0), order_id))
        (summary,) = db.list_production_orders(db_conn, status=None)
        assert summary.status_changed_at == datetime(2026, 1, 1, 8, 0)

        db.set_production_status(db_conn, order_id, from_status="pending", to_status="in_progress",
                                 user_id=None)
        db.set_production_status(db_conn, order_id, from_status="in_progress", to_status="ready",
                                 user_id=None)
        with db_conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("UPDATE production_order_events SET created_at = %s WHERE to_status = 'in_progress'",
                        (datetime(2026, 1, 2, 9, 0),))
            cur.execute("UPDATE production_order_events SET created_at = %s WHERE to_status = 'ready'",
                        (datetime(2026, 1, 3, 10, 30),))
        (summary,) = db.list_production_orders(db_conn, status=None)
        assert summary.status_changed_at == datetime(2026, 1, 3, 10, 30)


class TestCountByProductionStatus:
    def test_every_key_with_right_counts_excluding_fully_voided(self, db_conn):
        _order(db_conn, "1")
        _order(db_conn, "2")
        _order(db_conn, "3", status="ready")
        _order(db_conn, "4", status="ready", plates=({"plate_number": "AAA111", "is_voided": True},))
        assert db.count_by_production_status(db_conn) == {
            "pending": 2, "in_progress": 0, "ready": 1, "delivered": 0,
        }

    def test_empty_database_has_every_key_at_zero(self, db_conn):
        assert db.count_by_production_status(db_conn) == {
            "pending": 0, "in_progress": 0, "ready": 0, "delivered": 0,
        }
