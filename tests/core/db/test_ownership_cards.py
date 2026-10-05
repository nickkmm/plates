"""Integration tests for app/core/db/ownership_cards.py. Needs a real
Postgres connection (see tests/conftest.py's `db_conn`/`clean_db`).

Exception-inside-a-block rule (tests/conftest.py's clean_db docstring):
commit() BEFORE the statement that is expected to fail, then rollback()
right after, so the connection leaves Postgres's aborted-transaction state
without discarding the setup rows."""

from __future__ import annotations

import psycopg
import pytest

from app.core import db


def _customer(db_conn, name: str = "CLIENTE UNO") -> int:
    return db.resolve_or_create_customer(db_conn, name)


def _card(db_conn, customer_id: int, license_: str = "LIC-001", plate: str = "ABC123", **kwargs) -> int:
    return db.create_ownership_card(
        db_conn, customer_id, transit_license=license_, plate_number=plate, **kwargs,
    )


class TestCreateAndGet:
    def test_create_returns_id_and_get_by_id_roundtrips_every_field(self, db_conn):
        customer_id = _customer(db_conn)
        card_id = _card(
            db_conn, customer_id, engine_number="ENG1", chassis_number="CHA1",
            vehicle_type="CARRO PARTICULAR", registered_in="MEDELLÍN", vehicle_brand="MAZDA",
        )
        card = db.get_ownership_card(db_conn, card_id)
        assert card == db.OwnershipCardRecord(
            id=card_id, customer_id=customer_id, transit_license="LIC-001",
            plate_number="ABC123", engine_number="ENG1", chassis_number="CHA1",
            vehicle_type="CARRO PARTICULAR", registered_in="MEDELLÍN", vehicle_brand="MAZDA",
        )

    def test_optional_fields_default_to_none(self, db_conn):
        card = db.get_ownership_card(db_conn, _card(db_conn, _customer(db_conn)))
        assert card is not None
        assert card.engine_number is None
        assert card.chassis_number is None
        assert card.vehicle_type is None
        assert card.registered_in is None
        assert card.vehicle_brand is None

    def test_get_by_transit_license_and_plate(self, db_conn):
        card_id = _card(db_conn, _customer(db_conn))
        by_license = db.get_ownership_card_by_transit_license(db_conn, "LIC-001")
        by_plate = db.get_ownership_card_by_plate_number(db_conn, "ABC123")
        assert by_license is not None and by_license.id == card_id
        assert by_plate is not None and by_plate.id == card_id

    def test_lookups_return_none_when_missing(self, db_conn):
        assert db.get_ownership_card(db_conn, 999999) is None
        assert db.get_ownership_card_by_transit_license(db_conn, "NOPE") is None
        assert db.get_ownership_card_by_plate_number(db_conn, "ZZZ999") is None


class TestNormalization:
    def test_licence_is_stripped_and_uppercased_and_plate_is_cleaned(self, db_conn):
        card_id = _card(db_conn, _customer(db_conn), license_="  lic 001x ", plate=" abc 123 ")
        card = db.get_ownership_card(db_conn, card_id)
        assert card is not None
        assert card.transit_license == "LIC 001X"
        assert card.plate_number == "ABC123"

    def test_lookups_normalize_their_input_too(self, db_conn):
        card_id = _card(db_conn, _customer(db_conn))
        by_license = db.get_ownership_card_by_transit_license(db_conn, " lic-001 ")
        by_plate = db.get_ownership_card_by_plate_number(db_conn, "abc 123")
        assert by_license is not None and by_license.id == card_id
        assert by_plate is not None and by_plate.id == card_id

    def test_free_text_fields_are_stripped_and_blank_becomes_none(self, db_conn):
        card_id = _card(
            db_conn, _customer(db_conn), engine_number="  E1  ", chassis_number="   ",
            vehicle_type="", registered_in=" CALI ", vehicle_brand=None,
        )
        card = db.get_ownership_card(db_conn, card_id)
        assert card is not None
        assert card.engine_number == "E1"
        assert card.chassis_number is None
        assert card.vehicle_type is None
        assert card.registered_in == "CALI"
        assert card.vehicle_brand is None


