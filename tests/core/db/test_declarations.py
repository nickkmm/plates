"""Integration tests for app/core/db/declarations.py. Needs a real Postgres
connection (see tests/conftest.py's `db_conn`/`clean_db`)."""

from __future__ import annotations

from datetime import datetime, timedelta

import psycopg
import pytest

from app.core import db
from app.core.db.declarations import STAGES, declaration_stage, requires_notarization
from tests.conftest import TEST_USERNAME

_DECLARANT = {"name": "ANA", "document_number": "123"}


def _insert(db_conn, **overrides):
    kwargs = {
        "acting_as": "PROPIETARIO", "status": "pending_review",
        "submitted_data": {"acting_as": "PROPIETARIO", "declarant": _DECLARANT, "cards": []},
    }
    kwargs.update(overrides)
    return db.insert_declaration(db_conn, **kwargs)


def _insert_card(db_conn, declaration_id: int, position: int = 0, **overrides):
    kwargs = {
        "position": position, "reason": "DETERIORO", "plate_quantity": 1,
        "plate_number": f"ABC{position:03d}", "transit_license": f"LIC-{position}",
        "owner_data": _DECLARANT,
    }
    kwargs.update(overrides)
    return db.insert_declaration_card(db_conn, declaration_id, **kwargs)


def _set_created_at(db_conn, declaration_id: int, when: datetime) -> None:
    with db_conn.cursor() as cur:
        cur.execute("UPDATE declarations SET created_at = %s WHERE id = %s", (when, declaration_id))


class TestInsertAndGet:
    def test_roundtrips_every_field(self, db_conn):
        gestor_id = db.resolve_or_create_customer(db_conn, "GESTORIA")
        accepted = datetime(2026, 1, 2, 3, 4, 5)
        declaration_id = _insert(
            db_conn, status="approved", review_flags=["a", "b"], gestor_id=gestor_id,
            declarant_signature_path="sig.png", notarized_pdf_path="n.pdf", submitter_ip="1.2.3.4",
            user_agent="UA", accepted_terms_at=accepted, acting_as="GESTOR",
        )
        record = db.get_declaration(db_conn, declaration_id)
        assert record is not None
        assert record.acting_as == "GESTOR"
        assert record.status == "approved"
        assert record.review_flags == ["a", "b"]
        assert record.gestor_id == gestor_id
        assert (record.declarant_signature_path, record.notarized_pdf_path) == ("sig.png", "n.pdf")
        assert (record.submitter_ip, record.user_agent) == ("1.2.3.4", "UA")
        assert record.accepted_terms_at == accepted
        assert record.submitted_data["declarant"]["name"] == "ANA"
        assert record.created_at is not None
        assert record.reviewed_by is None and record.reviewed_at is None

    def test_defaults(self, db_conn):
        record = db.get_declaration(db_conn, _insert(db_conn))
        assert record is not None
        assert record.review_flags == []
        assert record.gestor_id is None
        assert record.declarant_signature_path is None

    def test_get_unknown_returns_none(self, db_conn):
        assert db.get_declaration(db_conn, 999999) is None


