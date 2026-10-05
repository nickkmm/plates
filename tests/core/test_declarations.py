"""Tests for app/core/declarations.py: validation (pure) and the submission /
review rules (need Postgres, see tests/conftest.py's `db_conn`).

All dates are fixed (`TODAY`), never the wall clock.

Exception-inside-a-block rule (tests/conftest.py's clean_db docstring):
commit() BEFORE the statement that is expected to fail, then rollback()
right after, so the connection leaves the aborted-transaction state without
discarding the setup rows. The ValueErrors raised by approve/reject happen
before any write, so no rollback is needed for them."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from app.core import db
from app.core import declarations as decl
from app.core.declarations import DeclarationInput, PersonInput, VehicleInput
from tests.conftest import TEST_USERNAME
from tests.core.db._helpers import _insert_order, make_row

TODAY = date(2026, 10, 5)


def _owner(**kw: str) -> PersonInput:
    base = {"name": "MARIA LOPEZ", "document_type": "CÉDULA", "document_number": "1020304050",
                "address": "CRA 1 # 2-3", "neighborhood": "CENTRO", "municipality": "MEDELLÍN",
                "phone": "3001112233", "email": "maria@example.com"}
    base.update(kw)
    return PersonInput(**base)


def _gestor(**kw: str) -> PersonInput:
    base = {"name": "GESTORIA SUR", "document_type": "NIT", "document_number": "900123456",
                "address": "CL 5 # 6-7", "neighborhood": "LAURELES", "municipality": "MEDELLÍN",
                "phone": "3109998877", "email": ""}
    base.update(kw)
    return PersonInput(**base)


def _vehicle(**kw: str) -> VehicleInput:
    base = {"plate_number": "ABC123", "transit_license": "LIC-001", "engine_number": "ENG1",
                "chassis_number": "CHA1", "vehicle_type": "CARRO PARTICULAR", "registered_in": "MEDELLÍN",
                "vehicle_brand": "MAZDA"}
    base.update(kw)
    return VehicleInput(**base)


def _input(**kw) -> DeclarationInput:
    base = {"reason": "DETERIORO", "plate_quantity": 1, "acting_as": "PROPIETARIO", "owner": _owner(),
                "vehicle": _vehicle(), "owner_signature_file": "owner.png", "submitter_ip": "1.1.1.1",
                "user_agent": "UA", "accepted_terms": True, "submitted_data": {"form": "raw"}}
    base.update(kw)
    return DeclarationInput(**base)


def _gestor_input(**kw) -> DeclarationInput:
    base = {"acting_as": "GESTOR", "owner_relation": "DUEÑO", "gestor": _gestor(),
                "gestor_signature_file": "gestor.png"}
    base.update(kw)
    return _input(**base)


def _reviewer_id(db_conn) -> int:
    user = db.get_user_by_username(db_conn, TEST_USERNAME)
    assert user is not None
    return user.id


def _count(db_conn, table: str) -> int:
    with db_conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")
        return cur.fetchone()["n"]


def _fabricate(db_conn, plate_number: str, order_date: date, **row_kw) -> None:
    order_id = _insert_order(db_conn, order_number=f"O-{plate_number}-{order_date}",
                             order_date=order_date, requested_by="PEDRO")
    db.insert_plate(db_conn, order_id=order_id, row=make_row(plate_number=plate_number, **row_kw),
                    is_possible_duplicate=False)


def _existing_client(db_conn, name: str = "MARIA LOPEZ", type_: str | None = "CLIENTE") -> int:
    return db.resolve_or_create_customer(db_conn, name, type=type_, phone="PREVIO")


# ---------------------------------------------------------------- validation

class TestValidate:
    def test_valid_input_has_no_errors(self):
        assert decl.validate_declaration(_input()) == []
        assert decl.validate_declaration(_gestor_input()) == []

    def test_vocabulary_is_case_and_accent_insensitive(self):
        assert decl.validate_declaration(_input(reason="deterioro", acting_as="propietario")) == []

    @pytest.mark.parametrize("reason", ["", "OTROS", "ROBO"])
    def test_reason_must_be_deterioro_or_perdida(self, reason):
        assert any("motivo" in e for e in decl.validate_declaration(_input(reason=reason)))

    @pytest.mark.parametrize("qty", [0, -1])
    def test_quantity_at_least_one(self, qty):
        assert any("cantidad" in e for e in decl.validate_declaration(_input(plate_quantity=qty)))

    def test_acting_as_required_and_valid(self):
        assert any("actúa" in e for e in decl.validate_declaration(_input(acting_as="")))
        assert any("actúa" in e for e in decl.validate_declaration(_input(acting_as="TIO")))

    @pytest.mark.parametrize("field,label", [
        ("name", "nombre"), ("document_number", "número de documento"), ("address", "dirección"),
        ("neighborhood", "barrio"), ("municipality", "municipio"), ("phone", "teléfono"),
        ("document_type", "tipo de documento"),
    ])
    def test_owner_required_fields(self, field, label):
        errors = decl.validate_declaration(_input(owner=_owner(**{field: "  "})))
        assert any(label in e for e in errors)

    def test_owner_document_type_must_be_in_vocabulary(self):
        errors = decl.validate_declaration(_input(owner=_owner(document_type="PASAPORTE")))
        assert any("tipo de documento" in e for e in errors)

    def test_email_is_optional(self):
        assert decl.validate_declaration(_input(owner=_owner(email=""))) == []

    def test_owner_signature_always_required(self):
        errors = decl.validate_declaration(_input(owner_signature_file=None))
        assert any("firma" in e for e in errors)

    def test_accepted_terms_required(self):
        assert any("términos" in e for e in decl.validate_declaration(_input(accepted_terms=False)))

    def test_loss_requires_notarized_pdf(self):
        assert any("PDF" in e for e in decl.validate_declaration(_input(reason="PÉRDIDA")))
        assert decl.validate_declaration(_input(reason="PÉRDIDA", notarized_pdf_file="n.pdf")) == []

    def test_deterioro_does_not_require_pdf(self):
        assert decl.validate_declaration(_input(reason="DETERIORO", notarized_pdf_file=None)) == []

    def test_gestor_requires_block_and_signature(self):
        errors = decl.validate_declaration(_gestor_input(gestor=None, gestor_signature_file=None))
        assert any("datos del gestor" in e for e in errors)
        assert any("firma del gestor" in e for e in errors)

    def test_gestor_block_fields_validated(self):
        errors = decl.validate_declaration(_gestor_input(gestor=_gestor(name="")))
        assert any("del gestor" in e for e in errors)

    def test_gestor_needs_owner_relation(self):
        errors = decl.validate_declaration(_gestor_input(owner_relation=""))
        assert any("dueño o poseedor" in e for e in errors)
        assert any("dueño o poseedor" in e for e in decl.validate_declaration(
            _gestor_input(owner_relation="OTROS")))

    def test_gestor_block_ignored_when_not_acting_as_gestor(self):
        assert decl.validate_declaration(_input(gestor=None, gestor_signature_file=None)) == []

    @pytest.mark.parametrize("field,label", [
        ("plate_number", "placa"), ("transit_license", "licencia"), ("vehicle_type", "tipo de vehículo"),
    ])
    def test_vehicle_required_fields(self, field, label):
        errors = decl.validate_declaration(_input(vehicle=_vehicle(**{field: ""})))
        assert any(label in e for e in errors)

    def test_vehicle_type_must_be_a_plate_type(self):
        errors = decl.validate_declaration(_input(vehicle=_vehicle(vehicle_type="BICICLETA")))
        assert any("tipo de vehículo" in e for e in errors)

    def test_plate_format_checked_against_vehicle_type(self):
        errors = decl.validate_declaration(_input(vehicle=_vehicle(plate_number="12")))
        assert any("formato" in e for e in errors)
        assert decl.validate_declaration(
            _input(vehicle=_vehicle(plate_number="923 naa", vehicle_type="MOTO CARRO PARTICULAR"))) == []


# ------------------------------------------------------------ submit: approved

class TestSubmitAutoApproved:
    def test_brand_new_client_and_card(self, db_conn):
        result = decl.submit_declaration(db_conn, _input(), today=TODAY)
        assert result.status == "approved" and result.flags == []
        record = db.get_declaration(db_conn, result.declaration_id)
        assert record is not None
        assert record.status == "approved"
        assert record.review_flags == []
        owner = db.get_customer(db_conn, record.customer_id)
        assert owner is not None
        assert owner.type == "CLIENTE"
        assert owner.name == "MARIA LOPEZ"
        assert owner.document_type == "CÉDULA"
        assert owner.document_number == "1020304050"
        assert owner.document_relation == "DUEÑO"
        assert (owner.address, owner.neighborhood, owner.municipality) == (
            "CRA 1 # 2-3", "CENTRO", "MEDELLÍN")
        assert (owner.phone, owner.email) == ("3001112233", "maria@example.com")
        card = db.get_ownership_card(db_conn, record.ownership_card_id)
        assert card is not None
        assert card.customer_id == owner.id
        assert (card.transit_license, card.plate_number) == ("LIC-001", "ABC123")
        assert (card.engine_number, card.vehicle_type, card.vehicle_brand) == (
            "ENG1", "CARRO PARTICULAR", "MAZDA")
        assert record.gestor_id is None
        assert record.submitter_ip == "1.1.1.1" and record.user_agent == "UA"
        assert record.owner_signature_path == "owner.png"
        assert record.accepted_terms_at is not None

    def test_submitted_data_is_a_full_copy(self, db_conn):
        result = decl.submit_declaration(db_conn, _input(), today=TODAY)
        data = db.get_declaration(db_conn, result.declaration_id).submitted_data
        assert data["owner"]["name"] == "MARIA LOPEZ"
        assert data["vehicle"]["plate_number"] == "ABC123"
        assert data["reason"] == "DETERIORO"
        assert data["raw"] == {"form": "raw"}

    def test_poseedor_maps_to_document_relation(self, db_conn):
        result = decl.submit_declaration(db_conn, _input(acting_as="POSEEDOR"), today=TODAY)
        record = db.get_declaration(db_conn, result.declaration_id)
        assert db.get_customer(db_conn, record.customer_id).document_relation == "POSEEDOR"

    def test_existing_client_with_a_second_car(self, db_conn):
        decl.submit_declaration(db_conn, _input(), today=TODAY)
        second = _input(vehicle=_vehicle(plate_number="DEF456", transit_license="LIC-002"))
        result = decl.submit_declaration(db_conn, second, today=TODAY)
        assert result.status == "approved"
        record = db.get_declaration(db_conn, result.declaration_id)
        assert len(db.list_ownership_cards_for_customer(db_conn, record.customer_id)) == 2
        assert _count(db_conn, "customers") == 1

    def test_same_client_same_car_after_six_months_links_existing_card_untouched(self, db_conn):
        client_id = _existing_client(db_conn)
        card_id = db.create_ownership_card(
            db_conn, client_id, transit_license="LIC-001", plate_number="ABC123",
            engine_number="OLD-ENGINE", vehicle_brand="OLD-BRAND")
        _fabricate(db_conn, "ABC123", date(2026, 4, 1))
        result = decl.submit_declaration(db_conn, _input(), today=TODAY)
        assert result.status == "approved"
        record = db.get_declaration(db_conn, result.declaration_id)
        assert (record.customer_id, record.ownership_card_id) == (client_id, card_id)
        card = db.get_ownership_card(db_conn, card_id)
        assert (card.engine_number, card.vehicle_brand) == ("OLD-ENGINE", "OLD-BRAND")
        assert _count(db_conn, "ownership_cards") == 1
        # Secondary data that differs only lives in the declaration copy.
        assert record.submitted_data["vehicle"]["engine_number"] == "ENG1"

    def test_existing_client_is_linked_never_modified(self, db_conn):
        client_id = _existing_client(db_conn)
        before = db.get_customer(db_conn, client_id)
        result = decl.submit_declaration(db_conn, _input(), today=TODAY)
        assert db.get_declaration(db_conn, result.declaration_id).customer_id == client_id
        assert db.get_customer(db_conn, client_id) == before

    def test_exactly_six_months_is_not_recent(self, db_conn):
        _fabricate(db_conn, "ABC123", date(2026, 4, 5))   # + 6 months == TODAY
        result = decl.submit_declaration(db_conn, _input(), today=TODAY)
        assert result.status == "approved"

    def test_voided_plate_ignored_by_six_month_rule(self, db_conn):
        _fabricate(db_conn, "ABC123", date(2026, 9, 1), is_voided=True)
        assert decl.submit_declaration(db_conn, _input(), today=TODAY).status == "approved"

    def test_annulled_plate_ignored_by_six_month_rule(self, db_conn):
        _fabricate(db_conn, "ABC123", date(2026, 9, 1), is_plate_annulled=True)
        assert decl.submit_declaration(db_conn, _input(), today=TODAY).status == "approved"

    def test_gestor_created_as_gestor_and_owner_relation_used(self, db_conn):
        result = decl.submit_declaration(db_conn, _gestor_input(owner_relation="POSEEDOR"), today=TODAY)
        assert result.status == "approved"
        record = db.get_declaration(db_conn, result.declaration_id)
        gestor = db.get_customer(db_conn, record.gestor_id)
        assert gestor is not None
        assert (gestor.type, gestor.document_type, gestor.document_number) == ("GESTOR", "NIT", "900123456")
        assert gestor.document_relation is None
        owner = db.get_customer(db_conn, record.customer_id)
        assert (owner.type, owner.document_relation) == ("CLIENTE", "POSEEDOR")
        assert record.acting_as == "GESTOR"
        assert record.gestor_signature_path == "gestor.png"

    def test_existing_gestor_linked_unmodified(self, db_conn):
        gestor_id = _existing_client(db_conn, "GESTORIA SUR", "GESTOR")
        before = db.get_customer(db_conn, gestor_id)
        result = decl.submit_declaration(db_conn, _gestor_input(), today=TODAY)
        assert result.status == "approved"
        assert db.get_declaration(db_conn, result.declaration_id).gestor_id == gestor_id
        assert db.get_customer(db_conn, gestor_id) == before

    def test_default_today_is_used_when_omitted(self, db_conn):
        assert decl.submit_declaration(db_conn, _input()).status == "approved"


# ------------------------------------------------------------- submit: pending

class TestSubmitPending:
    def _assert_nothing_created(self, db_conn, result, flags):
        assert result.status == "pending_review"
        assert sorted(result.flags) == sorted(flags)
        record = db.get_declaration(db_conn, result.declaration_id)
        assert record is not None
        assert record.status == "pending_review"
        assert sorted(record.review_flags) == sorted(flags)
        assert (record.customer_id, record.ownership_card_id, record.gestor_id) == (None, None, None)
        assert record.submitted_data["owner"]["name"] == "MARIA LOPEZ"

    def test_plate_made_recently(self, db_conn):
        _fabricate(db_conn, "ABC123", date(2026, 4, 6))   # + 6 months = day after TODAY
        result = decl.submit_declaration(db_conn, _input(), today=TODAY)
        self._assert_nothing_created(db_conn, result, [decl.PLATE_MADE_RECENTLY])
        assert _count(db_conn, "customers") == 0
        assert _count(db_conn, "ownership_cards") == 0

    def test_latest_fabrication_counts(self, db_conn):
        _fabricate(db_conn, "ABC123", date(2025, 1, 1))
        _fabricate(db_conn, "ABC123", date(2026, 8, 1))
        result = decl.submit_declaration(db_conn, _input(), today=TODAY)
        assert result.flags == [decl.PLATE_MADE_RECENTLY]

    def test_card_on_other_customer(self, db_conn):
        other = _existing_client(db_conn, "PEDRO RUIZ")
        db.create_ownership_card(db_conn, other, transit_license="LIC-001", plate_number="ABC123")
        result = decl.submit_declaration(db_conn, _input(), today=TODAY)
        self._assert_nothing_created(db_conn, result, [decl.CARD_OTHER_OWNER])
        assert _count(db_conn, "customers") == 1
        assert _count(db_conn, "ownership_cards") == 1

    def test_card_exists_and_owner_is_new(self, db_conn):
        other = _existing_client(db_conn, "PEDRO RUIZ")
        db.create_ownership_card(db_conn, other, transit_license="LIC-001", plate_number="ABC123")
        result = decl.submit_declaration(db_conn, _input(owner=_owner(name="NUEVA PERSONA")), today=TODAY)
        assert result.flags == [decl.CARD_OTHER_OWNER]
        assert db.get_customer_by_name(db_conn, "NUEVA PERSONA") is None

    def test_licence_with_different_plate(self, db_conn):
        client = _existing_client(db_conn)
        db.create_ownership_card(db_conn, client, transit_license="LIC-001", plate_number="ZZZ999")
        result = decl.submit_declaration(db_conn, _input(), today=TODAY)
        self._assert_nothing_created(db_conn, result, [decl.CARD_DATA_MISMATCH])

    def test_plate_with_different_licence(self, db_conn):
        client = _existing_client(db_conn)
        db.create_ownership_card(db_conn, client, transit_license="OTRA-LIC", plate_number="ABC123")
        result = decl.submit_declaration(db_conn, _input(), today=TODAY)
        self._assert_nothing_created(db_conn, result, [decl.CARD_DATA_MISMATCH])

    @pytest.mark.parametrize("type_", [None, "EMPRESA", "TRÁNSITO", "GESTOR"])
    def test_existing_owner_with_wrong_or_null_type(self, db_conn, type_):
        _existing_client(db_conn, type_=type_)
        result = decl.submit_declaration(db_conn, _input(), today=TODAY)
        self._assert_nothing_created(db_conn, result, [decl.CUSTOMER_TYPE_MISMATCH])
        assert _count(db_conn, "ownership_cards") == 0

    @pytest.mark.parametrize("type_", [None, "CLIENTE", "EMPRESA"])
    def test_existing_gestor_with_wrong_or_null_type(self, db_conn, type_):
        _existing_client(db_conn, "GESTORIA SUR", type_)
        result = decl.submit_declaration(db_conn, _gestor_input(), today=TODAY)
        self._assert_nothing_created(db_conn, result, [decl.CUSTOMER_TYPE_MISMATCH])

    def test_multiple_flags_accumulate(self, db_conn):
        _existing_client(db_conn, type_=None)
        _fabricate(db_conn, "ABC123", date(2026, 9, 1))
        result = decl.submit_declaration(db_conn, _input(), today=TODAY)
        assert sorted(result.flags) == sorted([decl.PLATE_MADE_RECENTLY, decl.CUSTOMER_TYPE_MISMATCH])

    def test_pending_files_are_kept(self, db_conn):
        _fabricate(db_conn, "ABC123", date(2026, 9, 1))
        result = decl.submit_declaration(
            db_conn, _input(reason="PÉRDIDA", notarized_pdf_file="n.pdf"), today=TODAY)
        record = db.get_declaration(db_conn, result.declaration_id)
        assert (record.owner_signature_path, record.notarized_pdf_path) == ("owner.png", "n.pdf")
        assert record.reason == "PÉRDIDA"


# ----------------------------------------------------------------- review

def _pending(db_conn, inp: DeclarationInput | None = None) -> int:
    """Force a pending declaration for `inp` regardless of conflicts, by
    submitting while a recent fabrication exists for the plate."""
    inp = inp or _input()
    _fabricate(db_conn, inp.vehicle.plate_number, date(2026, 10, 1))
    result = decl.submit_declaration(db_conn, inp, today=TODAY)
    assert result.status == "pending_review"
    return result.declaration_id


class TestApprove:
    def test_creates_owner_card_and_marks_approved(self, db_conn):
        declaration_id = _pending(db_conn)
        reviewer = _reviewer_id(db_conn)
        decl.approve_declaration(db_conn, declaration_id, reviewer, note="todo bien")
        record = db.get_declaration(db_conn, declaration_id)
        assert record.status == "approved"
        assert (record.reviewed_by, record.review_note) == (reviewer, "todo bien")
        assert record.reviewed_at is not None
        owner = db.get_customer(db_conn, record.customer_id)
        assert (owner.name, owner.type, owner.document_relation) == ("MARIA LOPEZ", "CLIENTE", "DUEÑO")
        card = db.get_ownership_card(db_conn, record.ownership_card_id)
        assert (card.customer_id, card.plate_number) == (owner.id, "ABC123")
        assert record.review_flags == ["plate_made_recently"]   # history is kept

    def test_creates_gestor(self, db_conn):
        declaration_id = _pending(db_conn, _gestor_input())
        decl.approve_declaration(db_conn, declaration_id, _reviewer_id(db_conn))
        record = db.get_declaration(db_conn, declaration_id)
        assert db.get_customer(db_conn, record.gestor_id).type == "GESTOR"

    def test_null_type_customer_is_fixed_to_expected_type(self, db_conn):
        client_id = _existing_client(db_conn, type_=None)
        gestor_id = _existing_client(db_conn, "GESTORIA SUR", None)
        declaration_id = _pending(db_conn, _gestor_input())
        decl.approve_declaration(db_conn, declaration_id, _reviewer_id(db_conn))
        client = db.get_customer(db_conn, client_id)
        assert client.type == "CLIENTE"
        assert client.phone == "PREVIO"           # nothing else touched
        assert db.get_customer(db_conn, gestor_id).type == "GESTOR"
        record = db.get_declaration(db_conn, declaration_id)
        assert (record.customer_id, record.gestor_id) == (client_id, gestor_id)

    def test_different_type_raises_and_changes_nothing(self, db_conn):
        _existing_client(db_conn, type_="EMPRESA")
        declaration_id = _pending(db_conn)
        with pytest.raises(ValueError, match="EMPRESA"):
            decl.approve_declaration(db_conn, declaration_id, _reviewer_id(db_conn))
        assert db.get_declaration(db_conn, declaration_id).status == "pending_review"
        assert _count(db_conn, "ownership_cards") == 0

    def test_different_gestor_type_raises(self, db_conn):
        _existing_client(db_conn, "GESTORIA SUR", "CLIENTE")
        declaration_id = _pending(db_conn, _gestor_input())
        with pytest.raises(ValueError, match="GESTOR"):
            decl.approve_declaration(db_conn, declaration_id, _reviewer_id(db_conn))

    def test_card_on_other_owner_raises_without_reassign(self, db_conn):
        other = _existing_client(db_conn, "PEDRO RUIZ")
        card_id = db.create_ownership_card(db_conn, other, transit_license="LIC-001", plate_number="ABC123")
        declaration_id = _pending(db_conn)
        with pytest.raises(ValueError, match="otro cliente"):
            decl.approve_declaration(db_conn, declaration_id, _reviewer_id(db_conn))
        assert db.get_ownership_card(db_conn, card_id).customer_id == other
        assert db.get_declaration(db_conn, declaration_id).status == "pending_review"

    def test_reassign_moves_the_card(self, db_conn):
        other = _existing_client(db_conn, "PEDRO RUIZ")
        card_id = db.create_ownership_card(db_conn, other, transit_license="LIC-001", plate_number="ABC123",
                                           engine_number="KEEP")
        declaration_id = _pending(db_conn)
        decl.approve_declaration(db_conn, declaration_id, _reviewer_id(db_conn), reassign_card=True)
        record = db.get_declaration(db_conn, declaration_id)
        assert record.ownership_card_id == card_id
        card = db.get_ownership_card(db_conn, card_id)
        assert card.customer_id == record.customer_id != other
        assert card.engine_number == "KEEP"

    def test_data_mismatch_raises_even_with_reassign(self, db_conn):
        client = _existing_client(db_conn)
        db.create_ownership_card(db_conn, client, transit_license="LIC-001", plate_number="ZZZ999")
        declaration_id = _pending(db_conn)
        with pytest.raises(ValueError, match="no coinciden"):
            decl.approve_declaration(db_conn, declaration_id, _reviewer_id(db_conn), reassign_card=True)

    def test_licence_and_plate_on_two_different_cards_raises(self, db_conn):
        client = _existing_client(db_conn)
        db.create_ownership_card(db_conn, client, transit_license="LIC-001", plate_number="AAA111")
        db.create_ownership_card(db_conn, client, transit_license="LIC-002", plate_number="ABC123")
        declaration_id = _pending(db_conn)
        with pytest.raises(ValueError, match="no coinciden"):
            decl.approve_declaration(db_conn, declaration_id, _reviewer_id(db_conn), reassign_card=True)

    def test_same_card_is_linked(self, db_conn):
        client = _existing_client(db_conn)
        card_id = db.create_ownership_card(db_conn, client, transit_license="LIC-001", plate_number="ABC123")
        declaration_id = _pending(db_conn)
        decl.approve_declaration(db_conn, declaration_id, _reviewer_id(db_conn))
        assert db.get_declaration(db_conn, declaration_id).ownership_card_id == card_id
        assert _count(db_conn, "ownership_cards") == 1

    def test_only_from_pending_review(self, db_conn):
        approved = decl.submit_declaration(db_conn, _input(), today=TODAY).declaration_id
        with pytest.raises(ValueError, match="pendiente"):
            decl.approve_declaration(db_conn, approved, _reviewer_id(db_conn))
        rejected = _pending(db_conn, _input(vehicle=_vehicle(plate_number="DEF456", transit_license="L2")))
        decl.reject_declaration(db_conn, rejected, _reviewer_id(db_conn), "no")
        with pytest.raises(ValueError, match="pendiente"):
            decl.approve_declaration(db_conn, rejected, _reviewer_id(db_conn))

    def test_unknown_declaration(self, db_conn):
        with pytest.raises(ValueError, match="no existe"):
            decl.approve_declaration(db_conn, 999999, _reviewer_id(db_conn))


class TestReject:
    def test_marks_rejected_and_creates_nothing(self, db_conn):
        declaration_id = _pending(db_conn)
        reviewer = _reviewer_id(db_conn)
        decl.reject_declaration(db_conn, declaration_id, reviewer, "  datos falsos ")
        record = db.get_declaration(db_conn, declaration_id)
        assert record.status == "rejected"
        assert (record.reviewed_by, record.review_note) == (reviewer, "datos falsos")
        assert record.reviewed_at is not None
        assert (record.customer_id, record.ownership_card_id, record.gestor_id) == (None, None, None)
        assert _count(db_conn, "customers") == 0
        assert _count(db_conn, "ownership_cards") == 0

    def test_note_required(self, db_conn):
        declaration_id = _pending(db_conn)
        with pytest.raises(ValueError, match="nota"):
            decl.reject_declaration(db_conn, declaration_id, _reviewer_id(db_conn), "  ")

    def test_only_from_pending_review(self, db_conn):
        approved = decl.submit_declaration(db_conn, _input(), today=TODAY).declaration_id
        with pytest.raises(ValueError, match="pendiente"):
            decl.reject_declaration(db_conn, approved, _reviewer_id(db_conn), "no")


def test_submit_result_is_a_plain_dataclass_replace_friendly():
    result = decl.SubmitResult(1, "approved", [])
    assert replace(result, status="pending_review").status == "pending_review"
