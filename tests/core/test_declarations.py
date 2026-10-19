"""Tests for app/core/declarations.py: validation (pure) and the submission /
review rules (need Postgres, see tests/conftest.py's `db_conn`).

A declaration has ONE declarant and 1..MAX_CARDS ownership cards; the rules
are evaluated per card, and one flagged card holds the whole declaration.

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
from app.core.declarations import DeclarationInput, OwnershipCardInput, PersonInput
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


def _card(**kw) -> OwnershipCardInput:
    base = {"plate_number": "ABC123", "transit_license": "LIC-001", "engine_number": "ENG1",
            "chassis_number": "CHA1", "vehicle_type": "CARRO PARTICULAR", "registered_in": "MEDELLÍN",
            "vehicle_brand": "MAZDA", "reason": "DETERIORO", "plate_quantity": 1}
    base.update(kw)
    return OwnershipCardInput(**base)


def _gcard(plate: str = "ABC123", licence: str = "LIC-001", owner: PersonInput | None = None,
           relation: str = "DUEÑO", **kw) -> OwnershipCardInput:
    """A card of a GESTOR declaration: it carries its own owner."""
    return _card(plate_number=plate, transit_license=licence, owner=owner or _owner(),
                 owner_relation=relation, **kw)


def _input(**kw) -> DeclarationInput:
    base = {"acting_as": "PROPIETARIO", "declarant": _owner(), "cards": [_card()],
            "signature_file": "sig.png", "submitter_ip": "1.1.1.1", "user_agent": "UA",
            "accepted_terms": True, "submitted_data": {"form": "raw"}}
    base.update(kw)
    return DeclarationInput(**base)


def _gestor_input(**kw) -> DeclarationInput:
    base = {"acting_as": "GESTOR", "declarant": _gestor(), "cards": [_gcard()]}
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


def _cards_of(db_conn, declaration_id: int) -> list[db.DeclarationCardRecord]:
    return db.list_declaration_cards(db_conn, declaration_id)


# ---------------------------------------------------------------- validation

class TestValidate:
    def test_valid_input_has_no_errors(self):
        assert decl.validate_declaration(_input()) == []
        assert decl.validate_declaration(_gestor_input()) == []

    def test_vocabulary_is_case_and_accent_insensitive(self):
        assert decl.validate_declaration(
            _input(cards=[_card(reason="deterioro")], acting_as="propietario")) == []

    def test_acting_as_required_and_valid(self):
        assert any("actúa" in e for e in decl.validate_declaration(_input(acting_as="")))
        assert any("actúa" in e for e in decl.validate_declaration(_input(acting_as="TIO")))

    @pytest.mark.parametrize("field,label", [
        ("name", "nombre"), ("document_number", "número de documento"), ("address", "dirección"),
        ("neighborhood", "barrio"), ("municipality", "municipio"), ("phone", "teléfono"),
        ("document_type", "tipo de documento"),
    ])
    def test_declarant_required_fields(self, field, label):
        errors = decl.validate_declaration(_input(declarant=_owner(**{field: "  "})))
        assert any(label in e and "declarante" in e for e in errors)

    def test_declarant_document_type_must_be_in_vocabulary(self):
        errors = decl.validate_declaration(_input(declarant=_owner(document_type="PASAPORTE")))
        assert any("tipo de documento" in e for e in errors)

    def test_email_is_optional(self):
        assert decl.validate_declaration(_input(declarant=_owner(email=""))) == []

    def test_single_signature_always_required(self):
        assert any("firma" in e for e in decl.validate_declaration(_input(signature_file=None)))
        assert any("firma" in e for e in decl.validate_declaration(_gestor_input(signature_file=" ")))

    def test_accepted_terms_required(self):
        assert any("términos" in e for e in decl.validate_declaration(_input(accepted_terms=False)))

    def test_zero_cards_rejected(self):
        errors = decl.validate_declaration(_input(cards=[]))
        assert any("al menos una tarjeta" in e for e in errors)

    def test_more_than_max_cards_rejected(self):
        cards = [_card(plate_number=f"AB{chr(65 + i // 10)}{i % 10:03d}", transit_license=f"L{i}")
                 for i in range(decl.MAX_CARDS + 1)]
        errors = decl.validate_declaration(_input(cards=cards))
        assert any(str(decl.MAX_CARDS) in e and "tarjetas" in e for e in errors)

    def test_exactly_max_cards_is_valid(self):
        cards = [_card(plate_number=f"AB{chr(65 + i // 10)}{i % 10:03d}", transit_license=f"L{i}")
                 for i in range(decl.MAX_CARDS)]
        assert decl.validate_declaration(_input(cards=cards)) == []

    @pytest.mark.parametrize("reason", ["", "OTROS", "ROBO"])
    def test_reason_must_be_deterioro_or_perdida_per_card(self, reason):
        errors = decl.validate_declaration(_input(cards=[_card(), _card(
            plate_number="DEF456", transit_license="L2", reason=reason)]))
        assert any(e.startswith("Tarjeta 2:") and "motivo" in e for e in errors)
        assert not any(e.startswith("Tarjeta 1:") for e in errors)

    def test_loss_in_any_card_requires_no_file(self):
        cards = [_card(), _card(plate_number="DEF456", transit_license="L2", reason="PÉRDIDA")]
        assert decl.validate_declaration(_input(cards=cards)) == []

    def test_declaration_input_has_no_notarized_pdf_field(self):
        assert not hasattr(decl.DeclarationInput(), "notarized_pdf_file")
        with pytest.raises(TypeError):
            decl.DeclarationInput(notarized_pdf_file="n.pdf")  # type: ignore[call-arg]

    @pytest.mark.parametrize("qty", [0, -1])
    def test_quantity_at_least_one(self, qty):
        errors = decl.validate_declaration(_input(cards=[_card(plate_quantity=qty)]))
        assert any(e.startswith("Tarjeta 1:") and "cantidad" in e for e in errors)

    @pytest.mark.parametrize("vehicle_type,plate", [
        ("CARRO PARTICULAR", "ABC123"), ("CARRO PÚBLICO", "ABC123")])
    def test_cars_accept_one_or_two_plates(self, vehicle_type, plate):
        for qty in (1, 2):
            card = _card(vehicle_type=vehicle_type, plate_number=plate, plate_quantity=qty)
            assert decl.validate_declaration(_input(cards=[card])) == []
        errors = decl.validate_declaration(_input(cards=[
            _card(vehicle_type=vehicle_type, plate_number=plate, plate_quantity=3)]))
        assert "Tarjeta 1: La cantidad de placas debe ser 1 o 2." in errors

    @pytest.mark.parametrize("vehicle_type,plate", [
        ("MOTO PARTICULAR", "KWD91G"), ("MOTO CARRO PARTICULAR", "923NAA"),
        ("MOTO CARRO PÚBLICO", "923NAA"), ("REMOLQUE", "R12345"), ("ORNAMENTAL", "ABC123")])
    def test_other_types_accept_a_single_plate_only(self, vehicle_type, plate):
        assert decl.validate_declaration(_input(cards=[
            _card(vehicle_type=vehicle_type, plate_number=plate, plate_quantity=1)])) == []
        errors = decl.validate_declaration(_input(cards=[
            _card(vehicle_type=vehicle_type, plate_number=plate, plate_quantity=2)]))
        assert "Tarjeta 1: La cantidad de placas para este tipo de vehículo debe ser 1." in errors

    def test_quantity_must_be_an_int(self):
        errors = decl.validate_declaration(_input(cards=[_card(plate_quantity=True)]))
        assert any("cantidad" in e for e in errors)

    @pytest.mark.parametrize("field,label", [
        ("plate_number", "placa"), ("transit_license", "licencia"), ("vehicle_type", "tipo de vehículo"),
    ])
    def test_card_required_fields_are_prefixed_with_the_card_number(self, field, label):
        second = {"plate_number": "DEF456", "transit_license": "L2", field: ""}
        errors = decl.validate_declaration(_input(cards=[_card(), _card(**second)]))
        assert any(e.startswith("Tarjeta 2:") and label in e for e in errors)

    def test_vehicle_type_must_be_a_plate_type(self):
        errors = decl.validate_declaration(_input(cards=[_card(vehicle_type="BICICLETA")]))
        assert any("tipo de vehículo" in e for e in errors)

    def test_plate_format_checked_against_vehicle_type(self):
        errors = decl.validate_declaration(_input(cards=[_card(plate_number="12")]))
        assert any("formato" in e for e in errors)
        assert decl.validate_declaration(_input(cards=[
            _card(plate_number="923 naa", vehicle_type="MOTO CARRO PARTICULAR")])) == []

    def test_repeated_plate_inside_the_submission_rejected(self):
        cards = [_card(), _card(plate_number="abc 123", transit_license="L2")]
        errors = decl.validate_declaration(_input(cards=cards))
        assert any(e.startswith("Tarjeta 2:") and "placa ABC123" in e and "tarjeta 1" in e for e in errors)

    def test_repeated_transit_license_inside_the_submission_rejected(self):
        cards = [_card(), _card(plate_number="DEF456", transit_license=" lic-001 ")]
        errors = decl.validate_declaration(_input(cards=cards))
        assert any(e.startswith("Tarjeta 2:") and "licencia de tránsito LIC-001" in e for e in errors)

    def test_gestor_cards_need_their_owner_and_relation(self):
        errors = decl.validate_declaration(_gestor_input(cards=[
            _gcard(), _card(plate_number="DEF456", transit_license="L2", owner=None, owner_relation="")]))
        assert any(e.startswith("Tarjeta 2:") and "datos del propietario" in e for e in errors)
        assert any(e.startswith("Tarjeta 2:") and "dueño o poseedor" in e for e in errors)
        assert not any(e.startswith("Tarjeta 1:") for e in errors)

    def test_gestor_card_owner_fields_validated(self):
        errors = decl.validate_declaration(_gestor_input(cards=[_gcard(owner=_owner(name=""))]))
        assert any(e.startswith("Tarjeta 1:") and "nombre" in e for e in errors)

    def test_owner_relation_must_be_dueno_or_poseedor(self):
        assert any("dueño o poseedor" in e for e in decl.validate_declaration(
            _gestor_input(cards=[_gcard(relation="OTROS")])))

    def test_card_owner_ignored_when_not_acting_as_gestor(self):
        card = _card(owner=None, owner_relation="")
        assert decl.validate_declaration(_input(cards=[card])) == []


# ------------------------------------------------------------ submit: approved

class TestSubmitAutoApproved:
    def test_brand_new_client_and_card(self, db_conn):
        result = decl.submit_declaration(db_conn, _input(), today=TODAY)
        assert result.status == "approved" and result.flags == []
        record = db.get_declaration(db_conn, result.declaration_id)
        assert record is not None
        assert record.status == "approved"
        assert record.review_flags == []
        (row,) = _cards_of(db_conn, result.declaration_id)
        owner = db.get_customer(db_conn, row.customer_id)
        assert owner is not None
        assert owner.type == "CLIENTE"
        assert owner.name == "MARIA LOPEZ"
        assert owner.document_type == "CÉDULA"
        assert owner.document_number == "1020304050"
        assert owner.document_relation == "DUEÑO"
        assert (owner.address, owner.neighborhood, owner.municipality) == (
            "CRA 1 # 2-3", "CENTRO", "MEDELLÍN")
        assert (owner.phone, owner.email) == ("3001112233", "maria@example.com")
        card = db.get_ownership_card(db_conn, row.ownership_card_id)
        assert card is not None
        assert card.customer_id == owner.id
        assert (card.transit_license, card.plate_number) == ("LIC-001", "ABC123")
        assert (card.engine_number, card.vehicle_type, card.vehicle_brand) == (
            "ENG1", "CARRO PARTICULAR", "MAZDA")
        assert record.gestor_id is None
        assert record.submitter_ip == "1.1.1.1" and record.user_agent == "UA"
        assert record.declarant_signature_path == "sig.png"
        assert record.accepted_terms_at is not None

    def test_card_row_stores_the_declared_card(self, db_conn):
        card = _card(plate_number="abc 123", transit_license=" lic-001 ", plate_quantity=2,
                     reason="deterioro", engine_number=" ", vehicle_type="carro particular")
        result = decl.submit_declaration(db_conn, _input(cards=[card]), today=TODAY)
        (row,) = _cards_of(db_conn, result.declaration_id)
        assert (row.position, row.reason, row.plate_quantity) == (0, "DETERIORO", 2)
        assert (row.plate_number, row.transit_license) == ("ABC123", "LIC-001")
        assert (row.engine_number, row.chassis_number) == (None, "CHA1")
        assert (row.vehicle_type, row.registered_in, row.vehicle_brand) == (
            "CARRO PARTICULAR", "MEDELLÍN", "MAZDA")
        assert row.owner_relation == "DUEÑO"
        assert row.owner_data["name"] == "MARIA LOPEZ"
        assert row.review_flags == []

    def test_submitted_data_has_the_canonical_shape(self, db_conn):
        inp = _input(cards=[_card(), _card(plate_number="DEF456", transit_license="L2", reason="PÉRDIDA",
                                           plate_quantity=2)])
        result = decl.submit_declaration(db_conn, inp, today=TODAY)
        data = db.get_declaration(db_conn, result.declaration_id).submitted_data
        assert set(data) == {"acting_as", "declarant", "cards", "raw"}
        assert data["acting_as"] == "PROPIETARIO"
        assert data["declarant"]["name"] == "MARIA LOPEZ"
        assert [c["plate_number"] for c in data["cards"]] == ["ABC123", "DEF456"]
        assert data["cards"][1]["reason"] == "PÉRDIDA"
        assert data["cards"][1]["plate_quantity"] == 2
        assert data["cards"][0]["owner"]["name"] == "MARIA LOPEZ"   # copy of the declarant
        assert data["cards"][0]["owner_relation"] == "DUEÑO"
        assert data["raw"] == {"form": "raw"}

    def test_raw_is_omitted_when_the_caller_gave_none(self, db_conn):
        result = decl.submit_declaration(db_conn, _input(submitted_data={}), today=TODAY)
        assert "raw" not in db.get_declaration(db_conn, result.declaration_id).submitted_data

    def test_poseedor_maps_to_document_relation(self, db_conn):
        result = decl.submit_declaration(db_conn, _input(acting_as="POSEEDOR"), today=TODAY)
        (row,) = _cards_of(db_conn, result.declaration_id)
        assert row.owner_relation == "POSEEDOR"
        assert db.get_customer(db_conn, row.customer_id).document_relation == "POSEEDOR"

    def test_several_cards_of_the_declarant_share_one_customer(self, db_conn):
        cards = [_card(), _card(plate_number="DEF456", transit_license="L2", plate_quantity=2),
                 _card(plate_number="923NAA", transit_license="L3", vehicle_type="MOTO CARRO PARTICULAR")]
        result = decl.submit_declaration(db_conn, _input(cards=cards), today=TODAY)
        assert result.status == "approved"
        rows = _cards_of(db_conn, result.declaration_id)
        assert [r.position for r in rows] == [0, 1, 2]
        assert len({r.customer_id for r in rows}) == 1
        assert len({r.ownership_card_id for r in rows}) == 3
        assert _count(db_conn, "customers") == 1
        assert _count(db_conn, "ownership_cards") == 3
        assert len(db.list_ownership_cards_for_customer(db_conn, rows[0].customer_id)) == 3

    def test_existing_client_with_a_second_card(self, db_conn):
        decl.submit_declaration(db_conn, _input(), today=TODAY)
        second = _input(cards=[_card(plate_number="DEF456", transit_license="LIC-002")])
        result = decl.submit_declaration(db_conn, second, today=TODAY)
        assert result.status == "approved"
        (row,) = _cards_of(db_conn, result.declaration_id)
        assert len(db.list_ownership_cards_for_customer(db_conn, row.customer_id)) == 2
        assert _count(db_conn, "customers") == 1

    def test_same_client_same_card_after_six_months_links_existing_card_untouched(self, db_conn):
        client_id = _existing_client(db_conn)
        card_id = db.create_ownership_card(
            db_conn, client_id, transit_license="LIC-001", plate_number="ABC123",
            engine_number="OLD-ENGINE", vehicle_brand="OLD-BRAND")
        _fabricate(db_conn, "ABC123", date(2026, 4, 1))
        result = decl.submit_declaration(db_conn, _input(), today=TODAY)
        assert result.status == "approved"
        (row,) = _cards_of(db_conn, result.declaration_id)
        assert (row.customer_id, row.ownership_card_id) == (client_id, card_id)
        card = db.get_ownership_card(db_conn, card_id)
        assert (card.engine_number, card.vehicle_brand) == ("OLD-ENGINE", "OLD-BRAND")
        assert _count(db_conn, "ownership_cards") == 1
        # Secondary data that differs only lives in the declaration copy.
        record = db.get_declaration(db_conn, result.declaration_id)
        assert record.submitted_data["cards"][0]["engine_number"] == "ENG1"

    def test_existing_client_is_linked_never_modified(self, db_conn):
        client_id = _existing_client(db_conn)
        before = db.get_customer(db_conn, client_id)
        result = decl.submit_declaration(db_conn, _input(), today=TODAY)
        assert _cards_of(db_conn, result.declaration_id)[0].customer_id == client_id
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

    def test_gestor_declarant_is_the_gestor_customer_and_owner_relation_used(self, db_conn):
        inp = _gestor_input(cards=[_gcard(relation="POSEEDOR")])
        result = decl.submit_declaration(db_conn, inp, today=TODAY)
        assert result.status == "approved"
        record = db.get_declaration(db_conn, result.declaration_id)
        gestor = db.get_customer(db_conn, record.gestor_id)
        assert gestor is not None
        assert (gestor.type, gestor.document_type, gestor.document_number) == ("GESTOR", "NIT", "900123456")
        assert gestor.document_relation is None
        (row,) = _cards_of(db_conn, result.declaration_id)
        owner = db.get_customer(db_conn, row.customer_id)
        assert (owner.type, owner.document_relation) == ("CLIENTE", "POSEEDOR")
        assert owner.id != gestor.id
        assert record.acting_as == "GESTOR"
        assert record.declarant_signature_path == "sig.png"
        assert record.submitted_data["declarant"]["name"] == "GESTORIA SUR"
        assert record.submitted_data["cards"][0]["owner"]["name"] == "MARIA LOPEZ"

    def test_gestor_with_several_owners_creates_each_owner_and_card(self, db_conn):
        luis = _owner(name="LUIS PEREZ", document_number="777")
        cards = [_gcard("ABC123", "L1", owner=_owner()),
                 _gcard("DEF456", "L2", owner=luis, relation="POSEEDOR", plate_quantity=2),
                 _gcard("GHI789", "L3", owner=_owner(), reason="PÉRDIDA")]
        result = decl.submit_declaration(db_conn, _gestor_input(cards=cards),
                                         today=TODAY)
        assert (result.status, result.flags) == ("approved", [])
        record = db.get_declaration(db_conn, result.declaration_id)
        rows = _cards_of(db_conn, result.declaration_id)
        names = [db.get_customer(db_conn, r.customer_id).name for r in rows]
        assert names == ["MARIA LOPEZ", "LUIS PEREZ", "MARIA LOPEZ"]
        # The repeated owner is ONE customer; gestor + 2 owners = 3 customers.
        assert rows[0].customer_id == rows[2].customer_id != rows[1].customer_id
        assert _count(db_conn, "customers") == 3
        assert _count(db_conn, "ownership_cards") == 3
        for row in rows:
            assert db.get_ownership_card(db_conn, row.ownership_card_id).customer_id == row.customer_id
        assert db.get_customer(db_conn, rows[1].customer_id).document_relation == "POSEEDOR"
        assert [r.reason for r in rows] == ["DETERIORO", "DETERIORO", "PÉRDIDA"]
        assert [r.plate_quantity for r in rows] == [1, 2, 1]
        assert record.gestor_id is not None
        assert db.get_customer(db_conn, record.gestor_id).type == "GESTOR"
        assert record.notarized_pdf_path is None

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
        assert record.gestor_id is None
        rows = _cards_of(db_conn, result.declaration_id)
        assert rows
        assert all(r.customer_id is None and r.ownership_card_id is None for r in rows)
        assert record.submitted_data["declarant"]["name"] in ("MARIA LOPEZ", "GESTORIA SUR")

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
        result = decl.submit_declaration(
            db_conn, _input(declarant=_owner(name="NUEVA PERSONA")), today=TODAY)
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

    def test_existing_owner_with_a_different_document_number(self, db_conn):
        """Names are the customer identity: a homonym with another document
        must not be merged silently into the existing customer."""
        db.resolve_or_create_customer(db_conn, "MARIA LOPEZ", type="CLIENTE", document_number="999")
        result = decl.submit_declaration(db_conn, _input(), today=TODAY)
        self._assert_nothing_created(db_conn, result, [decl.CUSTOMER_DOCUMENT_MISMATCH])
        assert _count(db_conn, "ownership_cards") == 0

    def test_same_document_number_with_other_punctuation_is_not_a_mismatch(self, db_conn):
        db.resolve_or_create_customer(
            db_conn, "MARIA LOPEZ", type="CLIENTE", document_number="1.020.304.050",
        )
        result = decl.submit_declaration(db_conn, _input(), today=TODAY)
        assert (result.status, result.flags) == ("approved", [])

    def test_existing_owner_without_a_document_number_is_not_a_mismatch(self, db_conn):
        _existing_client(db_conn)
        result = decl.submit_declaration(db_conn, _input(), today=TODAY)
        assert (result.status, result.flags) == ("approved", [])

    def test_existing_gestor_with_a_different_document_number(self, db_conn):
        db.resolve_or_create_customer(db_conn, "GESTORIA SUR", type="GESTOR", document_number="111")
        result = decl.submit_declaration(db_conn, _gestor_input(), today=TODAY)
        self._assert_nothing_created(db_conn, result, [decl.CUSTOMER_DOCUMENT_MISMATCH])

    def test_multiple_flags_accumulate(self, db_conn):
        _existing_client(db_conn, type_=None)
        _fabricate(db_conn, "ABC123", date(2026, 9, 1))
        result = decl.submit_declaration(db_conn, _input(), today=TODAY)
        assert sorted(result.flags) == sorted([decl.PLATE_MADE_RECENTLY, decl.CUSTOMER_TYPE_MISMATCH])

    def test_pending_signature_is_kept_and_no_pdf_is_recorded(self, db_conn):
        _fabricate(db_conn, "ABC123", date(2026, 9, 1))
        inp = _input(cards=[_card(reason="PÉRDIDA")])
        result = decl.submit_declaration(db_conn, inp, today=TODAY)
        record = db.get_declaration(db_conn, result.declaration_id)
        assert (record.declarant_signature_path, record.notarized_pdf_path) == ("sig.png", None)
        assert _cards_of(db_conn, result.declaration_id)[0].reason == "PÉRDIDA"

    # --- several cards: one flagged card holds the whole declaration ---

    def _three_cards_second_flagged(self, db_conn):
        _fabricate(db_conn, "DEF456", date(2026, 9, 1))   # made recently
        cards = [_card(), _card(plate_number="DEF456", transit_license="L2"),
                 _card(plate_number="GHI789", transit_license="L3")]
        return decl.submit_declaration(db_conn, _input(cards=cards), today=TODAY)

    def test_one_flagged_card_holds_the_whole_declaration_and_creates_nothing(self, db_conn):
        result = self._three_cards_second_flagged(db_conn)
        assert (result.status, result.flags) == ("pending_review", [decl.PLATE_MADE_RECENTLY])
        assert _count(db_conn, "customers") == 0
        assert _count(db_conn, "ownership_cards") == 0
        record = db.get_declaration(db_conn, result.declaration_id)
        assert record.review_flags == [decl.PLATE_MADE_RECENTLY]
        assert record.gestor_id is None
        rows = _cards_of(db_conn, result.declaration_id)
        assert [r.position for r in rows] == [0, 1, 2]
        assert all(r.customer_id is None and r.ownership_card_id is None for r in rows)

    def test_flags_are_stored_per_card_and_declaration_flags_are_the_union(self, db_conn):
        other = _existing_client(db_conn, "PEDRO RUIZ")
        db.create_ownership_card(db_conn, other, transit_license="L3", plate_number="GHI789")
        _fabricate(db_conn, "DEF456", date(2026, 9, 1))
        cards = [_card(), _card(plate_number="DEF456", transit_license="L2"),
                 _card(plate_number="GHI789", transit_license="L3"),
                 _card(plate_number="JKL012", transit_license="L4")]
        result = decl.submit_declaration(db_conn, _input(cards=cards), today=TODAY)
        rows = _cards_of(db_conn, result.declaration_id)
        assert [r.review_flags for r in rows] == [
            [], [decl.PLATE_MADE_RECENTLY], [decl.CARD_OTHER_OWNER], []]
        assert result.flags == [decl.PLATE_MADE_RECENTLY, decl.CARD_OTHER_OWNER]
        record = db.get_declaration(db_conn, result.declaration_id)
        assert record.review_flags == result.flags

    def test_the_same_flag_on_two_cards_appears_once_in_the_union(self, db_conn):
        _fabricate(db_conn, "ABC123", date(2026, 9, 1))
        _fabricate(db_conn, "DEF456", date(2026, 9, 1))
        cards = [_card(), _card(plate_number="DEF456", transit_license="L2")]
        result = decl.submit_declaration(db_conn, _input(cards=cards), today=TODAY)
        assert result.flags == [decl.PLATE_MADE_RECENTLY]

    def test_gestor_declarant_flags_are_recorded_on_every_card(self, db_conn):
        _existing_client(db_conn, "GESTORIA SUR", "CLIENTE")
        cards = [_gcard(), _gcard("DEF456", "L2", owner=_owner(name="LUIS PEREZ"))]
        result = decl.submit_declaration(db_conn, _gestor_input(cards=cards), today=TODAY)
        rows = _cards_of(db_conn, result.declaration_id)
        assert [r.review_flags for r in rows] == [[decl.CUSTOMER_TYPE_MISMATCH]] * 2
        assert _count(db_conn, "customers") == 1

    def test_gestor_owner_with_wrong_type_flags_only_that_card(self, db_conn):
        _existing_client(db_conn, "LUIS PEREZ", "EMPRESA")
        cards = [_gcard(), _gcard("DEF456", "L2", owner=_owner(name="LUIS PEREZ"))]
        result = decl.submit_declaration(db_conn, _gestor_input(cards=cards), today=TODAY)
        rows = _cards_of(db_conn, result.declaration_id)
        assert [r.review_flags for r in rows] == [[], [decl.CUSTOMER_TYPE_MISMATCH]]
        assert _count(db_conn, "customers") == 1   # nothing created, not even the gestor

    def test_same_owner_name_with_two_documents_in_one_submission_is_a_homonym(self, db_conn):
        cards = [_gcard(), _gcard("DEF456", "L2", owner=_owner(document_number="555"))]
        result = decl.submit_declaration(db_conn, _gestor_input(cards=cards), today=TODAY)
        rows = _cards_of(db_conn, result.declaration_id)
        assert [r.review_flags for r in rows] == [[], [decl.CUSTOMER_DOCUMENT_MISMATCH]]
        assert result.status == "pending_review"
        assert _count(db_conn, "customers") == 0

    def test_the_gestor_name_declared_as_a_card_owner_is_a_type_conflict(self, db_conn):
        cards = [_gcard(), _gcard("DEF456", "L2", owner=_gestor(), relation="DUEÑO")]
        result = decl.submit_declaration(db_conn, _gestor_input(cards=cards), today=TODAY)
        rows = _cards_of(db_conn, result.declaration_id)
        assert [r.review_flags for r in rows] == [[], [decl.CUSTOMER_TYPE_MISMATCH]]
        assert _count(db_conn, "customers") == 0


# ----------------------------------------------------------------- review

def _pending(db_conn, inp: DeclarationInput | None = None, recent_plate: str | None = None) -> int:
    """Force a pending declaration for `inp` regardless of conflicts, by
    submitting while a recent fabrication exists for one of its plates."""
    inp = inp or _input()
    _fabricate(db_conn, recent_plate or inp.cards[0].plate_number, date(2026, 10, 1))
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
        (row,) = _cards_of(db_conn, declaration_id)
        owner = db.get_customer(db_conn, row.customer_id)
        assert (owner.name, owner.type, owner.document_relation) == ("MARIA LOPEZ", "CLIENTE", "DUEÑO")
        card = db.get_ownership_card(db_conn, row.ownership_card_id)
        assert (card.customer_id, card.plate_number) == (owner.id, "ABC123")
        assert record.review_flags == ["plate_made_recently"]   # history is kept
        assert row.review_flags == ["plate_made_recently"]

    def test_approves_every_card_with_each_owner_and_the_gestor(self, db_conn):
        luis = _owner(name="LUIS PEREZ", document_number="777")
        cards = [_gcard("ABC123", "L1"), _gcard("DEF456", "L2", owner=luis, relation="POSEEDOR"),
                 _gcard("GHI789", "L3")]
        declaration_id = _pending(db_conn, _gestor_input(cards=cards))
        decl.approve_declaration(db_conn, declaration_id, _reviewer_id(db_conn))
        record = db.get_declaration(db_conn, declaration_id)
        assert record.status == "approved"
        assert db.get_customer(db_conn, record.gestor_id).type == "GESTOR"
        rows = _cards_of(db_conn, declaration_id)
        assert rows[0].customer_id == rows[2].customer_id != rows[1].customer_id
        assert db.get_customer(db_conn, rows[1].customer_id).document_relation == "POSEEDOR"
        assert _count(db_conn, "ownership_cards") == 3
        assert _count(db_conn, "customers") == 3

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
        assert _cards_of(db_conn, declaration_id)[0].customer_id == client_id
        assert record.gestor_id == gestor_id

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

    def test_gestor_name_also_declared_as_owner_raises(self, db_conn):
        cards = [_gcard("ABC123", "L1"), _gcard("DEF456", "L2", owner=_gestor())]
        declaration_id = _pending(db_conn, _gestor_input(cards=cards))
        with pytest.raises(ValueError, match="gestor y como propietario"):
            decl.approve_declaration(db_conn, declaration_id, _reviewer_id(db_conn))
        assert _count(db_conn, "customers") == 0

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
        (row,) = _cards_of(db_conn, declaration_id)
        decl.approve_declaration(db_conn, declaration_id, _reviewer_id(db_conn),
                                 reassign_card_ids=[row.id])
        (row,) = _cards_of(db_conn, declaration_id)
        assert row.ownership_card_id == card_id
        card = db.get_ownership_card(db_conn, card_id)
        assert card.customer_id == row.customer_id != other
        assert card.engine_number == "KEEP"

    def test_reassign_only_moves_the_selected_cards(self, db_conn):
        other = _existing_client(db_conn, "PEDRO RUIZ")
        db.create_ownership_card(db_conn, other, transit_license="L1", plate_number="ABC123")
        db.create_ownership_card(db_conn, other, transit_license="L2", plate_number="DEF456")
        cards = [_card(plate_number="ABC123", transit_license="L1"),
                 _card(plate_number="DEF456", transit_license="L2")]
        declaration_id = _pending(db_conn, _input(cards=cards))
        first, second = _cards_of(db_conn, declaration_id)
        with pytest.raises(ValueError, match="DEF456"):
            decl.approve_declaration(db_conn, declaration_id, _reviewer_id(db_conn),
                                     reassign_card_ids=[first.id])
        decl.approve_declaration(db_conn, declaration_id, _reviewer_id(db_conn),
                                 reassign_card_ids={first.id, second.id})
        for row in _cards_of(db_conn, declaration_id):
            assert db.get_ownership_card(db_conn, row.ownership_card_id).customer_id == row.customer_id
            assert row.customer_id != other

    def test_approval_is_all_or_nothing(self, db_conn):
        """A blocker on the SECOND card must leave the first one (and the
        customers) untouched: every check happens before the first write."""
        other = _existing_client(db_conn, "PEDRO RUIZ")
        db.create_ownership_card(db_conn, other, transit_license="L2", plate_number="DEF456")
        cards = [_gcard("ABC123", "L1", owner=_owner(name="LUIS PEREZ")),
                 _gcard("DEF456", "L2", owner=_owner(name="ANA GOMEZ"))]
        declaration_id = _pending(db_conn, _gestor_input(cards=cards))
        customers_before = _count(db_conn, "customers")
        with pytest.raises(ValueError, match="tarjeta 2 \\(placa DEF456\\)"):
            decl.approve_declaration(db_conn, declaration_id, _reviewer_id(db_conn))
        assert _count(db_conn, "customers") == customers_before
        assert _count(db_conn, "ownership_cards") == 1
        assert db.get_declaration(db_conn, declaration_id).status == "pending_review"
        assert all(r.customer_id is None for r in _cards_of(db_conn, declaration_id))

    def test_unknown_reassign_id_raises_before_writing(self, db_conn):
        declaration_id = _pending(db_conn)
        with pytest.raises(ValueError, match="no pertenece"):
            decl.approve_declaration(db_conn, declaration_id, _reviewer_id(db_conn),
                                     reassign_card_ids=[999999])
        assert _count(db_conn, "customers") == 0

    def test_reassign_id_of_another_declaration_is_refused(self, db_conn):
        first = _pending(db_conn)
        second = _pending(db_conn, _input(cards=[_card(plate_number="DEF456", transit_license="L2")]))
        foreign = _cards_of(db_conn, second)[0].id
        with pytest.raises(ValueError, match="no pertenece"):
            decl.approve_declaration(db_conn, first, _reviewer_id(db_conn), reassign_card_ids=[foreign])

    def test_data_mismatch_raises_even_with_reassign(self, db_conn):
        client = _existing_client(db_conn)
        db.create_ownership_card(db_conn, client, transit_license="LIC-001", plate_number="ZZZ999")
        declaration_id = _pending(db_conn)
        (row,) = _cards_of(db_conn, declaration_id)
        with pytest.raises(ValueError, match="no coinciden"):
            decl.approve_declaration(db_conn, declaration_id, _reviewer_id(db_conn),
                                     reassign_card_ids=[row.id])

    def test_licence_and_plate_on_two_different_cards_raises(self, db_conn):
        client = _existing_client(db_conn)
        db.create_ownership_card(db_conn, client, transit_license="LIC-001", plate_number="AAA111")
        db.create_ownership_card(db_conn, client, transit_license="LIC-002", plate_number="ABC123")
        declaration_id = _pending(db_conn)
        (row,) = _cards_of(db_conn, declaration_id)
        with pytest.raises(ValueError, match="no coinciden"):
            decl.approve_declaration(db_conn, declaration_id, _reviewer_id(db_conn),
                                     reassign_card_ids=[row.id])

    def test_same_card_is_linked(self, db_conn):
        client = _existing_client(db_conn)
        card_id = db.create_ownership_card(db_conn, client, transit_license="LIC-001", plate_number="ABC123")
        declaration_id = _pending(db_conn)
        decl.approve_declaration(db_conn, declaration_id, _reviewer_id(db_conn))
        assert _cards_of(db_conn, declaration_id)[0].ownership_card_id == card_id
        assert _count(db_conn, "ownership_cards") == 1

    def test_only_from_pending_review(self, db_conn):
        approved = decl.submit_declaration(db_conn, _input(), today=TODAY).declaration_id
        with pytest.raises(ValueError, match="pendiente"):
            decl.approve_declaration(db_conn, approved, _reviewer_id(db_conn))
        rejected = _pending(db_conn, _input(cards=[_card(plate_number="DEF456", transit_license="L2")]))
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
        assert record.gestor_id is None
        assert all(r.customer_id is None and r.ownership_card_id is None
                   for r in _cards_of(db_conn, declaration_id))
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
