"""Tests for app/core/production.py -- next_status/previous_status (pure) and
advance_order/revert_order (need a real Postgres connection, see
tests/conftest.py's `db_conn`/`clean_db`)."""

from __future__ import annotations

from datetime import date

import pytest
from psycopg.rows import tuple_row

from app.core import db
from app.core.production import advance_order, next_status, previous_status, revert_order
from app.shared.models import ExtractedRow
from tests.conftest import PRODUCCION_USERNAME, TEST_USERNAME

_STALE = "El estado de la orden cambió mientras tanto. Revisa el estado actual e inténtalo de nuevo."


def _new_order(db_conn) -> int:
    return db.insert_order(
        db_conn,
        row=ExtractedRow(order_number="500", order_date=date(2026, 1, 1), requested_by="PEDRO",
                         plate_type="CARRO PARTICULAR"),
        customer_id=None, created_by=None,
    )


def _set_status(db_conn, order_id: int, status: str) -> None:
    with db_conn.cursor() as cur:
        cur.execute("UPDATE production_orders SET production_status = %s WHERE id = %s", (status, order_id))


def _user_id(db_conn, username: str) -> int:
    user = db.get_user_by_username(db_conn, username)
    assert user is not None
    return user.id


def _event_user_ids(db_conn, order_id: int) -> list[int | None]:
    with db_conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT user_id FROM production_order_events WHERE order_id = %s ORDER BY id", (order_id,))
        return [row[0] for row in cur.fetchall()]


class TestNextAndPreviousStatus:
    def test_next(self):
        assert next_status("pending") == "in_progress"
        assert next_status("in_progress") == "ready"
        assert next_status("ready") == "delivered"
        assert next_status("delivered") is None
        assert next_status("unknown") is None

    def test_previous(self):
        assert previous_status("delivered") == "ready"
        assert previous_status("ready") == "in_progress"
        assert previous_status("in_progress") == "pending"
        assert previous_status("pending") is None
        assert previous_status("unknown") is None


class TestAdvanceOrder:
    def test_advances_through_the_whole_chain_recording_each_step(self, db_conn):
        order_id = _new_order(db_conn)
        user_id = _user_id(db_conn, TEST_USERNAME)
        steps = [("pending", "in_progress"), ("in_progress", "ready"), ("ready", "delivered")]
        for n, (from_status, to_status) in enumerate(steps, start=1):
            assert advance_order(db_conn, order_id, user_id, expected_status=from_status) == to_status
            assert db.get_production_status(db_conn, order_id) == to_status
            events = db.list_production_events(db_conn, order_id)
            assert len(events) == n
            assert (events[-1].from_status, events[-1].to_status) == (from_status, to_status)
            assert events[-1].username == TEST_USERNAME
        assert _event_user_ids(db_conn, order_id) == [user_id] * 3

    def test_user_id_may_be_none(self, db_conn):
        order_id = _new_order(db_conn)
        advance_order(db_conn, order_id, None, expected_status="pending")
        assert _event_user_ids(db_conn, order_id) == [None]


class TestRevertOrder:
    def test_revert_from_ready_goes_back_to_in_progress(self, db_conn):
        order_id = _new_order(db_conn)
        _set_status(db_conn, order_id, "ready")
        user_id = _user_id(db_conn, PRODUCCION_USERNAME)
        assert revert_order(db_conn, order_id, user_id, expected_status="ready") == "in_progress"
        assert db.get_production_status(db_conn, order_id) == "in_progress"
        events = db.list_production_events(db_conn, order_id)
        assert [(e.from_status, e.to_status, e.username) for e in events] == [
            ("ready", "in_progress", PRODUCCION_USERNAME),
        ]
        assert _event_user_ids(db_conn, order_id) == [user_id]

    def test_revert_from_delivered_goes_back_to_ready(self, db_conn):
        order_id = _new_order(db_conn)
        _set_status(db_conn, order_id, "delivered")
        assert revert_order(db_conn, order_id, None, expected_status="delivered") == "ready"
        assert db.get_production_status(db_conn, order_id) == "ready"


class TestErrors:
    """Every error raises ValueError with the exact Spanish message and
    changes nothing (status and event count)."""

    @staticmethod
    def _assert_unchanged(db_conn, order_id: int, status: str, events: int) -> None:
        assert db.get_production_status(db_conn, order_id) == status
        assert len(db.list_production_events(db_conn, order_id)) == events

    @pytest.mark.parametrize("move", [advance_order, revert_order])
    def test_nonexistent_order(self, db_conn, move):
        with pytest.raises(ValueError) as excinfo:
            move(db_conn, 999999, None, expected_status="in_progress")
        assert str(excinfo.value) == "La orden no existe."

    def test_advance_delivered(self, db_conn):
        order_id = _new_order(db_conn)
        _set_status(db_conn, order_id, "delivered")
        with pytest.raises(ValueError) as excinfo:
            advance_order(db_conn, order_id, None, expected_status="delivered")
        assert str(excinfo.value) == "La orden ya fue entregada."
        self._assert_unchanged(db_conn, order_id, "delivered", 0)

    def test_revert_pending(self, db_conn):
        order_id = _new_order(db_conn)
        with pytest.raises(ValueError) as excinfo:
            revert_order(db_conn, order_id, None, expected_status="pending")
        assert str(excinfo.value) == "La orden todavía no ha empezado."
        self._assert_unchanged(db_conn, order_id, "pending", 0)

    def test_stale_expected_status_on_advance(self, db_conn):
        """Two people click "advance" on the same pending order: the second
        click still says pending, so it must fail and change nothing."""
        order_id = _new_order(db_conn)
        assert advance_order(db_conn, order_id, None, expected_status="pending") == "in_progress"
        with pytest.raises(ValueError) as excinfo:
            advance_order(db_conn, order_id, None, expected_status="pending")
        assert str(excinfo.value) == _STALE
        self._assert_unchanged(db_conn, order_id, "in_progress", 1)

    def test_stale_expected_status_on_revert(self, db_conn):
        order_id = _new_order(db_conn)
        _set_status(db_conn, order_id, "ready")
        assert revert_order(db_conn, order_id, None, expected_status="ready") == "in_progress"
        with pytest.raises(ValueError) as excinfo:
            revert_order(db_conn, order_id, None, expected_status="ready")
        assert str(excinfo.value) == _STALE
        self._assert_unchanged(db_conn, order_id, "in_progress", 1)

    @pytest.mark.parametrize("move", [advance_order, revert_order])
    def test_unknown_expected_status(self, db_conn, move):
        order_id = _new_order(db_conn)
        with pytest.raises(ValueError) as excinfo:
            move(db_conn, order_id, None, expected_status="bogus")
        assert str(excinfo.value) == _STALE
        self._assert_unchanged(db_conn, order_id, "pending", 0)

    def test_conditional_update_that_changes_nothing(self, db_conn, monkeypatch):
        """The status read matches, but the conditional UPDATE loses the race
        (another session moved it in between): still the stale message."""
        order_id = _new_order(db_conn)
        monkeypatch.setattr(db, "set_production_status", lambda *args, **kwargs: False)
        with pytest.raises(ValueError) as excinfo:
            advance_order(db_conn, order_id, None, expected_status="pending")
        assert str(excinfo.value) == _STALE
        self._assert_unchanged(db_conn, order_id, "pending", 0)