class TestConstraints:
    def test_duplicate_transit_license_raises(self, db_conn):
        customer_id = _customer(db_conn)
        _card(db_conn, customer_id, "LIC-001", "ABC123")
        db_conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            _card(db_conn, customer_id, "lic-001", "XYZ789")
        db_conn.rollback()

    def test_duplicate_plate_number_raises(self, db_conn):
        customer_id = _customer(db_conn)
        _card(db_conn, customer_id, "LIC-001", "ABC123")
        db_conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            _card(db_conn, customer_id, "LIC-002", "abc 123")
        db_conn.rollback()

    def test_nonexistent_customer_raises_foreign_key_violation(self, db_conn):
        db_conn.commit()
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _card(db_conn, 999999)
        db_conn.rollback()

    def test_same_licence_with_different_customer_still_raises(self, db_conn):
        """The two UNIQUEs are global, not per customer."""
        first = _customer(db_conn, "CLIENTE UNO")
        second = _customer(db_conn, "CLIENTE DOS")
        _card(db_conn, first, "LIC-001", "ABC123")
        db_conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            _card(db_conn, second, "LIC-001", "DEF456")
        db_conn.rollback()


class TestListAndReassign:
    def test_customer_can_own_several_cards_listed_in_id_order(self, db_conn):
        customer_id = _customer(db_conn)
        ids = [
            _card(db_conn, customer_id, "LIC-003", "CCC333"),
            _card(db_conn, customer_id, "LIC-001", "AAA111"),
            _card(db_conn, customer_id, "LIC-002", "BBB222"),
        ]
        cards = db.list_ownership_cards_for_customer(db_conn, customer_id)
        assert [c.id for c in cards] == ids == sorted(ids)

    def test_list_only_returns_that_customers_cards(self, db_conn):
        first = _customer(db_conn, "CLIENTE UNO")
        second = _customer(db_conn, "CLIENTE DOS")
        _card(db_conn, first, "LIC-001", "AAA111")
        other = _card(db_conn, second, "LIC-002", "BBB222")
        assert [c.id for c in db.list_ownership_cards_for_customer(db_conn, second)] == [other]

    def test_list_is_empty_for_customer_without_cards(self, db_conn):
        assert db.list_ownership_cards_for_customer(db_conn, _customer(db_conn)) == []

    def test_reassign_moves_the_card(self, db_conn):
        first = _customer(db_conn, "CLIENTE UNO")
        second = _customer(db_conn, "CLIENTE DOS")
        card_id = _card(db_conn, first)
        db.reassign_ownership_card(db_conn, card_id, second)
        card = db.get_ownership_card(db_conn, card_id)
        assert card is not None and card.customer_id == second
        assert db.list_ownership_cards_for_customer(db_conn, first) == []

    def test_reassign_to_nonexistent_customer_raises(self, db_conn):
        card_id = _card(db_conn, _customer(db_conn))
        db_conn.commit()
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            db.reassign_ownership_card(db_conn, card_id, 999999)
        db_conn.rollback()