class TestDeclarationCards:
    def test_roundtrips_every_field(self, db_conn):
        owner_id = db.resolve_or_create_customer(db_conn, "ANA")
        card_id = db.create_ownership_card(db_conn, owner_id, transit_license="L1", plate_number="ABC123")
        declaration_id = _insert(db_conn)
        row_id = db.insert_declaration_card(
            db_conn, declaration_id, position=0, reason="PÉRDIDA", plate_quantity=2,
            plate_number="ABC123", transit_license="L1", engine_number="E", chassis_number="C",
            vehicle_type="CARRO PARTICULAR", registered_in="BOGOTÁ", vehicle_brand="KIA",
            owner_relation="DUEÑO", owner_data={"name": "ANA", "phone": "3"},
            review_flags=["x"], customer_id=owner_id, ownership_card_id=card_id,
        )
        (row,) = db.list_declaration_cards(db_conn, declaration_id)
        assert row == db.DeclarationCardRecord(
            id=row_id, declaration_id=declaration_id, position=0, reason="PÉRDIDA", plate_quantity=2,
            plate_number="ABC123", transit_license="L1", engine_number="E", chassis_number="C",
            vehicle_type="CARRO PARTICULAR", registered_in="BOGOTÁ", vehicle_brand="KIA",
            owner_relation="DUEÑO", owner_data={"name": "ANA", "phone": "3"},
            customer_id=owner_id, ownership_card_id=card_id, review_flags=["x"],
        )

    def test_defaults_are_null_links_and_no_flags(self, db_conn):
        declaration_id = _insert(db_conn)
        _insert_card(db_conn, declaration_id)
        (row,) = db.list_declaration_cards(db_conn, declaration_id)
        assert (row.customer_id, row.ownership_card_id, row.review_flags) == (None, None, [])
        assert (row.engine_number, row.owner_relation) == (None, None)

    def test_listed_in_position_order_not_insertion_order(self, db_conn):
        declaration_id = _insert(db_conn)
        _insert_card(db_conn, declaration_id, 2)
        _insert_card(db_conn, declaration_id, 0)
        _insert_card(db_conn, declaration_id, 1)
        assert [c.position for c in db.list_declaration_cards(db_conn, declaration_id)] == [0, 1, 2]

    def test_only_the_cards_of_that_declaration(self, db_conn):
        first, second = _insert(db_conn), _insert(db_conn)
        _insert_card(db_conn, first, 0)
        _insert_card(db_conn, second, 0, plate_number="ZZZ999", transit_license="Z")
        assert [c.plate_number for c in db.list_declaration_cards(db_conn, second)] == ["ZZZ999"]
        assert db.list_declaration_cards(db_conn, 999999) == []

    def test_position_is_unique_per_declaration(self, db_conn):
        declaration_id = _insert(db_conn)
        _insert_card(db_conn, declaration_id, 0)
        db_conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert_card(db_conn, declaration_id, 0)
        db_conn.rollback()

    def test_deleting_a_declaration_deletes_its_cards(self, db_conn):
        declaration_id = _insert(db_conn)
        _insert_card(db_conn, declaration_id)
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM declarations WHERE id = %s", (declaration_id,))
        assert db.list_declaration_cards(db_conn, declaration_id) == []

    def test_list_cards_for_declarations_groups_in_one_query(self, db_conn):
        first, second, empty = _insert(db_conn), _insert(db_conn), _insert(db_conn)
        _insert_card(db_conn, first, 1)
        _insert_card(db_conn, first, 0, plate_number="AAA111", transit_license="A")
        _insert_card(db_conn, second, 0, plate_number="BBB222", transit_license="B")
        grouped = db.list_cards_for_declarations(db_conn, [first, second, empty, 999999])
        assert set(grouped) == {first, second, empty, 999999}
        assert [c.position for c in grouped[first]] == [0, 1]
        assert [c.plate_number for c in grouped[second]] == ["BBB222"]
        assert grouped[empty] == [] and grouped[999999] == []
        assert db.list_cards_for_declarations(db_conn, []) == {}

    def test_set_declaration_card_links(self, db_conn):
        owner_id = db.resolve_or_create_customer(db_conn, "ANA")
        card_id = db.create_ownership_card(db_conn, owner_id, transit_license="L1", plate_number="ABC123")
        declaration_id = _insert(db_conn)
        row_id = _insert_card(db_conn, declaration_id)
        other_id = _insert_card(db_conn, declaration_id, 1)
        db.set_declaration_card_links(db_conn, row_id, customer_id=owner_id, ownership_card_id=card_id)
        first, second = db.list_declaration_cards(db_conn, declaration_id)
        assert (first.customer_id, first.ownership_card_id) == (owner_id, card_id)
        assert (second.id, second.customer_id) == (other_id, None)