class TestFindCardConflicts:
    def test_new_when_neither_exists(self, db_conn):
        customer_id = _customer(db_conn)
        result = db.find_card_conflicts(
            db_conn, customer_id=customer_id, transit_license="LIC-001", plate_number="ABC123",
        )
        assert result.status == db.CARD_STATUS_NEW == "new"
        assert result.by_transit_license is None
        assert result.by_plate_number is None

    def test_new_with_customer_id_none(self, db_conn):
        result = db.find_card_conflicts(
            db_conn, customer_id=None, transit_license="LIC-001", plate_number="ABC123",
        )
        assert result.status == "new"

    def test_same_card_when_both_match_and_owner_matches(self, db_conn):
        customer_id = _customer(db_conn)
        card_id = _card(db_conn, customer_id)
        result = db.find_card_conflicts(
            db_conn, customer_id=customer_id, transit_license=" lic-001", plate_number="abc 123",
        )
        assert result.status == "same_card"
        assert result.by_transit_license is not None and result.by_transit_license.id == card_id
        assert result.by_plate_number is not None and result.by_plate_number.id == card_id

    def test_other_owner_when_same_card_belongs_to_another_customer(self, db_conn):
        owner = _customer(db_conn, "CLIENTE UNO")
        other = _customer(db_conn, "CLIENTE DOS")
        _card(db_conn, owner)
        result = db.find_card_conflicts(
            db_conn, customer_id=other, transit_license="LIC-001", plate_number="ABC123",
        )
        assert result.status == "other_owner"

    def test_other_owner_when_customer_id_is_none_and_card_exists(self, db_conn):
        _card(db_conn, _customer(db_conn))
        result = db.find_card_conflicts(
            db_conn, customer_id=None, transit_license="LIC-001", plate_number="ABC123",
        )
        assert result.status == "other_owner"

    def test_data_mismatch_licence_exists_with_a_different_plate(self, db_conn):
        customer_id = _customer(db_conn)
        _card(db_conn, customer_id)
        result = db.find_card_conflicts(
            db_conn, customer_id=customer_id, transit_license="LIC-001", plate_number="ZZZ999",
        )
        assert result.status == "data_mismatch"
        assert result.by_transit_license is not None
        assert result.by_plate_number is None

    def test_data_mismatch_plate_exists_with_a_different_licence(self, db_conn):
        customer_id = _customer(db_conn)
        _card(db_conn, customer_id)
        result = db.find_card_conflicts(
            db_conn, customer_id=customer_id, transit_license="LIC-999", plate_number="ABC123",
        )
        assert result.status == "data_mismatch"
        assert result.by_transit_license is None
        assert result.by_plate_number is not None

    def test_data_mismatch_when_licence_and_plate_belong_to_different_cards(self, db_conn):
        customer_id = _customer(db_conn)
        _card(db_conn, customer_id, "LIC-001", "AAA111")
        _card(db_conn, customer_id, "LIC-002", "BBB222")
        result = db.find_card_conflicts(
            db_conn, customer_id=customer_id, transit_license="LIC-001", plate_number="BBB222",
        )
        assert result.status == "data_mismatch"
        assert result.by_transit_license is not None and result.by_plate_number is not None
        assert result.by_transit_license.id != result.by_plate_number.id

    def test_data_mismatch_wins_over_other_owner(self, db_conn):
        """Licence matches a card of ANOTHER customer but the plate differs:
        both mismatch and a different owner apply -- mismatch takes precedence."""
        owner = _customer(db_conn, "CLIENTE UNO")
        other = _customer(db_conn, "CLIENTE DOS")
        _card(db_conn, owner)
        result = db.find_card_conflicts(
            db_conn, customer_id=other, transit_license="LIC-001", plate_number="ZZZ999",
        )
        assert result.status == "data_mismatch"

    def test_data_mismatch_across_cards_of_different_owners(self, db_conn):
        first = _customer(db_conn, "CLIENTE UNO")
        second = _customer(db_conn, "CLIENTE DOS")
        _card(db_conn, first, "LIC-001", "AAA111")
        _card(db_conn, second, "LIC-002", "BBB222")
        result = db.find_card_conflicts(
            db_conn, customer_id=first, transit_license="LIC-001", plate_number="BBB222",
        )
        assert result.status == "data_mismatch"

    def test_find_card_conflicts_never_writes(self, db_conn):
        db.find_card_conflicts(
            db_conn, customer_id=None, transit_license="LIC-001", plate_number="ABC123",
        )
        assert db.get_ownership_card_by_transit_license(db_conn, "LIC-001") is None


class TestClassifyCardLookup:
    """The pure decision, without a DB (every combination)."""

    @staticmethod
    def _record(card_id: int, customer_id: int) -> db.OwnershipCardRecord:
        return db.OwnershipCardRecord(
            id=card_id, customer_id=customer_id, transit_license="L", plate_number="P",
            engine_number=None, chassis_number=None, vehicle_type=None,
            registered_in=None, vehicle_brand=None,
        )

    def test_every_combination(self):
        mine = self._record(1, 10)
        theirs = self._record(2, 20)
        cases = [
            (10, None, None, "new"),
            (None, None, None, "new"),
            (10, mine, mine, "same_card"),
            (10, theirs, theirs, "other_owner"),
            (None, mine, mine, "other_owner"),
            (10, mine, None, "data_mismatch"),
            (10, None, mine, "data_mismatch"),
            (10, theirs, None, "data_mismatch"),
            (10, None, theirs, "data_mismatch"),
            (10, mine, theirs, "data_mismatch"),
            (10, theirs, mine, "data_mismatch"),
            (None, mine, None, "data_mismatch"),
        ]
        for customer_id, by_license, by_plate, expected in cases:
            assert db.classify_card_lookup(customer_id, by_license, by_plate) == expected