class TestListDeclarations:
    def test_pending_first_then_newest(self, db_conn):
        base = datetime(2026, 3, 1, 12, 0)
        approved_new = _insert(db_conn, status="approved")
        pending_old = _insert(db_conn, status="pending_review")
        pending_new = _insert(db_conn, status="pending_review")
        rejected = _insert(db_conn, status="rejected")
        _set_created_at(db_conn, approved_new, base + timedelta(days=5))
        _set_created_at(db_conn, pending_old, base)
        _set_created_at(db_conn, pending_new, base + timedelta(days=1))
        _set_created_at(db_conn, rejected, base + timedelta(days=9))
        ids = [d.id for d in db.list_declarations(db_conn)]
        assert ids == [pending_new, pending_old, rejected, approved_new]

    def test_filter_by_stage(self, db_conn):
        ids = _stage_fixtures(db_conn)
        assert [d.id for d in db.list_declarations(db_conn, stage="to_review")] == [ids["to_review"]]
        assert [d.id for d in db.list_declarations(db_conn, stage="rejected")] == [ids["rejected"]]
        assert [d.id for d in db.list_declarations(db_conn, stage="attended")] == [ids["attended"]]
        assert [d.id for d in db.list_declarations(db_conn, stage="awaiting_notarization")] == [ids["awaiting"]]
        assert {d.id for d in db.list_declarations(db_conn, stage="ready")} == {
            ids["ready_plain"], ids["ready_notarized"], ids["ready_plain_no_cards"],
        }
        assert len(db.list_declarations(db_conn)) == 7           # None = every stage

    def test_every_listed_declaration_matches_its_derived_stage(self, db_conn):
        _stage_fixtures(db_conn)
        for stage in STAGES:
            listed = db.list_declarations(db_conn, stage=stage)
            assert listed, stage
            for d in listed:
                assert declaration_stage(d, db.list_declaration_cards(db_conn, d.id)) == stage

    def test_unknown_stage_raises(self, db_conn):
        with pytest.raises(ValueError):
            db.list_declarations(db_conn, stage="bogus")

    def test_stage_combines_with_query_and_keeps_ordering(self, db_conn):
        base = datetime(2026, 3, 1, 12, 0)
        old = _insert(db_conn, status="approved")
        new = _insert(db_conn, status="approved")
        _set_created_at(db_conn, old, base)
        _set_created_at(db_conn, new, base + timedelta(days=1))
        assert [d.id for d in db.list_declarations(db_conn, stage="ready", query="ana")] == [new, old]
        assert db.list_declarations(db_conn, stage="ready", query="nobody") == []
        assert db.list_declarations(db_conn, stage="attended", query="ana") == []

    def test_query_matches_declarant_case_insensitive(self, db_conn):
        match = _insert(db_conn, submitted_data={
            "declarant": {"name": "Gestoria Sur", "document_number": "555"}, "cards": []})
        _insert(db_conn)  # the default one, must never match these terms
        for term in ("gestoria", "555"):
            assert [d.id for d in db.list_declarations(db_conn, query=term)] == [match], term

    def test_query_matches_any_card_of_the_declaration(self, db_conn):
        match = _insert(db_conn)
        _insert_card(db_conn, match, 0, plate_number="AAA111", transit_license="LIC-1",
                     owner_data={"name": "Pedro Gil", "document_number": "11"})
        _insert_card(db_conn, match, 1, plate_number="XYZ789", transit_license="LIC-777",
                     owner_data={"name": "Maria Lopez", "document_number": "98765"})
        other = _insert(db_conn)
        _insert_card(db_conn, other, 0, plate_number="QQQ000", transit_license="OTHER",
                     owner_data={"name": "Nadie", "document_number": "0"})
        for term in ("xyz789", "lic-777", "maria", "98765", "aaa111", "pedro", "lic-1"):
            assert [d.id for d in db.list_declarations(db_conn, query=term)] == [match], term

    def test_declaration_with_two_matching_cards_is_listed_once(self, db_conn):
        declaration_id = _insert(db_conn)
        _insert_card(db_conn, declaration_id, 0, plate_number="ABC111", transit_license="L1")
        _insert_card(db_conn, declaration_id, 1, plate_number="ABC222", transit_license="L2")
        assert [d.id for d in db.list_declarations(db_conn, query="abc")] == [declaration_id]

    def test_query_matches_linked_rows(self, db_conn):
        customer_id = db.resolve_or_create_customer(db_conn, "JUAN PEREZ", document_number="4242")
        card_id = db.create_ownership_card(db_conn, customer_id, transit_license="LNK-1", plate_number="LNK123")
        gestor_id = db.resolve_or_create_customer(db_conn, "LA GESTORIA", document_number="9090")
        linked = _insert(db_conn, status="approved", gestor_id=gestor_id,
                         submitted_data={"declarant": {"name": "otra cosa"}})
        _insert_card(db_conn, linked, 0, plate_number="TTT999", transit_license="T9",
                     owner_data={"name": "otra cosa"}, customer_id=customer_id, ownership_card_id=card_id)
        for term in ("juan perez", "4242", "lnk123", "lnk-1", "la gestoria", "9090"):
            assert [d.id for d in db.list_declarations(db_conn, query=term)] == [linked], term

    def test_like_wildcards_typed_by_the_user_match_literally(self, db_conn):
        plain = _insert(db_conn)
        _insert_card(db_conn, plain)
        weird = _insert(db_conn)
        _insert_card(db_conn, weird, 0, plate_number="ZZZ999", transit_license="L",
                     owner_data={"name": "100% A_B", "document_number": "1"})
        assert [d.id for d in db.list_declarations(db_conn, query="%")] == [weird]
        assert [d.id for d in db.list_declarations(db_conn, query="_")] == [weird]
        assert [d.id for d in db.list_declarations(db_conn, query="a_b")] == [weird]
        assert [d.id for d in db.list_declarations(db_conn, query="\\")] == []
        assert plain not in [d.id for d in db.list_declarations(db_conn, query="%")]

    def test_query_and_stage_combine_and_blank_query_ignored(self, db_conn):
        a = _insert(db_conn, status="approved")
        b = _insert(db_conn, status="pending_review")
        assert [d.id for d in db.list_declarations(db_conn, query="ana", stage="ready")] == [a]
        assert {d.id for d in db.list_declarations(db_conn, query="  ")} == {a, b}


class TestCountRecentByIp:
    def test_counts_only_that_ip_since_the_cutoff(self, db_conn):
        since = datetime(2026, 5, 1, 12, 0)
        old = _insert(db_conn, submitter_ip="9.9.9.9")
        boundary = _insert(db_conn, submitter_ip="9.9.9.9")
        recent = _insert(db_conn, submitter_ip="9.9.9.9")
        other_ip = _insert(db_conn, submitter_ip="8.8.8.8")
        _insert(db_conn, submitter_ip=None)
        _set_created_at(db_conn, old, since - timedelta(seconds=1))
        _set_created_at(db_conn, boundary, since)
        _set_created_at(db_conn, recent, since + timedelta(hours=1))
        _set_created_at(db_conn, other_ip, since + timedelta(hours=1))
        assert db.count_recent_by_ip(db_conn, "9.9.9.9", since=since) == 2
        assert db.count_recent_by_ip(db_conn, "8.8.8.8", since=since) == 1
        assert db.count_recent_by_ip(db_conn, "7.7.7.7", since=since) == 0


class TestSetDeclarationReview:
    def test_records_review_and_gestor(self, db_conn):
        user = db.get_user_by_username(db_conn, TEST_USERNAME)
        assert user is not None
        gestor_id = db.resolve_or_create_customer(db_conn, "GESTORIA")
        declaration_id = _insert(db_conn)
        db.set_declaration_review(db_conn, declaration_id, status="approved", reviewed_by=user.id,
                                  note="ok", gestor_id=gestor_id)
        record = db.get_declaration(db_conn, declaration_id)
        assert record is not None
        assert record.status == "approved"
        assert record.reviewed_by == user.id
        assert record.reviewed_at is not None
        assert record.review_note == "ok"
        assert record.gestor_id == gestor_id


class TestListByReason:
    def test_reason_matches_declarations_with_at_least_one_card_of_it(self, db_conn):
        loss_only = _insert(db_conn, status="approved")
        _insert_card(db_conn, loss_only, 0, reason="PÉRDIDA")
        deterioration_only = _insert(db_conn, status="approved")
        _insert_card(db_conn, deterioration_only, 0, reason="DETERIORO")
        mixed = _insert(db_conn, status="pending_review")
        _insert_card(db_conn, mixed, 0, reason="DETERIORO")
        _insert_card(db_conn, mixed, 1, reason="PÉRDIDA")
        no_cards = _insert(db_conn, status="approved")

        assert {d.id for d in db.list_declarations(db_conn)} == {loss_only, deterioration_only, mixed, no_cards}
        assert {d.id for d in db.list_declarations(db_conn, reason="PÉRDIDA")} == {loss_only, mixed}
        assert {d.id for d in db.list_declarations(db_conn, reason="DETERIORO")} == {deterioration_only, mixed}
        assert db.list_declarations(db_conn, reason="OTRO") == []

    def test_a_mixed_declaration_is_listed_once(self, db_conn):
        mixed = _insert(db_conn)
        for position in range(3):
            _insert_card(db_conn, mixed, position, reason="PÉRDIDA")
        assert [d.id for d in db.list_declarations(db_conn, reason="PÉRDIDA")] == [mixed]

    def test_reason_combines_with_stage_and_query(self, db_conn):
        approved = _insert(db_conn, status="approved", submitted_data={"declarant": {"name": "ANA"}})
        _insert_card(db_conn, approved, 0, reason="PÉRDIDA", plate_number="AAA111")
        pending = _insert(db_conn, status="pending_review", submitted_data={"declarant": {"name": "BETO"}})
        _insert_card(db_conn, pending, 0, reason="PÉRDIDA", plate_number="BBB222")
        other = _insert(db_conn, status="approved", submitted_data={"declarant": {"name": "CARLA"}})
        _insert_card(db_conn, other, 0, reason="DETERIORO", plate_number="CCC333")

        assert [d.id for d in db.list_declarations(db_conn, reason="PÉRDIDA", stage="awaiting_notarization")] == [approved]
        assert db.list_declarations(db_conn, reason="PÉRDIDA", stage="ready") == []
        assert [d.id for d in db.list_declarations(db_conn, reason="PÉRDIDA", query="bbb")] == [pending]
        assert db.list_declarations(db_conn, reason="PÉRDIDA", query="carla") == []
        assert db.list_declarations(db_conn, reason="PÉRDIDA", stage="attended") == []


def _stage_fixtures(db_conn) -> dict[str, int]:
    """One declaration per situation of the derived stage (7 rows, 5 stages)."""
    from datetime import date

    from app.core.models import ExtractedRow

    order_id = db.insert_order(db_conn, row=ExtractedRow(
        order_number="9001", order_date=date(2026, 9, 1), requested_by="ANA",
        plate_type="CARRO PARTICULAR"), customer_id=None, created_by=None)
    ids: dict[str, int] = {}
    ids["to_review"] = _insert(db_conn, status="pending_review")
    _insert_card(db_conn, ids["to_review"], reason="PÉRDIDA")
    ids["rejected"] = _insert(db_conn, status="rejected")
    ids["ready_plain"] = _insert(db_conn, status="approved")
    _insert_card(db_conn, ids["ready_plain"], reason="DETERIORO")
    ids["awaiting"] = _insert(db_conn, status="approved")
    _insert_card(db_conn, ids["awaiting"], 0, reason="DETERIORO")
    _insert_card(db_conn, ids["awaiting"], 1, reason="PÉRDIDA")
    ids["ready_notarized"] = _insert(db_conn, status="approved")
    _insert_card(db_conn, ids["ready_notarized"], reason="PÉRDIDA")
    db.set_declaration_notarized_received(db_conn, ids["ready_notarized"], received_by=None)
    ids["attended"] = _insert(db_conn, status="approved")
    _insert_card(db_conn, ids["attended"], reason="PÉRDIDA")          # even un-notarized: attended wins
    db.set_declaration_production_order(db_conn, ids["attended"], order_id)
    ids["ready_plain_no_cards"] = _insert(db_conn, status="approved")
    return ids


class TestDeclarationStage:
    def test_the_five_stages(self, db_conn):
        ids = _stage_fixtures(db_conn)
        expected = {
            "to_review": "to_review", "rejected": "rejected", "attended": "attended",
            "awaiting": "awaiting_notarization", "ready_plain": "ready",
            "ready_notarized": "ready", "ready_plain_no_cards": "ready",
        }
        for key, stage in expected.items():
            record = db.get_declaration(db_conn, ids[key])
            assert record is not None
            assert declaration_stage(record, db.list_declaration_cards(db_conn, ids[key])) == stage, key

    def test_constants(self):
        assert STAGES == (
            "to_review", "awaiting_notarization", "ready", "attended", "rejected",
        )


class TestRequiresNotarization:
    @staticmethod
    def _card(reason: str) -> db.DeclarationCardRecord:
        return db.DeclarationCardRecord(
            id=1, declaration_id=1, position=0, reason=reason, plate_quantity=1, plate_number="ABC123",
            transit_license="L", engine_number=None, chassis_number=None, vehicle_type=None,
            registered_in=None, vehicle_brand=None, owner_relation=None, owner_data={},
            customer_id=None, ownership_card_id=None, review_flags=[],
        )

    def test_any_loss_card_requires_it(self):


        assert requires_notarization([self._card("PÉRDIDA")])
        assert requires_notarization([self._card("DETERIORO"), self._card("PÉRDIDA")])

    def test_deterioration_only_or_no_cards_does_not(self):


        assert not requires_notarization([self._card("DETERIORO"), self._card("DETERIORO")])
        assert not requires_notarization([])


class TestSetNotarizedReceived:
    def test_marks_with_user_and_time_and_clears(self, db_conn):
        user = db.get_user_by_username(db_conn, TEST_USERNAME)
        assert user is not None
        declaration_id = _insert(db_conn, status="approved")
        record = db.get_declaration(db_conn, declaration_id)
        assert record is not None
        assert record.notarized_received_at is None and record.notarized_received_by is None

        db.set_declaration_notarized_received(db_conn, declaration_id, received_by=user.id)
        record = db.get_declaration(db_conn, declaration_id)
        assert record is not None
        assert record.notarized_received_at is not None and record.notarized_received_by == user.id

        db.set_declaration_notarized_received(db_conn, declaration_id, received_by=None, received=False)
        record = db.get_declaration(db_conn, declaration_id)
        assert record is not None
        assert record.notarized_received_at is None and record.notarized_received_by is None

    def test_only_touches_its_own_declaration(self, db_conn):
        mine = _insert(db_conn, status="approved")
        other = _insert(db_conn, status="approved")
        db.set_declaration_notarized_received(db_conn, mine, received_by=None)
        record = db.get_declaration(db_conn, other)
        assert record is not None and record.notarized_received_at is None
